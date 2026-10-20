"""Read-only operator dashboard data: one call answers "is everything OK?".

GET /admin/overview returns, per tenant: site state, whether the site answers,
this month's platform-model usage, the last scheduled run, open invites, and a
short "needs you" list across all tenants. Allowlisted columns only: never
credentials, hashes, invite tokens or message bodies.
"""
import concurrent.futures
import http.client
import time

from support_core import SupportError

_PROBE_TIMEOUT = 2.0


def _probe(port):
    """True when something answers HTTP on the site's loopback port.

    Any HTTP status counts (a login wall or a host check still means the site
    process is up); only a refused/timed-out connection means it is down.
    """
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=_PROBE_TIMEOUT)
    try:
        conn.request('HEAD', '/', headers={'Host': '127.0.0.1:3080'})
        conn.getresponse().read()
        return True
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


# The optional board plugin (a DSH web plugin serving /board/ inside the site).
# Its presence is read from the site's web profile on disk: the site relay
# answers 403 to the platform for every path, so asking over HTTP proves nothing.
BOARD_PLUGIN = 'dsh-swarm-board'


def _board_installed(site_name):
    """True when this site's web profile has the board plugin installed.

    Takes the site name this platform recorded when it built the site (never a
    customer-supplied value) and looks under SWARM_SITES_ROOT.
    """
    import os
    import re

    if not isinstance(site_name, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', site_name):
        return False
    import _site_path  # noqa: F401  (site/ on sys.path)
    import common

    return os.path.isfile(os.path.join(str(common.SITES_ROOT), site_name, 'dsh-home', 'profiles', 'web',
                                       'node_modules', BOARD_PLUGIN, 'lib', 'index.js'))


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def overview(core, actor, *, now=None, probe=_probe, board_installed=_board_installed):
    core._admin(actor)
    now = time.time() if now is None else now
    month = time.strftime('%Y-%m', time.gmtime(now))
    with core._connect() as conn:
        tenants = conn.execute('SELECT id, site_host, enabled FROM tenants ORDER BY site_host').fetchall()
        jobs = ({r['tenant_id']: r for r in conn.execute(
                    'SELECT tenant_id, status, site_name, site_port, updated_at FROM support_site_jobs')}
                if _table(conn, 'support_site_jobs') else {})
        usage = {}
        months = {}
        if _table(conn, 'support_site_model_usage'):
            for r in conn.execute('SELECT tenant_id, month, SUM(COALESCE(actual, estimate)) AS tokens, '
                                  'COUNT(*) AS calls FROM support_site_model_usage '
                                  'GROUP BY tenant_id, month ORDER BY month'):
                months.setdefault(r['tenant_id'], []).append(
                    {'month': r['month'], 'tokens': int(r['tokens'] or 0), 'calls': r['calls']})
                if r['month'] == month:
                    usage[r['tenant_id']] = {'tokens': int(r['tokens'] or 0), 'calls': r['calls']}
        policies = {r['tenant_id']: r for r in conn.execute(
            'SELECT tenant_id, mode, monthly_limit FROM support_quota_policies')}
        runs = ({r['tenant_id']: r for r in conn.execute(
                    'SELECT tenant_id, name, status, MAX(started_at) AS started_at '
                    'FROM support_schedule_runs GROUP BY tenant_id')}
                if _table(conn, 'support_schedule_runs') else {})
        failed_runs = ({r['tenant_id']: r['n'] for r in conn.execute(
                           "SELECT tenant_id, COUNT(*) AS n FROM support_schedule_runs "
                           "WHERE status='failed' AND started_at>? GROUP BY tenant_id", (now - 7 * 86400,))}
                       if _table(conn, 'support_schedule_runs') else {})
        failed_tasks = ({r['tenant_id']: r['n'] for r in conn.execute(
                            "SELECT tenant_id, COUNT(*) AS n FROM support_task_deliveries "
                            "WHERE status='failed' GROUP BY tenant_id")}
                        if _table(conn, 'support_task_deliveries') else {})
        # Day buckets in the platform's schedule time zone (UTC+8, the same
        # Asia/Taipei the site scheduler uses), last 30 days.
        daily = []
        if _table(conn, 'support_site_model_usage'):
            daily = [{'day': r['day'], 'tenant_id': r['tenant_id'], 'tokens': int(r['tokens'] or 0)}
                     for r in conn.execute(
                         "SELECT date(created_at + 8*3600, 'unixepoch') AS day, tenant_id, "
                         "SUM(COALESCE(actual, estimate)) AS tokens FROM support_site_model_usage "
                         "WHERE created_at>? GROUP BY day, tenant_id ORDER BY day", (now - 30 * 86400,))]
        # Never the run output: it is the customer's AI writing, not ours to show here.
        recent_runs = ([{'tenant_id': r['tenant_id'], 'hive': r['hive'], 'name': r['name'],
                         'trigger': r['trigger'], 'status': r['status'],
                         'started_at': r['started_at'], 'finished_at': r['finished_at']}
                        for r in conn.execute(
                            'SELECT tenant_id, hive, name, trigger, status, started_at, finished_at '
                            'FROM support_schedule_runs ORDER BY started_at DESC LIMIT 12')]
                       if _table(conn, 'support_schedule_runs') else [])
        invites = {r['tenant_id']: r['n'] for r in conn.execute(
            'SELECT tenant_id, COUNT(*) AS n FROM invites WHERE redeemed_at IS NULL '
            'AND revoked_at IS NULL AND expires_at>? GROUP BY tenant_id', (now,))}
        accounts = {r['tenant_id']: r['n'] for r in conn.execute(
            'SELECT tenant_id, COUNT(*) AS n FROM principals GROUP BY tenant_id')}
        waiting = {}
        for r in conn.execute(
                'SELECT r.tenant_id, (SELECT sender_kind FROM messages m WHERE m.room_id=r.id '
                'ORDER BY seq DESC LIMIT 1) AS last FROM rooms r'):
            if r['last'] == 'customer':
                waiting[r['tenant_id']] = waiting.get(r['tenant_id'], 0) + 1

    ports = {t['id']: (jobs[t['id']]['site_port'] if t['id'] in jobs else None) for t in tenants}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        alive = dict(zip(ports, pool.map(lambda p: probe(p) if p else None, ports.values())))

    sites, attention = [], []
    for t in tenants:
        tid, host = t['id'], t['site_host']
        job = jobs.get(tid)
        run = runs.get(tid)
        policy = policies.get(tid)
        site = {
            'id': tid, 'host': host, 'enabled': bool(t['enabled']),
            'site_job': job['status'] if job else None,
            'online': alive.get(tid),
            'board': bool(job and job['status'] == 'succeeded_provisioned' and board_installed(job['site_name'])),
            'accounts': accounts.get(tid, 0),
            'open_invites': invites.get(tid, 0),
            'usage_month': usage.get(tid, {'tokens': 0, 'calls': 0}),
            'usage_history': months.get(tid, []),
            'policy': {'mode': policy['mode'] if policy else 'disabled',
                       'monthly_limit': policy['monthly_limit'] if policy else None},
            'last_run': None if run is None else {'name': run['name'], 'status': run['status'],
                                                  'at': run['started_at']},
            'waiting': waiting.get(tid, 0),
        }
        sites.append(site)
        if site['waiting']:
            attention.append({'kind': 'chat', 'tenant_id': tid, 'host': host,
                              'text': f'{site["waiting"]} 則客戶訊息等回覆'})
        if job and job['status'] in ('failed', 'reconciliation_required'):
            attention.append({'kind': 'site_job', 'tenant_id': tid, 'host': host,
                              'text': '建站失敗，需要人工處理'})
        if site['online'] is False:
            attention.append({'kind': 'offline', 'tenant_id': tid, 'host': host,
                              'text': '站台沒有回應'})
        if failed_runs.get(tid):
            attention.append({'kind': 'schedule', 'tenant_id': tid, 'host': host,
                              'text': f'近 7 天有 {failed_runs[tid]} 次排程失敗'})
        if failed_tasks.get(tid):
            attention.append({'kind': 'task', 'tenant_id': tid, 'host': host,
                              'text': f'{failed_tasks[tid]} 個任務交付失敗'})
        if job is None or job['status'] in ('succeeded_provisioned', 'succeeded_simulated'):
            if job and site['accounts'] == 0 and site['open_invites'] == 0:
                attention.append({'kind': 'invite', 'tenant_id': tid, 'host': host,
                                  'text': '站台已建好，還沒有客戶帳號，也沒有有效邀請'})
        limit = site['policy']['monthly_limit']
        if site['policy']['mode'] == 'capped' and limit and site['usage_month']['tokens'] >= 0.8 * limit:
            attention.append({'kind': 'quota', 'tenant_id': tid, 'host': host,
                              'text': '本月平台模型額度已用超過 80%'})
    hosts = {t['id']: t['site_host'] for t in tenants}
    for run in recent_runs:
        run['host'] = hosts.get(run['tenant_id'], '')
    return {'month': month, 'generated_at': int(now), 'sites': sites, 'attention': attention,
            'daily': daily, 'recent_runs': recent_runs,
            'totals': {'sites': len(sites),
                       'online': sum(1 for s in sites if s['online']),
                       'tokens_month': sum(s['usage_month']['tokens'] for s in sites),
                       'attention': len(attention)}}


__all__ = ['overview', 'SupportError']
