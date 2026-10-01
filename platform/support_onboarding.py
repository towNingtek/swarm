"""Resumable customer onboarding, not a provisioning/model verification service.

Only rule acknowledgement is customer-writable. Site/model/handoff remain
blocked until trusted execution adapters exist; a checkbox never proves a site.
No credentials, free text, supplied tenant IDs, or model responses are stored.
"""
from datetime import datetime, timezone

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from support_app import _body, _error
from support_core import Conflict, InvalidInput, SupportError
from support_site_jobs import SupportSiteJobs

RULES_VERSION = 'onboarding-rules-v1'
RULES = ('客服對話可由授權的平台支援人員查看；一般工作對話不會因此自動分享。',
         '密碼與模型憑證只在專用設定介面輸入，不貼到客服聊天。',
         '平台客服模型與你的日常工作模型分開；客服額度不代表一般工作由平台代付。')


def starter_allowance(core, conn, tenant_id):
    """This month's starter-model allowance: mode, used and limit tokens."""
    def table(name):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (name,)).fetchone() is not None
    if not table('support_quota_policies'):
        return {'mode': 'disabled', 'used': 0, 'limit': None}
    policy = conn.execute('SELECT mode, monthly_limit FROM support_quota_policies '
                          'WHERE tenant_id=?', (tenant_id,)).fetchone()
    mode = policy['mode'] if policy else 'disabled'
    used = 0
    if table('support_site_model_usage'):
        from support_site_model import SiteModelAccess

        month = datetime.fromtimestamp(core.clock(), timezone.utc).strftime('%Y-%m')
        used = SiteModelAccess._used(conn, tenant_id, month)
    return {'mode': mode, 'used': int(used),
            'limit': policy['monthly_limit'] if policy and mode == 'capped' else None}


class SupportOnboarding:
    def __init__(self, core, *, model_settings=None, office=None, tasks=None, scheduler=None):
        from support_model_settings import CustomerModelSettings
        self.model_settings = model_settings or CustomerModelSettings(core)
        if self.model_settings.core is not core:
            raise ValueError('model settings must share core')
        self.core = core
        # Read-only view of the customer's site office (SiteOffice). Absent in
        # fixtures: the office is then reported as unknown, never invented.
        self.office = office
        # Optional platform task library (offers) and site scheduler (run logs).
        self.tasks = tasks
        self.scheduler = scheduler
        self.site_jobs = SupportSiteJobs(core)  # Read-only worker authority.
        with core._connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS support_onboarding_ack (
                principal_id TEXT NOT NULL REFERENCES principals(id),
                rules_version TEXT NOT NULL, acknowledged_at REAL NOT NULL,
                PRIMARY KEY(principal_id, rules_version))''')

    @staticmethod
    def _site_step(site_status):
        """Report what actually happened, never a fixed 'not connected'."""
        status = site_status.get('status')
        if site_status.get('provisioned'):
            return {'id': 'site_preparation', 'status': 'complete'}
        if status in ('queued', 'running'):
            return {'id': 'site_preparation', 'status': 'in_progress'}
        if status in ('reconciliation_required', 'failed'):
            return {'id': 'site_preparation', 'status': 'attention',
                    'reason': 'provisioning_needs_operator'}
        if site_status.get('simulated'):
            # Simulation is not a site; say so rather than showing 'complete'.
            return {'id': 'site_preparation', 'status': 'blocked',
                    'reason': 'simulated_only'}
        return {'id': 'site_preparation', 'status': 'pending',
                'reason': 'awaiting_operator_request'}

    @staticmethod
    def _model_step(model_status):
        state = model_status.get('state')
        if model_status.get('verified_for_work'):
            return {'id': 'customer_model', 'status': 'complete'}
        if state in ('pending', 'probing', 'simulated_pass', 'failed'):
            # Selected, but nothing here proves the model works.
            return {'id': 'customer_model', 'status': 'in_progress',
                    'reason': 'selected_not_verified'}
        if state == 'unavailable':
            return {'id': 'customer_model', 'status': 'attention',
                    'reason': 'selection_withdrawn'}
        return {'id': 'customer_model', 'status': 'pending',
                'reason': 'awaiting_selection'}

    def _office(self, site_status):
        if self.office is None or not site_status.get('provisioned'):
            return None
        try:
            return self.office.for_host(site_status.get('site_host'))
        except Exception:
            # The office is an aid; an unreadable site must not break the page.
            return None

    def _starter(self, conn, tenant_id):
        return starter_allowance(self.core, conn, tenant_id)

    @staticmethod
    def _office_step(office):
        if office is None:
            return {'id': 'office', 'status': 'blocked', 'reason': 'site_not_ready'}
        if office['template'] in (None, 'none') and office['registry'] == 'missing':
            return {'id': 'office', 'status': 'skipped', 'reason': 'no_office_template'}
        if any(h['exists'] for h in office['hives']):
            return {'id': 'office', 'status': 'complete'}
        return {'id': 'office', 'status': 'pending', 'reason': 'no_hive_yet'}

    @staticmethod
    def _own_model_step(office):
        model = office and office.get('default_model')
        if model is None:
            return {'id': 'own_model', 'status': 'blocked' if office is None else 'pending',
                    'reason': 'site_not_ready' if office is None else 'default_unknown'}
        if model['starter']:
            return {'id': 'own_model', 'status': 'pending', 'reason': 'using_starter'}
        return {'id': 'own_model', 'status': 'complete'}

    def status(self, actor):
        # Diagnostic progress is separately authorized and never grants readiness.
        site_status = self.site_jobs.customer_status(actor)
        model_status = self.model_settings.status(actor)
        office = self._office(site_status)
        with self.core._connect() as conn:
            conn.execute('BEGIN')
            principal = self.core._customer(conn, actor)
            acknowledged = conn.execute(
                'SELECT acknowledged_at FROM support_onboarding_ack WHERE principal_id=? AND rules_version=?',
                (principal.id, RULES_VERSION)).fetchone()
            starter = self._starter(conn, principal.tenant_id)
            return {
                'rules_version': RULES_VERSION, 'rules': list(RULES),
                'rules_acknowledged': acknowledged is not None,
                'next_step': 'site_preparation' if acknowledged else 'rules',
                # Readiness is not claimed here: entering the site is offered
                # once it is provisioned, but the platform cannot prove work.
                'ready_for_work': False,
                'site_job': site_status,
                'model_settings': model_status,
                'office': office,
                'starter': starter,
                'tasks': self._tasks(actor, principal.tenant_id, office),
                'steps': [
                    {'id': 'activation', 'status': 'complete'},
                    {'id': 'rules', 'status': 'complete' if acknowledged else 'pending'},
                    self._site_step(site_status),
                    self._office_step(office),
                    self._own_model_step(office),
                ],
                'support_url': '/customer/chat',
            }

    def _tasks(self, actor, tenant_id, office):
        value = {'executor': self.scheduler is not None, 'offers': [], 'waiting_for_hive': 0,
                 'recent': [], 'runs': []}
        if self.tasks is not None:
            value.update(self.tasks.customer_view(actor))
        if self.scheduler is not None and office is not None:
            value['runs'] = self.scheduler.runs_for(tenant_id, 20)
        return value

    def _site_host(self, actor):
        from support_site_office import OfficeError

        site = self.site_jobs.customer_status(actor)
        if not site.get('provisioned'):
            raise OfficeError('站台尚未就緒')
        return site['site_host']

    def accept_task(self, actor, body):
        from support_site_office import OfficeError

        if self.tasks is None:
            raise OfficeError('這個平台沒有任務庫')
        self.tasks.accept(actor, self._site_host(actor), body['delivery'], body['hive'])
        return self.status(actor)

    def dismiss_task(self, actor, body):
        from support_site_office import OfficeError

        if self.tasks is None:
            raise OfficeError('這個平台沒有任務庫')
        self.tasks.dismiss(actor, body['delivery'])
        return self.status(actor)

    def run_schedule(self, actor, body):
        from support_site_office import OfficeError

        if self.scheduler is None:
            raise OfficeError('這個平台還沒有排程執行器')
        host = self._site_host(actor)
        with self.core._connect() as conn:
            conn.execute('BEGIN')
            tenant_id = self.core._customer(conn, actor).tenant_id
        self.scheduler.run_now(tenant_id, host, body['hive'], body['name'])
        return self.status(actor)

    # -- office edits (schedules) -----------------------------------------

    def _editor(self, actor):
        from support_site_office import OfficeError

        if self.office is None or not hasattr(self.office, 'editor_for_host'):
            raise OfficeError('這個平台沒有開放辦公室編輯')
        site = self.site_jobs.customer_status(actor)
        if not site.get('provisioned'):
            raise OfficeError('站台尚未就緒')
        # The site is derived from the customer's own capability, never from
        # the request: a customer can only ever edit their own office.
        return self.office.editor_for_host(site.get('site_host'))

    def save_schedule(self, actor, body):
        editor = self._editor(actor)
        editor.save_schedule(body['revision'], body['hive'], original=body['original'],
                             name=body['name'], role=body['role'], cron=body['cron'],
                             skill=body['skill'], context=body['context'])
        return self.status(actor)

    def delete_schedule(self, actor, body):
        self._editor(actor).delete_schedule(body['revision'], body['hive'], body['name'])
        return self.status(actor)

    def set_hive_enabled(self, actor, body):
        if body['enabled'] not in ('true', 'false'):
            raise InvalidInput('invalid enabled flag')
        self._editor(actor).set_hive_enabled(body['revision'], body['hive'],
                                             body['enabled'] == 'true')
        return self.status(actor)

    def acknowledge(self, actor, version):
        if version != RULES_VERSION:
            raise Conflict('rules changed; reload required')
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            principal = self.core._customer(conn, actor)
            conn.execute('INSERT OR IGNORE INTO support_onboarding_ack VALUES (?, ?, ?)',
                         (principal.id, version, self.core.clock()))
        return self.status(actor)


def install_onboarding(app, core, *, model_settings=None, enable_copilot=False, office=None,
                       tasks=None, scheduler=None):
    if not callable(getattr(app.state, 'support_actor_for', None)):
        raise ValueError('authenticated customer app required')
    if getattr(app.state, 'support_onboarding', None) is not None:
        raise ValueError('onboarding already installed')
    onboarding = SupportOnboarding(core, model_settings=model_settings, office=office,
                                   tasks=tasks, scheduler=scheduler)

    async def dispatch(request, write=False):
        try:
            actor = await app.state.support_actor_for(request)
            if request.scope.get('query_string'):
                raise InvalidInput('query not accepted')
            if write:
                body = await _body(request, ('rules_version',))
                value = await run_in_threadpool(onboarding.acknowledge, actor, body['rules_version'])
            else:
                value = await run_in_threadpool(onboarding.status, actor)
            return JSONResponse(value)
        except Conflict:
            return _error(409)
        except OverflowError:
            return _error(413)
        except (ValueError, InvalidInput, UnicodeError):
            return _error(400)
        except SupportError:
            return _error(401)

    # The Copilot is composed explicitly: without it these routes do not exist,
    # so nothing can act on a customer's behalf by default.
    copilot = None
    if enable_copilot:
        from support_onboarding_copilot import OnboardingCopilot

        copilot = OnboardingCopilot(onboarding)

        async def copilot_dispatch(request, perform=False):
            try:
                actor = await app.state.support_actor_for(request)
                if request.scope.get('query_string'):
                    raise InvalidInput('query not accepted')
                if perform:
                    body = await _body(request, ('kind', 'arguments', 'token'))
                    value = await run_in_threadpool(
                        copilot.perform, actor, body['kind'], body['arguments'],
                        body['token'])
                else:
                    value = await run_in_threadpool(copilot.next_step, actor)
                return JSONResponse(value)
            except Conflict:
                return _error(409)
            except OverflowError:
                return _error(413)
            except (ValueError, InvalidInput, UnicodeError):
                return _error(400)
            except SupportError:
                return _error(401)

        @app.get('/customer/onboarding/copilot')
        async def copilot_next(request: Request):
            return await copilot_dispatch(request)

        @app.post('/customer/onboarding/copilot/perform')
        async def copilot_perform(request: Request):
            return await copilot_dispatch(request, True)

    from support_site_office import OfficeConflict, OfficeError

    office_actions = {
        'schedule': (onboarding.save_schedule,
                     ('revision', 'hive', 'original', 'name', 'role', 'cron', 'skill', 'context')),
        'schedule-delete': (onboarding.delete_schedule, ('revision', 'hive', 'name')),
        'hive-enabled': (onboarding.set_hive_enabled, ('revision', 'hive', 'enabled')),
        'task-accept': (onboarding.accept_task, ('delivery', 'hive')),
        'task-dismiss': (onboarding.dismiss_task, ('delivery',)),
        'schedule-run': (onboarding.run_schedule, ('hive', 'name')),
    }

    async def office_dispatch(request, action):
        handler, fields = office_actions[action]
        try:
            actor = await app.state.support_actor_for(request)
            if request.scope.get('query_string'):
                raise InvalidInput('query not accepted')
            body = await _body(request, fields)
            value = await run_in_threadpool(handler, actor, body)
            return JSONResponse(value)
        except OfficeConflict:
            return JSONResponse({'error': 'conflict',
                                 'message': '站台上的設定剛被改過（可能是站台裡的 AI），已重新讀取，請再確認一次。'},
                                status_code=409)
        except OfficeError as exc:
            return JSONResponse({'error': 'refused', 'message': str(exc)}, status_code=422)
        except OSError:
            return JSONResponse({'error': 'refused',
                                 'message': '平台沒有權限寫入站台設定，未寫入；請通知平台管理員。'},
                                status_code=422)
        except Conflict:
            return _error(409)
        except OverflowError:
            return _error(413)
        except (ValueError, InvalidInput, UnicodeError):
            return _error(400)
        except SupportError:
            return _error(401)

    def office_route(action):
        async def endpoint(request: Request):
            return await office_dispatch(request, action)
        return endpoint

    for action in office_actions:
        app.add_api_route('/customer/office/' + action, office_route(action), methods=['POST'],
                          include_in_schema=False)

    @app.get('/customer/onboarding/status')
    async def status(request: Request):
        return await dispatch(request)

    @app.post('/customer/onboarding/acknowledge')
    async def acknowledge(request: Request):
        return await dispatch(request, True)

    app.state.support_onboarding = onboarding
    app.state.support_onboarding_copilot = copilot
    return app
