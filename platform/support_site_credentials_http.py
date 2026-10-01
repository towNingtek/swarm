"""Customer routes for collecting a provisioned site's one-time password.

GET  /customer/site-credential   -> whether a secret is waiting (never the value)
POST /customer/site-credential/reveal {} -> the value, ONCE; reading deletes it

The reveal route is POST because it is destructive: a prefetch, a refresh or a
crawler must not be able to consume the customer's only copy.
"""
from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from support_app import _error
from support_chat_app import strict_body
from support_core import Conflict, InvalidInput, SupportError


def install_site_entry(app, entry):
    """POST /customer/site-entry -> {url} for a one-time redirect into the site.

    POST because it mints a credential; a prefetch must not burn a ticket.
    """
    if not callable(getattr(app.state, 'support_actor_for', None)):
        raise ValueError('customer session adapter required')
    if getattr(app.state, 'support_site_entry_installed', False):
        raise ValueError('site entry already installed')

    @app.post('/customer/site-entry')
    async def site_entry(request: Request):
        try:
            actor = await app.state.support_actor_for(request)
            if request.scope.get('query_string'):
                raise InvalidInput('query not accepted')
            await strict_body(request, {})
            url = await run_in_threadpool(entry.entry_url, actor)
            return JSONResponse({'url': url})
        except Conflict:
            return _error(409)
        except OverflowError:
            return _error(413)
        except (ValueError, InvalidInput, UnicodeError):
            return _error(400)
        except SupportError:
            return _error(401)

    app.state.support_site_entry_installed = True
    return app


def install_site_credentials(app, credentials):
    if not callable(getattr(app.state, 'support_actor_for', None)):
        raise ValueError('customer session adapter required')
    if getattr(app.state, 'support_site_credentials_installed', False):
        raise ValueError('site credentials already installed')

    async def dispatch(request, reveal):
        try:
            actor = await app.state.support_actor_for(request)
            if request.scope.get('query_string'):
                raise InvalidInput('query not accepted')
            if reveal:
                await strict_body(request, {})
                value = await run_in_threadpool(credentials.reveal, actor)
            else:
                value = await run_in_threadpool(credentials.peek, actor)
            return JSONResponse(value)
        except Conflict:
            return _error(409)
        except OverflowError:
            return _error(413)
        except (ValueError, InvalidInput, UnicodeError):
            return _error(400)
        except SupportError:
            return _error(401)

    @app.get('/customer/site-credential')
    async def peek(request: Request):
        return await dispatch(request, False)

    @app.post('/customer/site-credential/reveal')
    async def reveal(request: Request):
        return await dispatch(request, True)

    app.state.support_site_credentials_installed = True
    return app


async def _one_password_field(request):
    """Exact JSON object with exactly one of password / platform_password (str)."""
    import json
    from support_chat_app import MAX_BODY, _object

    if request.headers.getlist('content-type') != ['application/json']:
        raise ValueError
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BODY:
            raise OverflowError
        data.extend(chunk)
    value = json.loads(data.decode('utf-8'), object_pairs_hook=_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError
    (key, item), = value.items()
    if key not in ('password', 'platform_password') or type(item) is not str:
        raise ValueError
    return {key: item}


def install_site_password(app, site_password):
    """POST /customer/site-password -> set the customer's own site login password.

    Body is exactly one of {"password": str} or {"platform_password": str}.
    Responds with {site_host, username}; never echoes the password.
    """
    from support_site_password import SitePasswordBusy

    if not callable(getattr(app.state, 'support_actor_for', None)):
        raise ValueError('customer session adapter required')
    if getattr(app.state, 'support_site_password_installed', False):
        raise ValueError('site password already installed')

    @app.get('/customer/site-password')
    async def site_password_available(request: Request):
        # Lets the page show the form only where the route is really wired.
        try:
            await app.state.support_actor_for(request)
        except SupportError:
            return _error(401)
        return JSONResponse({'available': True})

    @app.post('/customer/site-password')
    async def set_site_password(request: Request):
        try:
            actor = await app.state.support_actor_for(request)
            if request.scope.get('query_string'):
                raise InvalidInput('query not accepted')
            kwargs = await _one_password_field(request)
            return JSONResponse(await run_in_threadpool(lambda: site_password.set(actor, **kwargs)))
        except SitePasswordBusy:
            return _error(429)
        except Conflict:
            return _error(409)
        except OverflowError:
            return _error(413)
        except (ValueError, InvalidInput, UnicodeError):
            return _error(400)
        except SupportError:
            return _error(401)
        except Exception:
            # Controller failure: the site was restored or needs an operator.
            # Never surface its message; it may name paths or containers.
            return _error(502)

    app.state.support_site_password_installed = True
    return app
