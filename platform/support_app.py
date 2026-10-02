"""Isolated customer HTTP adapter; no admin imports, server or deployment hooks.

Rate limits are per-process, fixed-window, fail-closed at key capacity; deployers
need a shared limiter for multiple workers and must configure TLS independently.
Only the dedicated customer cookie conveys identity. Unrelated cookies (including
DSH cookies) may coexist but never authenticate customers; swarm_admin_session is
explicitly refused. Malformed cookies and duplicate customer credentials fail shut.
"""
from __future__ import annotations

import json
import ipaddress
import re
import time
from dataclasses import asdict

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from support_core import Conflict, SupportError
from support_http_policy import EdgePolicy, SENSITIVE_RESPONSE_HEADERS

COOKIE = '__Host-customer_session'
# Browsers refuse __Host- cookies without Secure. Plain-HTTP loopback fixtures use
# this separate name; production keeps __Host- and the name is never chosen by input.
LOOPBACK_COOKIE = 'customer_session_loopback_test'
LOOPBACK_ORIGIN = 'http://localhost:18204'
MAX_BODY = 16 * 1024


def loopback_customer_policy(public_origin):
    """Fixed-authority loopback policy; refuses anything but the single test origin."""
    if public_origin != LOOPBACK_ORIGIN:
        raise ValueError('loopback HTTP test mode is restricted to ' + LOOPBACK_ORIGIN)
    policy = EdgePolicy('https://localhost')
    policy.host = 'localhost:18204'
    policy.origin = public_origin
    return policy


def _error(status):
    return JSONResponse({'error': 'request rejected'}, status_code=status,
                        headers=SENSITIVE_RESPONSE_HEADERS)


class _Limiter:
    # Used only on the ASGI event loop, before any await: no thread races.
    def __init__(self, clock, limit, window, capacity):
        self.clock, self.limit, self.window, self.capacity = clock, limit, window, capacity
        self.entries = {}

    def allow(self, key):
        now = self.clock()
        self.entries = {k: v for k, v in self.entries.items() if now < v[0]}
        entry = self.entries.get(key)
        if entry is None:
            if len(self.entries) >= self.capacity:
                return False
            entry = (now + self.window, 0)
        if entry[1] >= self.limit:
            return False
        self.entries[key] = (entry[0], entry[1] + 1)
        return True


_LOOPBACK = frozenset({'127.0.0.1', '::1'})
CLIENT_HEADER = b'x-swarm-client'


def client_address(scope):
    """The address to rate-limit by.

    Behind the documented nginx every socket peer is 127.0.0.1, so the peer
    alone would put every visitor in one bucket. nginx overwrites
    X-Swarm-Client with $remote_addr, and only a loopback peer is trusted to
    send it. X-Forwarded-For, Forwarded and X-Real-IP are never read: clients
    can append to those.
    """
    client = scope.get('client')
    peer = client[0] if client else '<unknown>'
    if peer not in _LOOPBACK:
        return peer
    values = [v for k, v in scope.get('headers', ()) if k.lower() == CLIENT_HEADER]
    if len(values) != 1:
        return peer
    try:
        return str(ipaddress.ip_address(values[0].decode('ascii').strip()))
    except (UnicodeDecodeError, ValueError):
        return peer


def _cookie(headers, cookie_name=COOKIE):
    values = [v.decode('latin1') for k, v in headers if k.lower() == b'cookie']
    if not values:
        return None
    token = None
    for header in values:
        for pair in header.split(';'):
            # RFC 6265 token name and cookie-octet value, with optional quotes.
            match = re.fullmatch(r"[ \t]*([!#$%&'*+.^_`|~0-9A-Za-z-]+)=([\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*|\"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*\")[ \t]*", pair)
            # Other cookies are ignored, malformed or not: customer sites are
            # sibling subdomains and can set cookies on the parent domain, so
            # rejecting on them would let any site lock visitors out of the
            # platform. Only our own __Host- cookie (which a sibling cannot
            # set) is checked strictly.
            if not match:
                continue
            name, value = match.groups()
            if name == cookie_name:
                if token is not None or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value):
                    raise ValueError
                token = value
    return token


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class _Boundary:
    def __init__(self, app, policy, limiter, cookie_name=COOKIE, session_host=None):
        self.app, self.policy, self.limiter = app, policy, limiter
        # session_host maps a port-bearing loopback authority to the DNS host
        # that tenants/sessions are bound to; production leaves it None.
        self.cookie_name, self.session_host = cookie_name, session_host

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        response_started = False
        async def safe_send(message):
            nonlocal response_started
            if message['type'] == 'http.response.start':
                response_started = True
                protected = {k.lower().encode(): v.encode() for k, v in SENSITIVE_RESPONSE_HEADERS.items()}
                message['headers'] = [(k, v) for k, v in message.get('headers', [])
                                      if k.lower() not in protected] + list(protected.items())
            await send(message)
        try:
            self.policy.validate(scope['method'], scope['headers'])
            host = self.session_host or self.policy.host
            token = _cookie(scope['headers'], self.cookie_name)
        except (SupportError, ValueError):
            await _error(403)(scope, receive, safe_send)
            return
        scope.setdefault('state', {}).update(customer_host=host, customer_token=token)
        if scope['method'] == 'POST' and scope['path'] in ('/customer/login', '/customer/activate'):
            if not self.limiter.allow((client_address(scope), host)):
                await _error(429)(scope, receive, safe_send)
                return
        try:
            await self.app(scope, receive, safe_send)
        except Exception:
            # Do not propagate credential-bearing exceptions to server logging.
            if not response_started:
                await _error(500)(scope, receive, safe_send)


async def _body(request, fields):
    types = request.headers.getlist('content-type')
    if types != ['application/json']:
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
    if any(type(v) is not str for v in value.values()):
        raise ValueError
    if 'username' in value and not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value['username']):
        raise ValueError
    if 'password' in value:
        # Single source of truth: mirror the core bounds rather than repeat them.
        from support_core import MAX_PASSWORD_BYTES, MIN_PASSWORD_BYTES
        if not MIN_PASSWORD_BYTES <= len(value['password'].encode('utf-8')) <= MAX_PASSWORD_BYTES:
            raise ValueError
    if 'invite' in value and not re.fullmatch(r'[A-Za-z0-9_-]{43}', value['invite']):
        raise ValueError
    return value


def create_app(core, public_origin, *, clock=time.monotonic, rate_limit=10,
               rate_window=60.0, rate_capacity=4096, loopback_http_test=False):
    """Build a customer-only app. Clock/limiter knobs are trusted configuration.

    POST activate: {invite, username, password}; login: {username, password};
    logout: {}. GET me returns a principal, never session/invite credentials.
    Grants are transmitted ONLY in Secure HttpOnly Strict host-only cookies.
    """
    if (type(rate_limit) is not int or rate_limit <= 0 or
            type(rate_capacity) is not int or rate_capacity <= 0 or
            not isinstance(rate_window, (int, float)) or not 0 < rate_window < float('inf')):
        raise ValueError('invalid rate configuration')
    if loopback_http_test:
        policy = loopback_customer_policy(public_origin)
        cookie_name, secure, session_host = LOOPBACK_COOKIE, False, 'localhost'
    else:
        policy = EdgePolicy(public_origin)
        cookie_name, secure, session_host = COOKIE, True, None
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.router.redirect_slashes = False
    app.add_middleware(_Boundary, policy=policy, cookie_name=cookie_name, session_host=session_host,
                       limiter=_Limiter(clock, rate_limit, rate_window, rate_capacity))

    async def action(request, kind):
        try:
            fields = {'activate': ('invite', 'username', 'password'),
                      'login': ('username', 'password'), 'logout': ()}[kind]
            body = await _body(request, fields)
            host = request.state.customer_host
            if kind == 'activate':
                grant = await run_in_threadpool(core.redeem_invite, body['invite'], host,
                                               body['username'], body['password'])
            elif kind == 'login':
                grant = await run_in_threadpool(core.login, host, body['username'], body['password'])
            else:
                actor = await run_in_threadpool(core.authenticate_session, request.state.customer_token, host)
                await run_in_threadpool(core.logout, actor)
                response = JSONResponse({'ok': True})
                response.delete_cookie(cookie_name, path='/', secure=secure, httponly=True, samesite='strict')
                return response
            response = JSONResponse({'ok': True}, status_code=201 if kind == 'activate' else 200)
            response.set_cookie(cookie_name, grant.token, path='/', secure=secure, httponly=True, samesite='strict')
            return response
        except OverflowError:
            return _error(413)
        except (ValueError, UnicodeError):
            return _error(400)
        except Conflict:
            # The account already exists. Collapsing this into 401 told the
            # customer their password was wrong and left them guessing.
            return _error(409)
        except SupportError:
            return _error(401)

    @app.post('/customer/activate')
    async def activate(request: Request):
        return await action(request, 'activate')

    @app.post('/customer/login')
    async def login(request: Request):
        return await action(request, 'login')

    @app.post('/customer/logout')
    async def logout(request: Request):
        return await action(request, 'logout')

    @app.get('/customer/me')
    async def me(request: Request):
        try:
            actor = await run_in_threadpool(core.authenticate_session, request.state.customer_token,
                                            request.state.customer_host)
            principal = await run_in_threadpool(core.authorize_customer, actor)
            return JSONResponse(asdict(principal))
        except SupportError:
            return _error(401)

    return app
