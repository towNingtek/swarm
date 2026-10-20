"""Customer overview (swarm#556): the customer's own site, nothing else.

GET /customer/overview -> site host + state, usage this month (platform
starter model), recent runs (name/status/time), attention items. Tenant comes
from the session. Whitelisted fields only.
"""
import time

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from support_app import _error
from support_core import SupportError, Unauthorized
from support_http_policy import SENSITIVE_RESPONSE_HEADERS


def customer_overview(core, actor, now=None):
    now = now or time.time()
    principal = core.authorize_customer(actor)
    tid = principal.tenant_id
    month = time.strftime('%Y-%m', time.gmtime(now))
    with core._connect() as conn:
        def table(name):
            return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        tenant = conn.execute('SELECT site_host FROM tenants WHERE id=?', (tid,)).fetchone()
        tokens = 0
        if table('support_site_model_usage'):
            # Same sum as the operator overview, so both pages show the same number.
            row = conn.execute('SELECT SUM(COALESCE(actual, estimate)) FROM support_site_model_usage '
                               'WHERE tenant_id=? AND month=?', (tid, month)).fetchone()
            tokens = int(row[0] or 0)
        runs, failed7 = [], 0
        if table('support_schedule_runs'):
            for r in conn.execute('SELECT hive, name, status, started_at FROM support_schedule_runs '
                                  'WHERE tenant_id=? ORDER BY started_at DESC LIMIT 10', (tid,)):
                runs.append({'hive': r['hive'], 'name': r['name'], 'status': r['status'], 'at': r['started_at']})
            failed7 = conn.execute("SELECT COUNT(*) FROM support_schedule_runs WHERE tenant_id=? "
                                   "AND status='failed' AND started_at>?", (tid, int(now) - 7 * 86400)).fetchone()[0]
    attention = []
    if failed7:
        attention.append({'kind': 'schedule_failed', 'text': f'近 7 天有 {failed7} 次排程失敗', 'view': 'schedules'})
    return {'site_host': tenant['site_host'] if tenant else None, 'month': month,
            'usage_month': {'tokens': tokens}, 'recent_runs': runs, 'attention': attention}


def install_customer_overview(app, core):
    @app.get('/customer/overview')
    async def overview(request: Request):
        try:
            actor = await app.state.support_actor_for(request)
            if request.scope.get('query_string'):
                return _error(400)
            value = await run_in_threadpool(customer_overview, core, actor)
            return JSONResponse(value, headers=SENSITIVE_RESPONSE_HEADERS)
        except Unauthorized:
            return _error(401)
        except SupportError:
            return _error(403)
    return app
