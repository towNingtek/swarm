"""Runs customer site schedules inside the customer's own DSH container.

Every tick the platform reads each provisioned site's office (registry cron +
project.yaml reports, exactly the classic swarm pairing) and starts every
``ready`` schedule whose cron matches the current Asia/Taipei minute. A run is

    docker exec -i -w /home/dsh/workspace oc-<site> timeout <s> \
        dsh --profile headless --patch <site web profile patch> -

with the prompt on stdin. The site's own default model is used (the platform
starter model, metered by the relay, until the customer connects their own).
No key, token or platform credential is passed; nothing leaves the container
except the final answer, which is stored as the run log for that customer.

Semantics: standard cron numbering (day of week 0/7 = Sunday, 1 = Monday);
day-of-month and day-of-week are ANDed; a minute that passed while the platform was down is not
caught up. One run at a time per site, a small global cap, and each (site,
hive, schedule, minute) runs at most once.

Optional delivery (``delivery=``, a ``support_site_line.ScheduleDelivery``;
on only with ``PLATFORM_SITE_LINE=1``): after a run the report's own
``line_push`` / ``hackmd`` settings decide whether the cleaned answer is pushed
to LINE or published to HackMD, and a ``[LINE：…]`` / ``[HackMD：…]`` note is
added to the run log. Scheduled runs deliver; a manual run delivers only when
started with ``deliver=True`` (``run_now(..., deliver=False)`` never sends).
"""
from __future__ import annotations

import re
import secrets
import subprocess
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import _site_path  # noqa: F401  (puts site/ on sys.path)
import common as sitectl
from support_site_office import OfficeError, _ID, read_office

TZ = ZoneInfo('Asia/Taipei')
WORKSPACE = '/home/dsh/workspace'
PROFILE_PATCH = '/home/dsh/dsh-home/profiles/web/cordis.patch.yml'
RUN_TIMEOUT = 900
MAX_OUTPUT = 6000
MAX_GLOBAL = 2
MAX_MANUAL_PER_DAY = 20
QUOTA_CHECK_SECONDS = 3600     # LINE monthly quota, read hourly (sends nothing)
_DOW = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
_SITE = re.compile(r'^[a-z0-9][a-z0-9-]{0,62}$')


# -- cron --------------------------------------------------------------------

def _values(field, low, high, names=None):
    out = set()
    for piece in field.split(','):
        base, _, step = piece.partition('/')
        step = int(step) if step else 1
        if base == '*':
            start, end = low, high
        else:
            ends = [names.index(v) if names and v in names else int(v) for v in base.split('-')]
            start, end = ends[0], ends[-1]
            if step > 1 and len(ends) == 1:
                end = high
        out.update(range(start, end + 1, step))
    return out


# Day of week in standard cron numbering: 0 and 7 = Sunday, 1 = Monday ... 6 = Saturday
# (names mon..sun as usual). The classic scheduler read numbers the APScheduler way
# (0 = Monday), so "0 9 * * 1" written as "Monday 09:00" ran on Tuesday.
_CRON_DOW = ('sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')


def _dow_values(field):
    """Day-of-week field -> set of datetime.weekday() values (Monday = 0)."""
    days = _values(field.lower(), 0, 7, _CRON_DOW)
    return {(d - 1) % 7 for d in days}


def cron_matches(expr, moment):
    """Standard cron (fields ANDed; day of week 0/7 = Sunday, 1 = Monday)."""
    minute, hour, dom, month, dow = expr.split()
    return (moment.minute in _values(minute, 0, 59)
            and moment.hour in _values(hour, 0, 23)
            and moment.day in _values(dom, 1, 31)
            and moment.month in _values(month, 1, 12)
            and moment.weekday() in _dow_values(dow))


def build_prompt(hive_id, hive_name, role, name, skill, context):
    working = f'hives/{hive_id}/agents/{role}/WORKING.md'
    prompt = (
        f'你是 {role}，隸屬於「{hive_name}」（hive: {hive_id}）。這是排程「{name}」自動觸發的工作。\n\n'
        '## 你的任務\n執行本次觸發的工作，並產出實際成果；具體內容見下方「觸發上下文」。\n\n'
        '## 開始前的準備（不是任務本身）\n快速讀完以下檔案取得脈絡，不要逐一回報讀取進度：\n'
        '- INITIAL.md（共用守則）\n'
        f'- hives/{hive_id}/HIVE.md（這間公司的規則）\n'
        f'- .swarm/agents/{role}/SOUL.md（角色身份，若存在）\n'
        f'- hives/{hive_id}/agents/{role}/SOUL.md（這間公司裡的操作範圍，若存在）\n'
        f'- {working}（上次進度；不存在就建立）\n\n'
        '讀檔只是準備工作，讀完檔案不等於任務完成。\n\n'
        f'重要：只操作「{hive_id}」這間公司的資料，不要跨公司讀寫；不要讀取或輸出任何 .keys/ 內的金鑰。\n'
        '沒有人會即時回答你的問題：資訊不足時，用合理假設完成，並在結果裡註明假設。\n'
        '回覆一律使用繁體中文。')
    if skill:
        prompt += (f'\n\n--- Skill ---\n請先讀 .dsh/skills/{skill}/SKILL.md 並依照它執行。')
    if context:
        prompt += f'\n\n--- 觸發上下文 ---\n{context}'
    prompt += (
        '\n\n--- 結束前自我檢查 ---\n'
        '1. 把本次的成果完整寫在你最後一則回覆的正文裡（這段文字就是給使用者看的執行結果）。\n'
        f'2. 更新 {working}：記下這次做了什麼、下次要接著做什麼。\n'
        '兩項都完成才結束。')
    return prompt


class SiteScheduler:
    def __init__(self, core, office, *, runner=None, clock=time.time, library=None, models=None,
                 delivery=None):
        # models: [(provider, model)] for unattended runs (SCHEDULE_MODELS); empty
        # = the site's own default model, as before. See support_schedule_answer.
        self.models = list(models or [])
        self.core = core
        self.office = office
        self.library = library
        self.clock = clock
        self.runner = runner or self._chain_run
        # Optional ScheduleDelivery (LINE / HackMD). None = runs are only logged.
        self.delivery = delivery
        self._last_quota_check = None
        self._last_purge_day = None
        self._lock = threading.Lock()
        self._busy = set()          # site names with a run in flight
        self._last_minute = None
        # Optional SiteStatusWriter: refreshed on ticks and right after a run.
        self.status = None
        with core._connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS support_schedule_runs (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                hive TEXT NOT NULL, name TEXT NOT NULL, slot TEXT NOT NULL,
                trigger TEXT NOT NULL CHECK(trigger IN ('schedule','manual')),
                status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','skipped')),
                started_at INTEGER NOT NULL, finished_at INTEGER, output TEXT,
                UNIQUE(tenant_id, hive, name, slot))''')
            conn.execute('CREATE INDEX IF NOT EXISTS support_schedule_runs_tenant '
                         'ON support_schedule_runs(tenant_id, started_at)')
            # A restart loses in-flight subprocesses; say so instead of leaving
            # them "running" forever.
            conn.execute("UPDATE support_schedule_runs SET status='failed', finished_at=?, "
                         "output='平台重新啟動，這次執行中斷。' WHERE status='running'",
                         (int(clock()),))

    # -- sites ------------------------------------------------------------

    def sites(self):
        """tenant_id -> site host, for provisioned sites only."""
        with self.core._connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='support_site_jobs'").fetchone():
                return {}
            return {r['tenant_id']: r['site_host'] for r in conn.execute(
                "SELECT j.tenant_id, j.site_host FROM support_site_jobs j JOIN tenants t "
                "ON t.id=j.tenant_id WHERE j.status='succeeded_provisioned' AND t.enabled=1 "
                "AND t.site_host=j.site_host")}

    def _site_name(self, host):
        suffix = '.' + self.office.domain
        if not isinstance(host, str) or not host.endswith(suffix):
            return None
        name = host[:-len(suffix)]
        return name if _SITE.match(name) else None

    # -- tick -------------------------------------------------------------

    def _slots(self):
        """Minutes to check this tick: every minute since the last tick, capped
        at three so a long pause never triggers a burst of old work."""
        now = datetime.fromtimestamp(self.clock(), TZ).replace(second=0, microsecond=0)
        last = self._last_minute
        self._last_minute = now
        if last is None or now <= last:
            return [now] if last is None else []
        slots, cursor = [], max(last + timedelta(minutes=1), now - timedelta(minutes=2))
        while cursor <= now:
            slots.append(cursor)
            cursor += timedelta(minutes=1)
        return slots

    def _delivery_upkeep(self):
        """Hourly LINE quota reading (sends nothing) and a daily purge of the
        30-day delivery log. Only when delivery is enabled; never raises."""
        if self.delivery is None:
            return
        now = self.clock()
        day = datetime.fromtimestamp(now, TZ).strftime('%Y-%m-%d')
        if self._last_purge_day != day:
            self._last_purge_day = day
            try:
                self.delivery.purge()
            except Exception as exc:
                print(f'[scheduler] delivery purge: {type(exc).__name__}', flush=True)
        if self._last_quota_check is not None and now - self._last_quota_check < QUOTA_CHECK_SECONDS:
            return
        self._last_quota_check = now
        try:
            sites = []
            for host in self.sites().values():
                name = self._site_name(host)
                office = self.office.for_host(host) if name else None
                if office:
                    sites.append((name, [h['id'] for h in office.get('hives', []) if h['exists']]))
            self.delivery.check_quotas(sites)
        except Exception as exc:
            print(f'[scheduler] quota check: {type(exc).__name__}', flush=True)

    def tick(self):
        self._delivery_upkeep()
        if self.library is not None:
            try:
                self.library.apply_pending(self.sites())
            except Exception as exc:  # delivery problems never stop schedules
                print(f'[scheduler] deliveries: {type(exc).__name__}', flush=True)
        if self.status is not None:
            try:
                self.status.refresh()
            except Exception as exc:  # the status file is an aid, never a blocker
                print(f'[scheduler] status: {type(exc).__name__}', flush=True)
        slots = self._slots()
        if not slots:
            return []
        started = []
        for tenant_id, host in self.sites().items():
            try:
                started.extend(self._tick_site(tenant_id, host, slots))
            except Exception as exc:  # one site never stops the others
                print(f'[scheduler] {host}: {type(exc).__name__}', flush=True)
        return started

    def _tick_site(self, tenant_id, host, slots):
        started = []
        name = self._site_name(host)
        if name is None:
            return started
        office = self.office.for_host(host)
        for hive in (office or {}).get('hives', []):
            if not hive['enabled'] or not hive['exists']:
                continue
            for job in hive['schedules']:
                if job['state'] != 'ready':
                    continue
                for slot in slots:
                    try:
                        due = cron_matches(job['cron'], slot)
                    except (ValueError, IndexError):
                        due = False
                    if due:
                        run = self._start(tenant_id, name, hive, job,
                                          slot.strftime('%Y-%m-%dT%H:%M'), 'schedule')
                        if run:
                            started.append(run)
        return started

    def run_now(self, tenant_id, host, hive_id, schedule_name, *, deliver=True):
        """Customer-initiated single run of one of their schedules.

        ``deliver=False`` is a dry run: nothing is pushed to LINE, relayed or
        published, whatever the report says. With delivery off (no
        ``PLATFORM_SITE_LINE``) the flag changes nothing."""
        name = self._site_name(host)
        office = self.office.for_host(host) if name else None
        hive = next((h for h in (office or {}).get('hives', [])
                     if h['id'] == hive_id and h['exists']), None)
        if hive is None:
            raise OfficeError('找不到這間公司')
        job = next((j for j in hive['schedules'] if j['name'] == schedule_name), None)
        if job is None or job['state'] == 'missing_definition' or not job['role']:
            raise OfficeError('這個排程還不完整，無法執行')
        since = int(self.clock()) - 86400
        with self.core._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM support_schedule_runs WHERE tenant_id=? "
                                 "AND trigger='manual' AND started_at>?",
                                 (tenant_id, since)).fetchone()[0]
        if count >= MAX_MANUAL_PER_DAY:
            raise OfficeError(f'今天手動執行已達 {MAX_MANUAL_PER_DAY} 次上限')
        run = self._start(tenant_id, name, hive, job, 'manual-' + secrets.token_hex(6), 'manual',
                          deliver=deliver is True)
        if run is None:
            raise OfficeError('這個站台已經有一個排程在執行，請等它結束')
        return run

    def _start(self, tenant_id, site, hive, job, slot, trigger, deliver=True):
        with self._lock:
            if site in self._busy or len(self._busy) >= MAX_GLOBAL:
                busy = True
            else:
                busy = False
                self._busy.add(site)
        run_id = 'r-' + secrets.token_hex(8)
        now = int(self.clock())
        with self.core._connect() as conn:
            if conn.execute('SELECT 1 FROM support_schedule_runs WHERE tenant_id=? AND hive=? '
                            'AND name=? AND slot=?',
                            (tenant_id, hive['id'], job['name'], slot)).fetchone():
                if not busy:
                    with self._lock:
                        self._busy.discard(site)
                return None
            if busy:
                if trigger == 'manual':
                    return None
                conn.execute('INSERT INTO support_schedule_runs VALUES (?,?,?,?,?,?,?,?,?,?)',
                             (run_id, tenant_id, hive['id'], job['name'], slot, trigger,
                              'skipped', now, now, '上一個排程還在執行，這次跳過。'))
                return None
            conn.execute('INSERT INTO support_schedule_runs VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (run_id, tenant_id, hive['id'], job['name'], slot, trigger,
                          'running', now, None, None))
        prompt = build_prompt(hive['id'], hive['name'], job['role'], job['name'],
                              job['skill'], job['context'])
        meta = {'tenant_id': tenant_id, 'hive_id': hive['id'], 'report_name': job['name'],
                'deliver': deliver}
        thread = threading.Thread(target=self._run, args=(run_id, site, prompt, meta), daemon=True)
        thread.start()
        return {'id': run_id, 'hive': hive['id'], 'name': job['name'], 'trigger': trigger}

    def _run(self, run_id, site, prompt, meta=None):
        status, output = 'failed', ''
        try:
            ok, output = self.runner(site, prompt)
            status = 'succeeded' if ok else 'failed'
            if self.delivery is not None and meta is not None:
                # Only after the run itself finished; the hook never raises and
                # never changes the run's status, it only appends a note.
                output = (output or '') + self.delivery.after_run(
                    site=site, run_id=run_id, ok=bool(ok), output=output or '', **meta)
        except Exception as exc:
            output = f'執行器錯誤：{type(exc).__name__}'
        finally:
            with self._lock:
                self._busy.discard(site)
            with self.core._connect() as conn:
                conn.execute("UPDATE support_schedule_runs SET status=?, finished_at=?, output=? "
                             "WHERE id=?", (status, int(self.clock()),
                                            (output or '')[-MAX_OUTPUT:], run_id))
            if self.status is not None:
                self.status._last = 0.0   # next tick rewrites the site's status

    def _chain_run(self, site, prompt):
        """Try each scheduled-run model in order (or the site default); keep only the
        deliverable. The first model whose cleaned answer is usable wins."""
        from support_schedule_answer import deliverable
        tried, last = [], (False, '沒有可用的模型')
        for model in (self.models or [None]):
            ok, output = self._docker_run(site, prompt, model=model)
            label = '/'.join(model) if model else '站台預設模型'
            tried.append(label)
            if ok:
                answer, why = deliverable(output)
                if not why:
                    return True, f'[model={label}] [tried={",".join(tried)}]\n{answer}'
                ok, output = False, f'{label}：{why}'
            last = (False, output)
        return False, f'全部模型都失敗（{"、".join(tried)}）。\n{last[1]}'

    @staticmethod
    def _docker_run(site, prompt, model=None):
        if not _SITE.match(site):
            return False, '站台名稱不正確'
        container = sitectl.container_name(site)
        command = ['docker', 'exec', '-i', '-w', WORKSPACE, container,
                   'timeout', str(RUN_TIMEOUT), 'dsh', '--profile', 'headless',
                   '--patch', PROFILE_PATCH]
        if model is not None:
            # Scheduled-run model as an extra patch layer inside the container; the
            # site's own profile (its chat default) is not changed.
            from support_schedule_answer import model_overlay
            overlay = '/tmp/swarm-schedule-model-%s.yml' % secrets.token_hex(4)
            text = model_overlay(*model)
            put = subprocess.run(['docker', 'exec', '-i', container, 'sh', '-c',
                                  'umask 077; cat > "$1"', 'sh', overlay],
                                 input=text, capture_output=True, text=True, timeout=30)
            if put.returncode != 0:
                return False, '無法設定排程模型'
            command += ['--patch', overlay]
        command.append('-')
        try:
            done = subprocess.run(command, input=prompt, capture_output=True, text=True,
                                  timeout=RUN_TIMEOUT + 60)
        except subprocess.TimeoutExpired:
            return False, f'超過 {RUN_TIMEOUT // 60} 分鐘沒有完成，已停止。'
        finally:
            if model is not None:
                subprocess.run(['docker', 'exec', container, 'rm', '-f', overlay],
                               capture_output=True, timeout=30)
        answer = (done.stdout or '').strip()
        if done.returncode == 0 and answer:
            return True, answer
        if done.returncode == 124:
            return False, f'超過 {RUN_TIMEOUT // 60} 分鐘沒有完成，已停止。'
        # stderr is diagnostics from the customer's own container; keep a short
        # tail so the customer can see why (e.g. model quota exhausted).
        tail = (done.stderr or '').strip().splitlines()[-6:]
        return False, (answer + '\n' if answer else '') + '執行失敗（代碼 %d）。\n%s' % (
            done.returncode, '\n'.join(tail)[-1500:])

    # -- views ------------------------------------------------------------

    def runs_for(self, tenant_id, limit=30):
        with self.core._connect() as conn:
            rows = conn.execute('SELECT * FROM support_schedule_runs WHERE tenant_id=? '
                                'ORDER BY started_at DESC, id LIMIT ?',
                                (tenant_id, limit)).fetchall()
        return [{'id': r['id'], 'hive': r['hive'], 'name': r['name'], 'trigger': r['trigger'],
                 'status': r['status'], 'started_at': r['started_at'],
                 'finished_at': r['finished_at'], 'output': r['output'] or ''} for r in rows]


def loop(scheduler, stop, interval=20.0):
    import traceback

    while not stop.wait(interval):
        try:
            scheduler.tick()
        except Exception:
            print('[scheduler] tick failed:', flush=True)
            traceback.print_exc()
