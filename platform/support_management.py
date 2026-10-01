"""Metadata-only administrator management extension; no provisioning or auth.

Install on create_admin_chat_app, retaining its strict edge middleware and trusted
app.state.support_actor_for adapter. POST /admin/tenants atomically initializes
metadata, never a deployed site. Required: client_request_id (1..128 ASCII ID),
host (DNS host only). Optional: room_mode (ai default/coassist/human), policy
(disabled default; unlimited; or capped with integer monthly_limit 0..10^12).
No other fields, coercions or duplicate JSON keys are accepted. Tenant enabled
means identity metadata enabled, not a public site; quota defaults disabled.
Response 200 (both initial and replay) includes metadata_only=true and
provisioning=not_provisioned. Request IDs are permanent/global/admin-bound;
canonical same payload returns original snapshot; different payload returns 409.
No Docker, DNS, model, keys, logging, or deployment is performed here.

POST /admin/tenants/{id}/invites: {"ttl_seconds": integer}, 60..86400.
The token is returned once, never listed/stored in plaintext. Issuance is NOT
idempotent: lost-response retries create another valid invite. Do not auto-retry;
trusted operators must revoke unwanted invites using core.revoke_invite.
POST /admin/tenants/{id}/quota: {"policy": {"mode": "disabled"|"unlimited"}}
or {"policy": {"mode": "capped", "monthly_limit": integer}}. Policy is separate
from legacy tenant quota_mode metadata. Missing policy always means disabled.
Policy writes are idempotent for identical input, last-write-wins (not CAS).
GET /admin/site-templates lists workspace templates; POST
/admin/tenants/{id}/template {"template": id} picks one (or "none"). It only
affects the next site build and never rewrites an existing workspace.

Task library (support_task_library): GET /admin/tasks lists templates; POST
/admin/tasks {original,name,title,role,cron,skill,context,is_default} saves one;
POST /admin/tasks/delete {name}. GET /admin/tenants/{id}/tasks lists what was
handed to a tenant; POST /admin/tenants/{id}/tasks {names:[...], mode:
direct|offer} hands templates over. This process only records deliveries; the
platform process writes them into the site (direct) or the customer does (offer).
"""
import json

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from support_app import MAX_BODY, _error
from support_bootstrap import SupportBootstrap
from support_chat_app import AdminBoundary, strict_body
from support_core import Conflict, InvalidInput, SupportError, Unauthorized
from support_http_policy import SENSITIVE_RESPONSE_HEADERS
from support_site_office import OfficeError


MIN_INVITE_TTL = 60
MAX_INVITE_TTL = 86400


def install_management(app, core, rooms, quota, *, enable_site_job_queue=False,
                       platform_host=None, teardown=None, templates=None, task_library=None):
    """Extend an existing admin app; never manufacture administrator authority."""
    if rooms.core is not core or quota.core is not core:
        raise ValueError('management dependencies must share core')
    if not callable(getattr(app.state, 'support_actor_for', None)):
        raise ValueError('existing administrator adapter required')
    if not any(m.cls is AdminBoundary for m in app.user_middleware):
        raise ValueError('existing administrator edge middleware required')
    if getattr(app.state, 'support_management_installed', False):
        raise ValueError('management already installed')
    bootstrap = SupportBootstrap(core, rooms, quota, platform_host=platform_host)
    # Explicit opt-in queues metadata only. No worker or external backend is
    # installed here, and HTTP cannot claim/reconcile/complete a worker lease.
    if enable_site_job_queue:
        from support_site_jobs import SupportSiteJobs
        site_jobs = SupportSiteJobs(core)
    if templates is None:
        from support_site_template import SiteTemplateChoice
        templates = SiteTemplateChoice(core)
    elif templates.core is not core:
        raise ValueError('management dependencies must share core')
    if task_library is None:
        from support_task_library import TaskLibrary
        task_library = TaskLibrary(core)
    elif task_library.core is not core:
        raise ValueError('management dependencies must share core')
    app.state.support_management_installed = True

    def summaries(actor, tenant_id=None):
        core._admin(actor)
        # Explicit allowlist: never SELECT credentials, hashes, or room messages.
        with core._connect() as conn:
            query = ('SELECT t.id,t.site_host,t.platform_host,t.enabled,t.quota_mode,'
                     'p.mode,p.monthly_limit FROM tenants t LEFT JOIN '
                     'support_quota_policies p ON p.tenant_id=t.id')
            rows = conn.execute(query + (' WHERE t.id=?' if tenant_id is not None else '')
                                + ' ORDER BY t.id',
                                (tenant_id,) if tenant_id is not None else ()).fetchall()
        if tenant_id is not None and not rows:
            raise InvalidInput('unknown tenant')
        jobs = {}
        with core._connect() as conn:
            if conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                            " AND name='support_site_jobs'").fetchone():
                jobs = {r['tenant_id']: r['status'] for r in
                        conn.execute('SELECT tenant_id, status FROM support_site_jobs')}
        chosen = templates.all()
        values = [{'id': r['id'], 'host': r['site_host'],
                   'template': chosen.get(r['id'], templates.default),
                   'platform_host': r['platform_host'], 'enabled': bool(r['enabled']),
                   'site_job': jobs.get(r['id']),
                   'quota_mode': r['quota_mode'],
                   'policy': {'mode': r['mode'] or 'disabled', 'monthly_limit': r['monthly_limit']},
                   'metadata_only': True, 'provisioning': 'trusted_bootstrap_only'} for r in rows]
        return values if tenant_id is None else values[0]

    async def dispatch(request, operation, tenant_id=None):
        try:
            # Resolve dynamically and validate every request, even if adapter
            # accidentally returns a customer capability or a forged admin.
            actor = await app.state.support_actor_for(request)
            await run_in_threadpool(core._admin, actor)
            if request.scope.get('query_string'):
                raise InvalidInput('query parameters not accepted')
            if operation == 'read':
                value = await run_in_threadpool(summaries, actor, tenant_id)
            elif operation == 'templates':
                from support_site_template import available
                value = {'default': templates.default, 'templates': await run_in_threadpool(available)}
            elif operation == 'template':
                body = await strict_body(request, {'template': str})
                await run_in_threadpool(templates.set, actor, tenant_id, body['template'])
                value = {'metadata_only': True, 'template': body['template']}
            elif operation == 'create':
                # strict_body requires an exact key set; this endpoint permits
                # optional defaults, so decode once with equivalent edge rules.
                if request.headers.getlist('content-type') != ['application/json']:
                    raise InvalidInput('JSON required')
                data = bytearray()
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_BODY:
                        raise OverflowError
                    data.extend(chunk)
                def unique_object(pairs):
                    obj = {}
                    for key, item in pairs:
                        if key in obj:
                            raise InvalidInput('duplicate field')
                        obj[key] = item
                    return obj
                body = json.loads(data.decode('utf-8'), object_pairs_hook=unique_object,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                value = await run_in_threadpool(bootstrap.create, actor, body)
            elif operation == 'delete_tenant':
                # Destructive: require the caller to name what it is deleting,
                # so a stale page cannot remove a tenant it never displayed.
                body = await strict_body(request, {'expected_host': str})
                value = await run_in_threadpool(teardown.delete_tenant, actor, tenant_id,
                                                expected_host=body['expected_host'])
            elif operation == 'queue_site':
                body = await strict_body(request, {'client_request_id': str})
                result = await run_in_threadpool(site_jobs.queue, actor, tenant_id,
                                                body['client_request_id'])
                value = {'metadata_only': True, 'executor_connected': False, 'job': result}
            elif operation == 'tasks':
                value = {'templates': await run_in_threadpool(task_library.templates, actor)}
            elif operation == 'task_save':
                body = await strict_body(request, {'original': str, 'name': str, 'title': str,
                                                   'role': str, 'cron': str, 'skill': str,
                                                   'context': str, 'is_default': bool})
                original = body.pop('original')
                value = {'templates': await run_in_threadpool(
                    lambda: task_library.save_template(actor, body, original=original))}
            elif operation == 'task_delete':
                body = await strict_body(request, {'name': str})
                value = {'templates': await run_in_threadpool(task_library.delete_template,
                                                              actor, body['name'])}
            elif operation == 'deliveries':
                await run_in_threadpool(summaries, actor, tenant_id)
                value = {'deliveries': await run_in_threadpool(task_library.deliveries,
                                                               actor, tenant_id)}
            elif operation == 'push':
                body = await strict_body(request, {'names': list, 'mode': str})
                value = {'deliveries': await run_in_threadpool(
                    task_library.push, actor, tenant_id, body['names'], body['mode'])}
            elif operation == 'invite':
                body = await strict_body(request, {'ttl_seconds': int})
                ttl = body['ttl_seconds']
                if not MIN_INVITE_TTL <= ttl <= MAX_INVITE_TTL:
                    raise InvalidInput('invalid lifetime')
                token = await run_in_threadpool(core.issue_invite, actor, tenant_id, ttl=ttl)
                value = {'token': token, 'ttl_seconds': ttl, 'metadata_only': True,
                         'retry_safe': False}
            else:
                body = await strict_body(request, {'policy': dict})
                policy = body['policy']
                mode = policy.get('mode')
                if type(mode) is not str or mode not in ('disabled', 'unlimited', 'capped'):
                    raise InvalidInput('invalid policy')
                if set(policy) != ({'mode', 'monthly_limit'} if mode == 'capped' else {'mode'}):
                    raise InvalidInput('invalid policy fields')
                await run_in_threadpool(quota.set_policy, actor, tenant_id, mode,
                                        policy.get('monthly_limit'))
                value = {'metadata_only': True, 'policy': {
                    'mode': mode, 'monthly_limit': policy.get('monthly_limit')}}
            return JSONResponse(value, status_code=201 if operation == 'invite' else 200,
                                headers=SENSITIVE_RESPONSE_HEADERS)
        except OverflowError:
            return _error(413)
        except OfficeError as exc:
            return JSONResponse({'error': 'refused', 'message': str(exc)}, status_code=422,
                                headers=SENSITIVE_RESPONSE_HEADERS)
        except (ValueError, UnicodeError, InvalidInput):
            return _error(400)
        except Conflict:
            return _error(409)
        except Unauthorized:
            return _error(401)
        except SupportError:
            return _error(403)
        except Exception as exc:
            from support_site_teardown import TeardownError
            if isinstance(exc, TeardownError):
                # External resources may remain; say so instead of a bare 500.
                return JSONResponse({'error': 'teardown_incomplete'}, status_code=409,
                                    headers=SENSITIVE_RESPONSE_HEADERS)
            return _error(500)

    @app.post('/admin/tenants')
    async def create_tenant(request: Request):
        return await dispatch(request, 'create')

    @app.get('/admin/tenants')
    async def list_tenants(request: Request):
        return await dispatch(request, 'read')

    @app.get('/admin/site-templates')
    async def list_templates(request: Request):
        return await dispatch(request, 'templates')

    @app.post('/admin/tenants/{tenant_id}/template')
    async def set_template(tenant_id: str, request: Request):
        return await dispatch(request, 'template', tenant_id)

    @app.get('/admin/tenants/{tenant_id}')
    async def read_tenant(tenant_id: str, request: Request):
        return await dispatch(request, 'read', tenant_id)

    @app.post('/admin/tenants/{tenant_id}/invites')
    async def issue_invite(tenant_id: str, request: Request):
        return await dispatch(request, 'invite', tenant_id)

    @app.get('/admin/tasks')
    async def list_tasks(request: Request):
        return await dispatch(request, 'tasks')

    @app.post('/admin/tasks')
    async def save_task(request: Request):
        return await dispatch(request, 'task_save')

    @app.post('/admin/tasks/delete')
    async def delete_task(request: Request):
        return await dispatch(request, 'task_delete')

    @app.get('/admin/tenants/{tenant_id}/tasks')
    async def tenant_tasks(tenant_id: str, request: Request):
        return await dispatch(request, 'deliveries', tenant_id)

    @app.post('/admin/tenants/{tenant_id}/tasks')
    async def push_tasks(tenant_id: str, request: Request):
        return await dispatch(request, 'push', tenant_id)

    @app.post('/admin/tenants/{tenant_id}/quota')
    async def set_quota(tenant_id: str, request: Request):
        return await dispatch(request, 'quota', tenant_id)

    if enable_site_job_queue:
        @app.post('/admin/tenants/{tenant_id}/site-jobs')
        async def queue_site(tenant_id: str, request: Request):
            return await dispatch(request, 'queue_site', tenant_id)

    if teardown is not None:
        @app.post('/admin/tenants/{tenant_id}/delete')
        async def delete_tenant(tenant_id: str, request: Request):
            return await dispatch(request, 'delete_tenant', tenant_id)

    return app
