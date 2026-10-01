"""Customer-only non-secret selection API. No browser probe completion endpoint."""
from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from support_app import _error
from support_chat_app import strict_body
from support_core import Conflict, InvalidInput, SupportError


def install_model_settings(app, settings):
    if not callable(getattr(app.state, 'support_actor_for', None)):
        raise ValueError('customer session adapter required')

    async def dispatch(request, operation):
        try:
            actor = await app.state.support_actor_for(request)
            if request.scope.get('query_string'):
                raise InvalidInput('query not accepted')
            if operation == 'status':
                value = await run_in_threadpool(settings.status, actor)
            elif operation == 'configure':
                body = await strict_body(request, {'selection_id':str, 'expected_revision':int})
                value = await run_in_threadpool(settings.configure, actor, body['selection_id'], body['expected_revision'])
            else:
                body = await strict_body(request, {'expected_revision':int})
                value = await run_in_threadpool(settings.revoke, actor, body['expected_revision'])
            return JSONResponse(value)
        except Conflict:
            return _error(409)
        except OverflowError:
            return _error(413)
        except (ValueError, InvalidInput, UnicodeError):
            return _error(400)
        except SupportError:
            return _error(401)

    @app.get('/customer/model-settings')
    async def status(request: Request):
        return await dispatch(request, 'status')

    @app.post('/customer/model-settings')
    async def configure(request: Request):
        return await dispatch(request, 'configure')

    @app.post('/customer/model-settings/revoke')
    async def revoke(request: Request):
        return await dispatch(request, 'revoke')
