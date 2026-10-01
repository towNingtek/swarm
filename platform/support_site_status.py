"""Writes ``workspace/.swarm/platform-status.md`` into each customer site.

The site's own AI (in the customer's DSH) cannot see the platform. This file
tells it, in plain words, what the platform knows about THIS site only:

* which default model the site uses (starter or the customer's own),
* this month's starter-model allowance (used / limit),
* every schedule and whether it will run (and why not),
* the last few schedule runs with a short excerpt of their result,
* how many platform-offered tasks wait for the customer's decision.

Never written: keys, tokens, passwords, other tenants, platform internals.
Everything is built from values the platform already shows the same customer
on the onboarding page. The file is rewritten only when its content changes,
atomically, never through a symlink.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import safe_fs
from support_site_office import _SECRET, _is_dir

TZ = ZoneInfo('Asia/Taipei')
RUNS = 5
EXCERPT = 300
STATE_TEXT = {
    'ready': '會執行',
    'disabled': '不會執行：這間公司的排程總開關（registry 的 enabled）沒開，或角色被停用',
    'missing_definition': '不會執行：project.yaml 的 reports: 裡沒有同名定義',
    'missing_time': '不會執行：registry 裡沒有這個排程的時間',
}
RUN_TEXT = {'succeeded': '成功', 'failed': '失敗', 'skipped': '跳過', 'running': '執行中'}


def _when(ts):
    return datetime.fromtimestamp(ts, TZ).strftime('%Y-%m-%d %H:%M') if ts else '—'


def _excerpt(text):
    text = ' '.join((text or '').split())
    if _SECRET.search(text):
        return '（結果含有像金鑰的內容，未列出；請到平台頁查看）'
    return text[:EXCERPT] + ('…' if len(text) > EXCERPT else '')


def render_status(*, site_host, office, starter, runs, offers_waiting, now):
    lines = ['# 平台狀態（由平台自動產生，請勿修改）', '',
             f'站台：{site_host}　更新時間：{_when(now)}（台北時間）', '']
    model = (office or {}).get('default_model')
    lines.append('## 模型')
    if model is None:
        lines.append('- 目前預設模型：平台讀不到（可能還在建立中）。')
    elif model['starter']:
        lines.append(f"- 目前預設模型：平台借用的起步模型 `{model['model']}`（有每月額度）。")
    else:
        lines.append(f"- 目前預設模型：使用者自己的 `{model['provider']}/{model['model']}`。")
    mode, used, limit = starter.get('mode'), starter.get('used', 0), starter.get('limit')
    if mode == 'capped' and limit:
        left = max(0, limit - used)
        lines.append(f'- 起步模型本月額度：已用 {used:,} / {limit:,} tokens（剩 {left:,}）。')
    elif mode == 'unlimited':
        lines.append(f'- 起步模型本月額度：不設上限（本月已用 {used:,} tokens）。')
    else:
        lines.append('- 起步模型：目前停用；要繼續工作請接上自己的模型。')
    lines.append('')

    lines.append('## 排程')
    hives = [h for h in (office or {}).get('hives', []) if h.get('exists')]
    if not hives:
        lines.append('- 還沒有公司，所以沒有排程。')
    for hive in hives:
        switch = '開' if hive['enabled'] else '關（全部不會執行）'
        lines.append(f"- 公司 `{hive['id']}`（{hive['name']}），排程總開關：{switch}")
        if not hive['schedules']:
            lines.append('  - 沒有排程。')
        for job in hive['schedules']:
            cron = f"`{job['cron']}`" if job['cron'] else '（沒有時間）'
            role = job['role'] or '—'
            lines.append(f"  - `{job['name']}`　角色 {role}　時間 {cron}　→ {STATE_TEXT.get(job['state'], job['state'])}")
    lines.append('')

    lines.append(f'## 最近 {RUNS} 次執行')
    if not runs:
        lines.append('- 還沒有執行紀錄。')
    for run in runs[:RUNS]:
        how = '手動' if run['trigger'] == 'manual' else '排程'
        lines.append(f"- {_when(run['started_at'])}　`{run['hive']}/{run['name']}`（{how}）："
                     f"{RUN_TEXT.get(run['status'], run['status'])}")
        if run['output']:
            lines.append(f"  > {_excerpt(run['output'])}")
    lines.append('')

    lines.append('## 平台提供的任務')
    if offers_waiting:
        lines.append(f'- 有 {offers_waiting} 個任務等使用者在平台頁決定要不要加入（你無法代為接受）。')
    else:
        lines.append('- 沒有等待決定的任務。')
    lines.append('')
    return '\n'.join(lines)


def _body(text):
    return text.split('\n## ', 1)[-1]


def _write(workspace: Path, text):
    """Atomic, never through a symlink, only when changed. True if written."""
    rel = Path('.swarm', 'platform-status.md')
    if not _is_dir(workspace, '.swarm'):
        return False
    try:
        if _body(safe_fs.read_text(workspace, rel, 256 * 1024)) == _body(text):
            return False
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError):
        if safe_fs.kind(workspace, rel) != 'file':
            return False
    try:
        safe_fs.write_atomic(workspace, rel, text)
    except safe_fs.UnsafePath:
        return False
    return True


class SiteStatusWriter:
    """Refreshes every provisioned site's status file. Best effort."""

    def __init__(self, scheduler, onboarding_starter, *, library=None, clock=time.time,
                 interval=120.0):
        self.scheduler = scheduler            # sites(), office, runs_for()
        self.starter = onboarding_starter     # callable(conn, tenant_id) -> dict
        self.library = library
        self.clock = clock
        self.interval = interval
        self._last = 0.0

    def _offers_waiting(self, tenant_id):
        if self.library is None:
            return 0
        with self.scheduler.core._connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='support_task_deliveries'").fetchone():
                return 0
            return conn.execute("SELECT COUNT(*) FROM support_task_deliveries WHERE tenant_id=? "
                                "AND status='pending' AND mode='offer'", (tenant_id,)).fetchone()[0]

    def refresh(self, *, force=False):
        now = self.clock()
        if not force and now - self._last < self.interval:
            return []
        self._last = now
        written = []
        office_root = self.scheduler.office
        for tenant_id, host in self.scheduler.sites().items():
            name = self.scheduler._site_name(host)
            if name is None:
                continue
            try:
                office = office_root.for_host(host)
                if office is None:
                    continue
                with self.scheduler.core._connect() as conn:
                    starter = self.starter(conn, tenant_id)
                text = render_status(site_host=host, office=office, starter=starter,
                                     runs=self.scheduler.runs_for(tenant_id, RUNS),
                                     offers_waiting=self._offers_waiting(tenant_id), now=now)
                if _write(Path(office_root.sites_root) / name / 'workspace', text):
                    written.append(host)
            except Exception as exc:  # one broken site never stops the others
                print(f'[status] {host}: {type(exc).__name__}', flush=True)
        return written
