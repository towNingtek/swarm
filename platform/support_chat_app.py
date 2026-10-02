"""Offline chat HTTP slice; no ambient credentials, provider or server wiring.

Customer routes extend support_app.create_app and retain its cookie/edge boundary.
Admin routes are a SEPARATE app on the mother-site origin. request_assertion is
trusted synchronous request -> assertion verification code, not a cookie name or
header convention. It must reject invalid credentials; core.admin_actor verifies
its result again. No production credential adapter is provided. Both default
admin authentication and model responses are disabled. Only an explicitly
injected, exact FakeModel is supported by this offline slice.
"""
import json

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from support_app import MAX_BODY, _error, _object, create_app
from support_core import Conflict, InvalidInput, SupportError, Unauthorized
from support_copilot import SupportCopilot
from support_quota import QuotaDenied, SupportQuota
from support_http_policy import EdgePolicy, SENSITIVE_RESPONSE_HEADERS
from support_model import FakeModel, ModelUnavailable


async def strict_body(request, fields):
    """Bounded exact JSON object; reject duplicates, coercion and extra fields."""
    if request.headers.getlist('content-type') != ['application/json']:
        raise ValueError
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BODY:
            raise OverflowError
        data.extend(chunk)
    value = json.loads(data.decode('utf-8'), object_pairs_hook=_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError
    if any(type(value[key]) is not kind for key, kind in fields.items()):
        raise ValueError
    if 'expected_epoch' in value and value['expected_epoch'] < 0:
        raise ValueError
    return value


class AdminBoundary:
    """Raw ASGI edge and redacted failures, including framework error responses."""
    def __init__(self, app, policy):
        self.app, self.policy = app, policy

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        started = False

        async def safe_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                protected = {k.lower().encode(): v.encode()
                             for k, v in SENSITIVE_RESPONSE_HEADERS.items()}
                message['headers'] = [(k, v) for k, v in message.get('headers', [])
                                      if k.lower() not in protected] + list(protected.items())
            await send(message)
        try:
            self.policy.validate(scope['method'], scope['headers'])
        except SupportError:
            await _error(403)(scope, receive, safe_send)
            return
        try:
            await self.app(scope, receive, safe_send)
        except Exception:
            if not started:
                await _error(500)(scope, receive, safe_send)


def _routes(app, core, rooms, prefix, actor_for, *, copilot=None, admin=False, notifier=None):
    def notify(kind, room_id, body=None):
        # Best effort and after the customer's action succeeded (or failed):
        # a notification problem must never change the HTTP result.
        if notifier is None or room_id is None:
            return
        try:
            context = rooms.notify_context(room_id)
            if context is None:
                return
            if kind == 'message':
                notifier.customer_message(context, body)
            else:
                notifier.ai_failed(context)
        except Exception:
            pass

    async def dispatch(request, operation, room_id=None):
        try:
            # Only a bounded replay cursor is accepted; never query credentials.
            after = 0
            if request.scope.get('query_string'):
                pairs = list(request.query_params.multi_items())
                if (operation != 'read' or len(pairs) != 1 or pairs[0][0] != 'after'
                        or not pairs[0][1].isascii() or not pairs[0][1].isdigit()
                        or len(pairs[0][1]) > 19):
                    raise ValueError
                after = int(pairs[0][1])
                if after > 2**63 - 1:
                    raise ValueError
            actor = await actor_for(request)
            if operation == 'list':
                value = await run_in_threadpool(rooms.list_rooms, actor)
            elif operation == 'overview':
                value = await run_in_threadpool(rooms.overview, actor)
            elif operation == 'reply':
                body = await strict_body(request, {'client_message_id': str, 'body': str})
                value = await run_in_threadpool(rooms.admin_reply, actor, room_id,
                                               body['client_message_id'], body['body'])
            elif operation == 'assist':
                body = await strict_body(request, {'instruction': str})
                await run_in_threadpool(rooms.read_room, actor, room_id)
                if copilot is None:
                    raise ModelUnavailable
                value = await run_in_threadpool(copilot.operator_respond, actor, room_id,
                                               body['instruction'])
            elif operation == 'read':
                value = await run_in_threadpool(rooms.read_room, actor, room_id, after=after)
            elif operation == 'post':
                body = await strict_body(request, {'client_message_id': str, 'body': str})
                value = await run_in_threadpool(rooms.post_message, actor, room_id,
                                               body['client_message_id'], body['body'])
                if not admin:
                    await run_in_threadpool(notify, 'message', room_id, body['body'])
            elif operation == 'control':
                body = await strict_body(request, {'mode': str, 'expected_epoch': int})
                value = await run_in_threadpool(rooms.set_mode, actor, room_id,
                                               body['mode'], expected_epoch=body['expected_epoch'])
            else:
                body = await strict_body(request, {'requested': bool})
                # Authorize the resource even when the model is disabled.
                await run_in_threadpool(rooms.read_room, actor, room_id)
                if copilot is None:
                    raise ModelUnavailable
                try:
                    value = await run_in_threadpool(copilot.respond, actor, room_id,
                                                   requested=body['requested'])
                except (Conflict, InvalidInput, Unauthorized, QuotaDenied):
                    raise
                except Exception:
                    # The model failed: the customer is left without an answer.
                    await run_in_threadpool(notify, 'ai_failed', room_id)
                    raise
            return JSONResponse(value)
        except OverflowError:
            return _error(413)
        except (ValueError, UnicodeError, InvalidInput):
            return _error(400)
        except Conflict:
            return _error(409)
        except Unauthorized:
            return _error(401)
        except SupportError:
            return _error(403)
        except ModelUnavailable:
            return _error(503)
        except Exception:
            return _error(500)

    @app.get(prefix + '/rooms')
    async def list_rooms(request: Request):
        return await dispatch(request, 'list')

    @app.get(prefix + '/rooms/{room_id}')
    async def read_room(room_id: str, request: Request):
        return await dispatch(request, 'read', room_id)

    @app.post(prefix + '/rooms/{room_id}/messages')
    async def post_message(room_id: str, request: Request):
        return await dispatch(request, 'post', room_id)

    if admin:
        @app.get(prefix + '/room-overview')
        async def overview(request: Request):
            return await dispatch(request, 'overview')

        @app.post(prefix + '/rooms/{room_id}/reply')
        async def reply(room_id: str, request: Request):
            return await dispatch(request, 'reply', room_id)

        @app.post(prefix + '/rooms/{room_id}/control')
        async def control(room_id: str, request: Request):
            return await dispatch(request, 'control', room_id)

        @app.post(prefix + '/rooms/{room_id}/assist')
        async def assist(room_id: str, request: Request):
            return await dispatch(request, 'assist', room_id)
    else:
        @app.post(prefix + '/rooms/{room_id}/respond')
        async def respond(room_id: str, request: Request):
            return await dispatch(request, 'respond', room_id)


def create_customer_chat_app(core, rooms, public_origin, *, model=None, model_catalog=(),
                             model_settings=None, site_credentials=None, site_entry=None,
                             site_password=None, onboarding_copilot=False, site_office=None,
                             task_library=None, site_scheduler=None, notifier=None,
                             **app_options):
    """GET rooms/read; POST messages {client_message_id,body}, respond {requested}.

    model_settings may be a trusted pre-built CustomerModelSettings sharing core
    (composition keeps any verifier capability; HTTP never obtains it).
    """
    if rooms.core is not core:
        raise ValueError('rooms must belong to core')
    # An approved provider adapter is now allowed alongside the offline fixture.
    # The allowlist stays exact-type: an arbitrary object that merely looks like
    # a model must not be accepted, and nothing here may be chosen implicitly.
    from support_model_gateway import GatewayModel
    if model is not None and type(model) not in (FakeModel, GatewayModel):
        raise ValueError('support model must be an explicit FakeModel or GatewayModel')
    from support_model_settings import CustomerModelSettings
    if model_settings is not None:
        if type(model_settings) is not CustomerModelSettings or model_settings.core is not core:
            raise ValueError('model settings must be a CustomerModelSettings sharing core')
        if model_catalog:
            raise ValueError('model_catalog is taken from the supplied model settings')
    app = create_app(core, public_origin, **app_options)

    async def customer(request):
        return await run_in_threadpool(core.authenticate_session,
                                       request.state.customer_token, request.state.customer_host)

    app.state.support_actor_for = customer
    from support_onboarding import install_onboarding
    from support_model_settings_http import install_model_settings
    settings = model_settings or CustomerModelSettings(core, catalog=model_catalog)
    install_onboarding(app, core, model_settings=settings, enable_copilot=onboarding_copilot,
                       office=site_office, tasks=task_library, scheduler=site_scheduler)
    install_model_settings(app, settings)
    if site_credentials is not None:
        from support_site_credentials import SiteCredentials
        from support_site_credentials_http import install_site_credentials
        if type(site_credentials) is not SiteCredentials or site_credentials.core is not core:
            raise ValueError('site credentials must be a SiteCredentials sharing core')
        install_site_credentials(app, site_credentials)
    if site_entry is not None:
        from support_site_entry import SiteEntry
        from support_site_credentials_http import install_site_entry
        if type(site_entry) is not SiteEntry or site_entry.core is not core:
            raise ValueError('site entry must be a SiteEntry sharing core')
        install_site_entry(app, site_entry)
    if site_password is not None:
        from support_site_password import SitePassword
        from support_site_credentials_http import install_site_password
        if type(site_password) is not SitePassword or site_password.core is not core:
            raise ValueError('site password must be a SitePassword sharing core')
        install_site_password(app, site_password)
    # Share the onboarding Copilot so the room assistant answers from the
    # customer's real state and can offer the allowed setup action.
    _routes(app, core, rooms, '/customer', customer, notifier=notifier,
            copilot=SupportCopilot(
                rooms, model,
                onboarding_copilot=getattr(app.state, 'support_onboarding_copilot', None),
                # A real provider spends money: charge the tenant's pool.
                quota=SupportQuota(core) if type(model) is GatewayModel else None,
            ) if model is not None else None)
    return app


def create_admin_chat_app(core, rooms, public_origin, *, request_assertion=None, model=None):
    """Separate mother-site app; no default cookie/header authentication.

    GET /admin/rooms and /admin/rooms/{id}; POST /messages and /control
    ({mode,expected_epoch}). Verifier must be synchronous, trusted and fail shut.
    It must not use query credentials; only a bounded `after` replay cursor is
    accepted on room reads. No customer activation/login or model endpoint is mounted.
    """
    if rooms.core is not core:
        raise ValueError('rooms must belong to core')
    if request_assertion is not None and not callable(request_assertion):
        raise ValueError('request assertion verifier must be callable')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.router.redirect_slashes = False
    app.add_middleware(AdminBoundary, policy=EdgePolicy(public_origin))

    async def administrator(request):
        if request_assertion is None:
            raise Unauthorized('administrator adapter disabled')
        try:
            assertion = await run_in_threadpool(request_assertion, request)
            if assertion is None or assertion is False:
                raise Unauthorized('administrator required')
            return await run_in_threadpool(core.admin_actor, assertion)
        except Exception:
            raise Unauthorized('administrator required') from None

    app.state.support_actor_for = administrator
    # Optional: lets the operator instruct the assistant (POST .../assist).
    from support_model_gateway import GatewayModel
    if model is not None and type(model) not in (FakeModel, GatewayModel):
        raise ValueError('support model must be an explicit FakeModel or GatewayModel')
    copilot = SupportCopilot(rooms, model) if model is not None else None
    _routes(app, core, rooms, '/admin', administrator, copilot=copilot, admin=True)
    return app
