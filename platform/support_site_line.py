"""Optional LINE push (and HackMD publish) after a site's scheduled run.

Off unless the operator sets ``PLATFORM_SITE_LINE=1`` (see platform_service.py).
Without it the scheduler behaves exactly as before.

Where things live:

* LINE tokens, secrets and recipients stay in the SITE. The platform pipes
  ``site_scripts/line_agent.js`` into the site's own container; the script
  reads the token file the site names (under ``.swarm/keys/`` in the site's
  workspace) and prints only a status. The platform never reads a token.
* A report opts in on the site's own ``hives/<hive>/project.yaml``::

    line:
      push:
        - {name: bot-a, token_file: shared/line-bot-a.token, to: U0123...}   # 33 chars
        - {name: bot-b, relay: {site: beta, channel: bot-b}}
    notify: {channel_id: "12345678", token_file: shared/discord.token}      # optional
    reports:
      daily_digest:
        line_push: true          # push the cleaned answer after a successful run
        relay: true              # default; false = skip relay targets for this report
        hackmd: {repo: bd-agent, token_file: shared/hackmd.token, owner: Acme}  # optional

* A RELAY sends one site's result through another site's LINE bot. The
  destination sends it with its own token, and both sides must consent in
  their own files:

    source  workspace/hives/<hive>/project.yaml
            line.push: [{name: bot-b, relay: {site: beta, channel: bot-b}}]
    dest    workspace/.swarm/line-channels.yaml (in site "beta")
            channels: {bot-b: {token_file, to, accept_from: [{site: acme}]}}

  A relay is only ever started by the platform scheduler after a run; there
  is no endpoint a site or its AI could call to request one.
* The platform database keeps a 30-day delivery log (content and status) and
  the LINE monthly quota per bot. Alert thresholds per bot come from an
  optional ``line-quota.yaml`` next to the database.

Rules: 1600-char cap, an answer with fewer than 40 characters after cleaning is
not pushed, 15 s per LINE call, no retry, no daily limit. Failures alert; quota
alerts at 80 % / 95 % by default; a month with no quota left skips further
sends to that bot; more than 30 sends to one bot within an hour alerts without
blocking.
"""
from __future__ import annotations

import calendar
import json
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

import _site_path  # noqa: F401  (puts site/ on sys.path)
import common as sitectl
from support_site_office import _read, _yaml

TZ = ZoneInfo('Asia/Taipei')
SCRIPTS = Path(__file__).resolve().parent / 'site_scripts'
LINE_SCRIPT = SCRIPTS / 'line_agent.js'
HACKMD_SCRIPT = SCRIPTS / 'bd_hackmd.js'
WORKSPACE = '/home/dsh/workspace'          # the office inside every site container
KEYS_DIR = WORKSPACE + '/.swarm/keys'


def keys_dir(sites_root, site):
    """The office's key folder inside the container: under the office sub-folder
    named by the platform's site.yaml (see support_site_office.office_dir)."""
    from support_site_office import office_dir
    sub = office_dir(Path(sites_root) / site)
    return f'{WORKSPACE}/{sub}/.swarm/keys' if sub else KEYS_DIR
EXEC_TIMEOUT = 40                          # docker exec around one 15 s provider call
HACKMD_TIMEOUT = 90
RETENTION_DAYS = 30
BURST_PER_HOUR = 30
QUOTA_STEPS = (80, 95, 100)
MIN_DELIVERABLE_CHARS = 40
LINE_SOFT_LIMIT = 1600
_ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,62}$')
_SITE = re.compile(r'^[a-z0-9][a-z0-9-]{0,62}$')
_LINE_TO = re.compile(r'^[UCR][0-9a-f]{32}$')
_KEYFILE = re.compile(r'^[A-Za-z0-9._/-]{1,120}$')
_CHANNEL = re.compile(r'^[0-9]{5,25}$')
_RELPATH = re.compile(r'^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$')


def _keyfile(value):
    if (isinstance(value, str) and _KEYFILE.match(value) and not value.startswith('/')
            and '..' not in value.split('/')):
        return value
    return None


def mask_to(to):
    return (to[:5] + '…') if isinstance(to, str) else None


def truncate_for_line(text):
    """Cap at 1600 characters with a notice (LINE itself allows 5000)."""
    if len(text) > LINE_SOFT_LIMIT:
        return text[:LINE_SOFT_LIMIT].rstrip() + '\n\n…（訊息過長已截斷，完整內容見 repo）'
    return text[:5000]


# -- site configuration (read only) ------------------------------------------

def load_project(workspace, hive_id):
    """hives/<hive>/project.yaml of a site workspace (symlink-safe), or {}."""
    if not _ID.match(hive_id or ''):
        return {}
    data = _yaml(_read(Path(workspace), 'hives', hive_id, 'project.yaml'))
    return data if isinstance(data, dict) else {}


def hackmd_config(value):
    """reports.<task>.hackmd: {repo, token_file, owner}.

    ``repo`` is relative to the site workspace, ``token_file`` to
    ``.swarm/keys``; both are re-checked inside the container."""
    if not isinstance(value, dict):
        return None
    repo, token, owner = value.get('repo'), value.get('token_file'), value.get('owner')
    if not all(isinstance(v, str) and _RELPATH.match(v) and '..' not in v.split('/')
               for v in (repo, token)):
        return None
    if not (isinstance(owner, str) and 0 < len(owner) <= 40):
        return None
    return {'repo': repo, 'token_file': token, 'owner': owner}


def report_config(project, report_name):
    """Delivery settings of one report, or None when it is not defined."""
    reports = project.get('reports') if isinstance(project.get('reports'), dict) else {}
    rdef = reports.get(report_name)
    if not isinstance(rdef, dict):
        return None
    return {'line_push': rdef.get('line_push') is True,
            'relay': rdef.get('relay', True) is not False,
            'hackmd': hackmd_config(rdef.get('hackmd'))}


def line_config(project):
    """project.yaml ``line.push`` -> normalized push and relay targets."""
    line = project.get('line') if isinstance(project.get('line'), dict) else {}
    targets = []
    for item in line.get('push') or [] if isinstance(line.get('push'), list) else []:
        if not isinstance(item, dict) or not _ID.match(str(item.get('name') or '')):
            continue
        relay = item.get('relay')
        if isinstance(relay, dict):
            site, channel = relay.get('site'), relay.get('channel')
            if isinstance(site, str) and _SITE.match(site) and isinstance(channel, str) and _ID.match(channel):
                targets.append({'name': item['name'], 'relay': {'site': site, 'channel': channel}})
            continue
        token, to = _keyfile(item.get('token_file')), item.get('to')
        if token and isinstance(to, str) and _LINE_TO.match(to):
            targets.append({'name': item['name'], 'token_file': token, 'to': to})
    return {'targets': targets}


def notify_config(project):
    """project.yaml ``notify:`` -> the site's own Discord alert channel, or None."""
    notify = project.get('notify') if isinstance(project.get('notify'), dict) else {}
    channel = str(notify.get('channel_id') or '')
    token = _keyfile(notify.get('token_file'))
    if _CHANNEL.match(channel) and token:
        return {'channel_id': channel, 'token_file': token}
    return None


def relay_channel(dest_workspace, channel, source_site):
    """Destination's own consent file. None unless it accepts source_site."""
    data = _yaml(_read(Path(dest_workspace), '.swarm', 'line-channels.yaml'))
    channels = data.get('channels') if isinstance(data, dict) else None
    entry = channels.get(channel) if isinstance(channels, dict) else None
    if not isinstance(entry, dict):
        return None
    accept = entry.get('accept_from') if isinstance(entry.get('accept_from'), list) else []
    accepted = {a.get('site') for a in accept if isinstance(a, dict)}
    token, to = _keyfile(entry.get('token_file')), entry.get('to')
    if source_site not in accepted or not token or not (isinstance(to, str) and _LINE_TO.match(to)):
        return None
    return {'token_file': token, 'to': to}


# -- running an in-container script ------------------------------------------

def run_site_script(site, script_path, request, timeout=EXEC_TIMEOUT):
    """Run a site_scripts/*.js in the site's container with ``const __REQ``
    prepended (the request travels on stdin, never on a command line).
    Returns the last JSON line it printed."""
    if not _SITE.match(site or ''):
        return {'ok': False, 'error': 'bad site'}
    program = 'const __REQ = ' + json.dumps(request, ensure_ascii=False) + ';\n' + \
        Path(script_path).read_text(encoding='utf-8')
    try:
        done = subprocess.run(['docker', 'exec', '-i', sitectl.container_name(site), 'node', '-'],
                              input=program, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'timeout'}
    except OSError:
        return {'ok': False, 'error': 'docker unavailable'}
    for line in reversed((done.stdout or '').strip().splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get('ok'), bool):
            return value
    if done.returncode != 0:
        return {'ok': False, 'error': 'container unavailable'}
    return {'ok': False, 'error': 'no status'}


def docker_agent(site, request):
    """Run line_agent.js in the site; return only its status fields."""
    value = run_site_script(site, LINE_SCRIPT, request)
    return {k: value[k] for k in ('ok', 'error', 'limit', 'used', 'bot') if k in value}


# -- delivery ----------------------------------------------------------------

class LineDelivery:
    """Pushes, relays, quota tracking and the 30-day delivery log."""

    def __init__(self, core, sites_root, *, agent=None, alerts=None, clock=time.time,
                 quota_file=None):
        self.core = core
        self.sites_root = Path(sites_root)
        # Platform-owned per-bot alert rules (line-quota.yaml next to the DB).
        self.quota_file = quota_file
        self.agent = agent or docker_agent
        self.alerts = alerts            # SiteAlerts or None
        self.clock = clock
        self._lock = threading.Lock()
        with core._connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS support_line_deliveries (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                source_site TEXT NOT NULL, dest_site TEXT NOT NULL,
                run_id TEXT, kind TEXT NOT NULL CHECK(kind IN ('push','relay','reply')),
                target TEXT NOT NULL, status TEXT NOT NULL
                    CHECK(status IN ('sent','failed','skipped')),
                reason TEXT, content TEXT, created_at INTEGER NOT NULL)''')
            conn.execute('CREATE INDEX IF NOT EXISTS support_line_deliveries_time '
                         'ON support_line_deliveries(created_at)')
            conn.execute('CREATE INDEX IF NOT EXISTS support_line_deliveries_target '
                         'ON support_line_deliveries(dest_site, target, created_at)')
            conn.execute('''CREATE TABLE IF NOT EXISTS support_line_quota (
                site TEXT NOT NULL, target TEXT NOT NULL, month TEXT NOT NULL,
                lim INTEGER, used INTEGER, alerted INTEGER NOT NULL DEFAULT 0,
                exhausted INTEGER NOT NULL DEFAULT 0, checked_at INTEGER NOT NULL,
                PRIMARY KEY(site, target, month))''')
            conn.execute('''CREATE TABLE IF NOT EXISTS support_line_quota_alerts (
                site TEXT NOT NULL, target TEXT NOT NULL, month TEXT NOT NULL,
                kind TEXT NOT NULL, at INTEGER NOT NULL,
                PRIMARY KEY(site, target, month, kind))''')

    def _month(self):
        return datetime.fromtimestamp(self.clock(), TZ).strftime('%Y-%m')

    def _log(self, tenant_id, source, dest, run_id, kind, target, status, reason, content):
        with self.core._connect() as conn:
            conn.execute('INSERT INTO support_line_deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         ('d-' + secrets.token_hex(8), tenant_id, source, dest, run_id, kind,
                          target, status, (reason or '')[:200] or None,
                          content if kind != 'reply' else None, int(self.clock())))

    # quota ------------------------------------------------------------------

    def _exhausted(self, site, target):
        with self.core._connect() as conn:
            row = conn.execute('SELECT exhausted FROM support_line_quota WHERE site=? AND target=? '
                               'AND month=?', (site, target, self._month())).fetchone()
        return bool(row and row['exhausted'])

    def _mark_exhausted(self, site, target):
        now = int(self.clock())
        with self.core._connect() as conn:
            conn.execute('INSERT INTO support_line_quota(site,target,month,checked_at,exhausted,alerted) '
                         'VALUES (?,?,?,?,1,100) ON CONFLICT(site,target,month) DO UPDATE SET '
                         'exhausted=1, checked_at=excluded.checked_at', (site, target, self._month(), now))

    def quota_rules(self, target):
        """Per-bot alert rules from line-quota.yaml (or the defaults).

        ``target`` is the bot label: ``bot-a`` for a site's own bot or
        ``beta/bot-b`` for a relay channel. File format::

            bots:
              beta/bot-b: {steps: [50, 70, 80, 95], remaining_below: 30, projected_over: 90}
        """
        rules = {'steps': list(QUOTA_STEPS[:-1]), 'remaining_below': None, 'projected_over': None}
        path = self.quota_file
        if path is not None:
            try:
                data = yaml.safe_load(Path(path).read_text(encoding='utf-8')) if Path(path).is_file() else None
            except (OSError, yaml.YAMLError):
                data = None
            bots = (data or {}).get('bots') if isinstance(data, dict) else None
            bot = bots.get(target) if isinstance(bots, dict) else None
            if isinstance(bot, dict):
                steps = [s for s in bot.get('steps') or [] if isinstance(s, int) and 0 < s < 100]
                if steps:
                    rules['steps'] = sorted(set(steps))
                for key in ('remaining_below', 'projected_over'):
                    if isinstance(bot.get(key), int) and bot[key] > 0:
                        rules[key] = bot[key]
        return rules

    def _once(self, conn, site, target, month, kind):
        """True the first time (site, target, month, kind) is seen."""
        return conn.execute('INSERT OR IGNORE INTO support_line_quota_alerts VALUES (?,?,?,?,?)',
                            (site, target, month, kind, int(self.clock()))).rowcount == 1

    def check_quota(self, site, target, token_file):
        """Read LINE's real monthly limit/usage in the site and alert.

        Alerts (each at most once per bot per month): the per-bot percentage
        steps (default 80/95), ``remaining_below`` messages left, a month-end
        projection above ``projected_over`` %, and 100 % (sending stops).
        """
        res = self.agent(site, {'op': 'quota', 'keys_dir': keys_dir(self.sites_root, site), 'token_file': token_file})
        if not res.get('ok'):
            return res
        limit, used = res.get('limit'), res.get('used')
        month, now = self._month(), int(self.clock())
        rules = self.quota_rules(target)
        known = isinstance(limit, int) and limit > 0 and isinstance(used, int)
        pct = int(used * 100 / limit) if known else 0
        notes = []
        with self.core._connect() as conn:
            row = conn.execute('SELECT alerted FROM support_line_quota WHERE site=? AND target=? '
                               'AND month=?', (site, target, month)).fetchone()
            alerted = row['alerted'] if row else 0
            step = 100 if pct >= 100 else max([s for s in rules['steps'] if pct >= s], default=0)
            conn.execute('INSERT INTO support_line_quota(site,target,month,lim,used,alerted,exhausted,'
                         'checked_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(site,target,month) DO UPDATE '
                         'SET lim=excluded.lim, used=excluded.used, alerted=excluded.alerted, '
                         'exhausted=max(exhausted, excluded.exhausted), checked_at=excluded.checked_at',
                         (site, target, month, limit, used, max(alerted, step), 1 if step >= 100 else 0, now))
            if step > alerted:
                notes.append((f'{step}', f'📉 LINE 額度 {step}%：**{site}/{target}** 本月已用 {used}/{limit} 則'
                              + ('，本月剩下的推播會略過。' if step >= 100 else '。')))
            if known and rules['remaining_below'] and 0 < limit - used <= rules['remaining_below'] \
                    and self._once(conn, site, target, month, 'remaining'):
                notes.append(('remaining', f'📉 LINE 額度剩不多：**{site}/{target}** 本月只剩 '
                              f'{limit - used} 則（已用 {used}/{limit}）。'))
            if known and rules['projected_over']:
                day = datetime.fromtimestamp(now, TZ)
                days = calendar.monthrange(day.year, day.month)[1]
                elapsed = (day.day - 1) + (day.hour * 60 + day.minute) / 1440
                if elapsed >= 3:   # too early in the month to project
                    projected = int(used / elapsed * days)
                    if projected * 100 >= rules['projected_over'] * limit and pct < 100 \
                            and self._once(conn, site, target, month, 'projected'):
                        notes.append(('projected', f'📈 LINE 額度預估：**{site}/{target}** 照本月速度，月底約用 '
                                      f'{projected}/{limit} 則（{projected * 100 // limit}%），超過 '
                                      f"{rules['projected_over']}% 警戒。目前已用 {used}。"))
        if self.alerts is not None:
            for kind, text in notes:
                self.alerts.platform(f'line-quota:{site}:{target}:{month}:{kind}', text)
        return res

    def _relay_target(self, source_site, relay):
        dest = relay['site']
        if not (self.sites_root / dest / 'site.yaml').is_file():
            return dest, None
        return dest, relay_channel(self.sites_root / dest / 'workspace', relay['channel'], source_site)

    def quota_targets(self, site, project):
        """(dest_site, target_label, token_file) for every bot a site pushes to."""
        out = []
        for t in line_config(project)['targets']:
            if 'relay' in t:
                dest, ch = self._relay_target(site, t['relay'])
                if ch:
                    out.append((dest, f"{dest}/{t['relay']['channel']}", ch['token_file']))
            else:
                out.append((site, t['name'], t['token_file']))
        return out

    def check_all_quotas(self, sites):
        """Periodic quota check: ``sites`` yields (site, project).

        Reads LINE's own monthly figures (no message is sent, no quota used).
        Each bot is checked once per call even when several sites push to it.
        Returns {(dest, target): result}; never raises.
        """
        seen, results = set(), {}
        for site, project in sites:
            for dest, label, token_file in self.quota_targets(site, project):
                if (dest, label) in seen:
                    continue
                seen.add((dest, label))
                try:
                    results[(dest, label)] = self.check_quota(dest, label, token_file)
                except Exception as exc:
                    results[(dest, label)] = {'ok': False, 'error': type(exc).__name__}
        return results

    # sending ----------------------------------------------------------------

    def _burst(self, dest, target):
        since = int(self.clock()) - 3600
        with self.core._connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM support_line_deliveries WHERE dest_site=? AND "
                             "target=? AND status='sent' AND created_at>?", (dest, target, since)).fetchone()[0]
        if n > BURST_PER_HOUR and self.alerts is not None:
            self.alerts.platform(f'line-burst:{dest}:{target}',
                                 f'📈 LINE 異常大量：**{dest}/{target}** 1 小時內已送 {n} 則（只告警，不擋）。')

    def _send(self, tenant_id, source, dest, run_id, kind, target, token_file, to, text):
        if self._exhausted(dest, target):
            self._log(tenant_id, source, dest, run_id, kind, target, 'skipped', '本月額度已用完', text)
            return {'ok': False, 'error': 'monthly_limit', 'skipped': True}
        res = self.agent(dest, {'op': 'push', 'keys_dir': keys_dir(self.sites_root, dest),
                                'token_file': token_file, 'to': to, 'text': text})
        if res.get('ok'):
            self._log(tenant_id, source, dest, run_id, kind, target, 'sent', None, text)
            self._burst(dest, target)
        else:
            if res.get('error') == 'monthly_limit':
                self._mark_exhausted(dest, target)
                if self.alerts is not None:
                    self.alerts.platform(f'line-quota:{dest}:{target}:{self._month()}:100',
                                         f'📉 LINE 額度用完：**{dest}/{target}**，本月剩下的推播會略過。')
            self._log(tenant_id, source, dest, run_id, kind, target, 'failed', res.get('error'), text)
        return res

    def push_run(self, *, tenant_id, site, project, run_id, report, deliverable):
        """Push one cleaned answer to every target in project.yaml.

        Returns {target_name: status}; never raises. Each target is independent.
        """
        text = truncate_for_line(deliverable)
        results = {}
        for target in line_config(project)['targets']:
            name = target['name']
            if 'relay' in target:
                if not report.get('relay', True):
                    continue
                dest, channel = self._relay_target(site, target['relay'])
                label = f"{dest}/{target['relay']['channel']}"
                if channel is None:
                    self._log(tenant_id, site, dest, run_id, 'relay', label, 'failed',
                              '對方站台不存在或未同意轉交', text)
                    res = {'ok': False, 'error': 'relay not accepted'}
                else:
                    res = self._send(tenant_id, site, dest, run_id, 'relay', label,
                                     channel['token_file'], channel['to'], text)
            else:
                res = self._send(tenant_id, site, site, run_id, 'push', name,
                                 target['token_file'], target['to'], text)
            results[name] = res
        return results

    # retention --------------------------------------------------------------

    def purge(self, now=None):
        cutoff = int((now or self.clock()) - RETENTION_DAYS * 86400)
        with self.core._connect() as conn:
            n = conn.execute('DELETE FROM support_line_deliveries WHERE created_at<?', (cutoff,)).rowcount
            conn.execute('DELETE FROM support_line_quota WHERE checked_at<?', (cutoff - 31 * 86400,))
            conn.execute('DELETE FROM support_line_quota_alerts WHERE at<?', (cutoff - 31 * 86400,))
        return n


# -- alerts --------------------------------------------------------------------

class SiteAlerts:
    """Discord alerts with a 30-minute quiet period per (kind, subject).

    ``site(...)`` sends through the SITE's own bot inside its container, as the
    site's project.yaml ``notify:`` says. ``platform(...)`` uses the platform's
    notifier (``PLATFORM_NOTIFY_DISCORD_*``) for things the site cannot say
    itself. When the site path fails, the alert falls back to the platform.
    Both paths are optional: with neither configured, alerts are dropped.
    """

    QUIET = 1800

    def __init__(self, *, notifier=None, agent=None, clock=time.time, sites_root=None):
        self.sites_root = sites_root
        self.notifier = notifier
        self.agent = agent or docker_agent
        self.clock = clock
        self._last = {}
        self._lock = threading.Lock()

    def _due(self, key):
        now = self.clock()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self.QUIET:
                return False
            self._last[key] = now
            return True

    def platform(self, key, text):
        if self.notifier is None or not self._due('p:' + key):
            return False
        try:
            self.notifier.system_failure(text)
            return True
        except Exception:
            return False

    def site(self, key, text, *, site, notify):
        if not self._due('s:' + key):
            return False
        if notify:
            res = self.agent(site, {'op': 'discord', 'keys_dir': keys_dir(self.sites_root, site) if self.sites_root else KEYS_DIR,
                                    'token_file': notify['token_file'],
                                    'channel_id': notify['channel_id'], 'text': text})
            if res.get('ok'):
                return True
        # Site bot unavailable: the platform says it instead (the quiet key
        # was just reserved above).
        if self.notifier is None:
            return False
        try:
            self.notifier.system_failure(text + '\n（站台通知發不出去，由平台代發）')
            return True
        except Exception:
            return False


# -- the scheduler hook ----------------------------------------------------------

def answer_text(output):
    """The run log of ``SiteScheduler._chain_run`` starts with one
    ``[model=…] [tried=…]`` line; the deliverable is what follows."""
    text = output or ''
    if text.startswith('[model='):
        text = text.split('\n', 1)[1] if '\n' in text else ''
    return text


class ScheduleDelivery:
    """What the scheduler calls after each run when delivery is enabled.

    ``after_run`` returns a suffix for the run log (``[LINE：…]``,
    ``[HackMD：…]``) and never raises. Nothing is sent unless the report says
    ``line_push: true`` or has ``hackmd:``, the run succeeded, and the run was
    started with ``deliver=True``.
    """

    QUOTA_EVERY = 3600

    def __init__(self, sites_root, line, alerts=None, *, site_script=run_site_script):
        self.sites_root = Path(sites_root)
        self.line = line
        self.alerts = alerts
        self.site_script = site_script

    def workspace(self, site):
        from support_site_office import office_workspace
        return office_workspace(self.sites_root / site)

    def container_workspace(self, site):
        """The office folder as seen inside the site container."""
        from support_site_office import office_dir
        sub = office_dir(self.sites_root / site)
        return f'{WORKSPACE}/{sub}' if sub else WORKSPACE

    def publish_hackmd(self, site, conf, answer):
        """Refresh the HackMD note; return (text to push, log note).

        The pushed text becomes "this week's changes + URL" ("本週無更新 + URL"
        when there are none). If HackMD fails, the original answer is pushed.
        """
        from support_schedule_answer import deliverable

        changes, _ = deliverable(answer)
        res = self.site_script(site, HACKMD_SCRIPT, {
            'workspace': self.container_workspace(site), 'repo': conf['repo'], 'keys_dir': keys_dir(self.sites_root, site),
            'token_file': conf['token_file'], 'owner_label': conf['owner'], 'changes': changes},
            timeout=HACKMD_TIMEOUT)
        url = str(res.get('url', '')) if isinstance(res, dict) else ''
        if not (isinstance(res, dict) and res.get('ok')) or not url.startswith('https://hackmd.io/'):
            error = res.get('error', 'unknown') if isinstance(res, dict) else 'unknown'
            return answer, f'失敗（{error}），改推原文'
        head = changes.strip() if changes else f'📁 顧問側寫 — {conf["owner"]}\n\n本週無更新。'
        return f'{head}\n\n👉 完整側寫（只有你看得到）：{url}', url

    def after_run(self, *, tenant_id, site, hive_id, report_name, run_id, ok, output, deliver):
        try:
            return self._after_run(tenant_id, site, hive_id, report_name, run_id, ok, output, deliver)
        except Exception as exc:  # delivery never changes the run's own result
            print(f'[line] {site}/{hive_id}/{report_name}: {type(exc).__name__}', flush=True)
            return f'\n\n[LINE：錯誤 {type(exc).__name__}]'

    def _after_run(self, tenant_id, site, hive_id, report_name, run_id, ok, output, deliver):
        from support_schedule_answer import deliverable

        if not _SITE.match(site or ''):
            return ''
        project = load_project(self.workspace(site), hive_id)
        cfg = report_config(project, report_name)
        if cfg is None or not (cfg['line_push'] or cfg['hackmd']):
            return ''
        if not deliver:
            return '\n\n[試跑：不推播]'
        notify = notify_config(project)
        label = f'{site}/{hive_id}/{report_name}'
        if not ok:
            if self.alerts is not None:
                tail = '\n'.join((output or '').strip().splitlines()[-3:])[-600:]
                self.alerts.site(f'fail:{label}', f'排程失敗：**{label}**\n{tail}',
                                 site=site, notify=notify)
            return ''
        body, log = answer_text(output), ''
        if cfg['hackmd']:
            body, note = self.publish_hackmd(site, cfg['hackmd'], body)
            log += f'\n\n[HackMD：{note}]'
        if not cfg['line_push']:
            return log
        text, reason = deliverable(body)
        if reason:
            if self.alerts is not None:
                self.alerts.site(f'empty:{label}',
                                 f'⚠️ 排程無實質產出（已攔截，未推 LINE）：**{label}**\n原因：{reason}',
                                 site=site, notify=notify)
            return log + f'\n\n[LINE 未推送：{reason}]'
        sent = self.line.push_run(tenant_id=tenant_id, site=site, project=project,
                                  run_id=run_id, report=cfg, deliverable=text)
        failed = {k: v.get('error') for k, v in sent.items() if not v.get('ok')}
        if failed and self.alerts is not None:
            self.alerts.site(f'push:{label}', f'LINE 推播失敗：**{label}**\n' +
                             '\n'.join(f'- {k}: {v}' for k, v in failed.items()),
                             site=site, notify=notify)
        summary = ', '.join(f'{k}={"ok" if v.get("ok") else v.get("error")}' for k, v in sent.items())
        return log + f'\n\n[LINE：{summary or "沒有推播對象"}]'

    # periodic work, called from the scheduler tick --------------------------

    def check_quotas(self, sites):
        """``sites`` yields (site, [hive_id, ...])."""
        targets = []
        for site, hives in sites:
            if not _SITE.match(site or ''):
                continue
            for hive_id in hives:
                targets.append((site, load_project(self.workspace(site), hive_id)))
        return self.line.check_all_quotas(targets)

    def purge(self):
        return self.line.purge()
