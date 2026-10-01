"""Operator console for the customer platform, served under /admin.

Deployed on the SAME origin as the customer platform (path-separated), so both
cookies live in one jar. Isolation therefore rests on things that are checked,
not on the origin boundary:

* Distinct cookie names. Each app reads only its own name, so an admin cookie
  never yields a customer session and vice versa (verified by test).
* The admin cookie is Path=/admin, so it is not even sent on customer requests.
* This console mounts no /customer/* routes at all.

It shares the platform's database, so tenants and invites created here are
immediately visible to the platform service. It does NOT provision: queuing a
job records intent, and the platform service (which holds the worker
capability) performs the real build.

Authentication is a single operator password supplied by the environment,
compared in constant time. Sessions are signed with a secret generated at
startup, so a restart invalidates every session.

Configuration:
  ADMIN_ORIGIN      https origin, normally the SAME as PLATFORM_ORIGIN
  ADMIN_PASSWORD    operator password (>= 16 chars)
  PLATFORM_DB       same database the platform service uses
  PLATFORM_ORIGIN   customer platform origin, used to build invite links
  ADMIN_PORT        loopback port (default 8211)
  ADMIN_PATH_PREFIX public path prefix (default /admin)
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _site_path  # noqa: E402,F401  (site/ on sys.path)

from fastapi import FastAPI, Form, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from support_core import SupportCore
from support_http_policy import SENSITIVE_RESPONSE_HEADERS
from support_rooms import SupportRooms


class OperatorSessions:
    """Signed, expiring session tokens. The secret dies with the process."""

    # __Host- requires Path=/, which would send this cookie on every customer
    # request too. A path-scoped name is the correct trade here: the console
    # shares an origin with the customer platform, so the cookie must be
    # confined to /admin rather than accompany every invite page load.
    COOKIE_NAME = 'platform_admin_session'
    COOKIE_PATH = '/admin'
    OPERATOR = 'platform-operator'
    TTL = 8 * 3600

    def __init__(self):
        self._secret = secrets.token_bytes(32)

    def issue(self):
        payload = f'{int(time.time()) + self.TTL}:{secrets.token_hex(16)}'
        signature = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        return payload + ':' + signature

    def verify_session(self, token):
        if not isinstance(token, str) or len(token) > 256:
            return False
        try:
            expiry, nonce, signature = token.split(':')
            expected = hmac.new(self._secret, f'{expiry}:{nonce}'.encode(),
                                hashlib.sha256).hexdigest()
            return int(expiry) > time.time() and hmac.compare_digest(expected, signature)
        except (ValueError, TypeError):
            return False

    # Shape expected by install_swarm_admin_support.
    ADMIN_USERNAME = OPERATOR


def _require(name, minimum=1):
    value = os.environ.get(name, '').strip()
    if len(value) < minimum:
        raise RuntimeError(f'{name} is required'
                           + (f' and must be at least {minimum} characters' if minimum > 1 else ''))
    return value


def _https_origin(name):
    origin = _require(name)
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or parsed.port is not None or parsed.username
            or parsed.path not in ('', '/') or not parsed.hostname):
        raise RuntimeError(f'{name} must be an HTTPS DNS origin without port or path')
    return 'https://' + parsed.hostname.lower()


def build():
    origin = _https_origin('ADMIN_ORIGIN')
    platform_origin = _https_origin('PLATFORM_ORIGIN')
    password = _require('ADMIN_PASSWORD', 16)
    db = Path(_require('PLATFORM_DB'))
    if not db.is_absolute():
        raise RuntimeError('PLATFORM_DB must be absolute')
    if not db.exists():
        raise RuntimeError('PLATFORM_DB does not exist; start the platform service first')
    prefix = os.environ.get('ADMIN_PATH_PREFIX', '/admin').rstrip('/') or '/admin'

    auth = OperatorSessions()
    core = SupportCore(db, admin_verifier=lambda v: auth.OPERATOR if v == auth.OPERATOR else None)
    rooms = SupportRooms(core)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.router.redirect_slashes = False
    login_path = prefix + '/login'

    from support_http_policy import EdgePolicy
    from support_core import SupportError

    edge = EdgePolicy(origin)

    # Temporary diagnostic: record why a request was rejected. Logs header NAMES
    # and the Sec-Fetch-*/Origin/Host values only - never cookies, credentials or
    # bodies. Enabled by ADMIN_DEBUG_EDGE=1 and meant to be removed afterwards.
    debug_edge = os.environ.get('ADMIN_DEBUG_EDGE') == '1'

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        try:
            edge.validate(request.method, request.scope['headers'])
        except SupportError as exc:
            if debug_edge:
                safe = {}
                for key, value in request.scope['headers']:
                    name = key.decode('latin1').lower()
                    if name in ('host', 'origin', 'referer', 'sec-fetch-site',
                                'sec-fetch-mode', 'sec-fetch-dest', 'sec-fetch-user'):
                        safe.setdefault(name, []).append(value.decode('latin1'))
                print(f'EDGE-REJECT {request.method} {request.scope["path"]} '
                      f'reason={exc} headers={safe} '
                      f'present={sorted({k.decode("latin1").lower() for k, _ in request.scope["headers"]})}',
                      flush=True)
            return JSONResponse({'error': 'rejected'}, status_code=403,
                                headers=SENSITIVE_RESPONSE_HEADERS)
        path = request.scope['path']
        if path == login_path or auth.verify_session(request.cookies.get(auth.COOKIE_NAME)):
            response = await call_next(request)
        else:
            response = RedirectResponse(login_path, status_code=302)
        response.headers.update(SENSITIVE_RESPONSE_HEADERS)
        return response

    @app.get(login_path, response_class=HTMLResponse)
    async def login_page():
        return HTMLResponse(
            '<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
            '<title>平台管理登入</title><h1>平台管理登入</h1>'
            f'<form method="post" action="{login_path}">'
            '<label>密碼 <input type="password" name="password" autocomplete="current-password">'
            '</label><button>登入</button></form></html>')

    @app.post(login_path)
    async def login_submit(supplied: str = Form('', alias='password')):
        if not hmac.compare_digest(supplied.encode(), password.encode()):
            return HTMLResponse('登入失敗', status_code=401)
        response = RedirectResponse(prefix + '/customers', status_code=303)
        response.set_cookie(auth.COOKIE_NAME, auth.issue(), max_age=auth.TTL,
                            httponly=True, secure=True, samesite='lax', path=prefix)
        return response

    # The domain customer sites are created under; the operator types only the
    # site name. Taken from the provisioning layer so the console cannot offer a
    # domain the executor would refuse.
    site_domain = os.environ.get('SWARM_DOMAIN', '').strip().lower()
    app.state.support_invite_origin = platform_origin
    from support_swarm_admin import install_swarm_admin_support

    # Teardown is destructive, so it is composed explicitly here rather than
    # being implied by the console existing.
    from support_site_teardown import SiteTeardown

    teardown = SiteTeardown(core, enabled=True) if os.environ.get('ADMIN_ALLOW_DELETE') == '1' else None

    # Optional assistant for 「指示 AI」 in the operator's chat popup. Same
    # explicit gateway values as the platform service; missing ones stop start-up.
    model = None
    if os.environ.get('PLATFORM_COPILOT', '').strip().lower() == 'gateway':
        from support_model_gateway import GatewayModel

        model = GatewayModel(_require('PLATFORM_MODEL_BASE_URL'), _require('PLATFORM_MODEL_KEY'),
                             _require('PLATFORM_MODEL_ID'))
    elif os.environ.get('PLATFORM_COPILOT', '').strip():
        raise RuntimeError("admin accepts PLATFORM_COPILOT=gateway or nothing")

    install_swarm_admin_support(app, core, rooms, origin, prefix=prefix + '-upstream',
                                public_prefix=prefix, session_auth=auth,
                                enable_site_job_queue=True, live=True,
                                platform_host=urlsplit(platform_origin).hostname,
                                site_domain=site_domain, teardown=teardown, model=model)
    return app


def main():
    import uvicorn

    app = build()
    # No access log: the console's responses and links carry invite tokens.
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('ADMIN_PORT', '8211')),
                reload=False, access_log=False, proxy_headers=False, server_header=False,
                date_header=False)


if __name__ == '__main__':
    main()
