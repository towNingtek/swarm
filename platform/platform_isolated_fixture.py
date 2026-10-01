"""Explicitly started, loopback-bound ASGI fixture; never production auth.

Import creates a disposable app/DB, not a listener. Only PLATFORM_* configuration
is used. Session signing material is random per import and never persisted.
HTTP cookies are permitted only in the explicit localhost:18203 test mode.

Loopback mode also builds `customer_app` (tunnel URL http://localhost:18204)
sharing the same disposable DB, plus in-process SIMULATED site executor and
model verifier. Everything they produce is labelled simulated; no Docker, DNS,
nginx, DSH, or provider is ever contacted, and ready_for_work stays false.
"""
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from support_core import SupportCore, SupportError
from support_http_policy import SENSITIVE_RESPONSE_HEADERS
from support_rooms import SupportRooms
from support_swarm_admin import admin_edge_policy, install_swarm_admin_support


class FixtureSessions:
    """Disposable session issuer, independent of ambient ADMIN_* settings."""
    ADMIN_USERNAME = 'fixture-admin'
    COOKIE_NAME = 'platform_fixture_session'
    SESSION_TTL = 3600

    def __init__(self):
        self._secret = secrets.token_bytes(32)

    def issue_session(self):
        payload = f'{int(time.time()) + self.SESSION_TTL}:{secrets.token_hex(16)}'
        signature = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        return payload + ':' + signature

    def verify_session(self, token):
        if not isinstance(token, str) or len(token) > 256:
            return False
        try:
            expiry, nonce, signature = token.split(':')
            payload = expiry + ':' + nonce
            expected = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
            return int(expiry) > time.time() and hmac.compare_digest(expected, signature)
        except (ValueError, TypeError):
            return False


LOOPBACK_HTTP_TEST = os.environ.get('PLATFORM_LOOPBACK_HTTP_TEST') == '1'
# Real provisioning is OFF unless explicitly requested. When on, the fixture
# creates REAL DSH containers, DNS records and nginx configs that outlive it and
# must be deleted deliberately. Simulation remains the default everywhere.
REAL_PROVISIONING = os.environ.get('PLATFORM_REAL_PROVISIONING') == '1'
PLATFORM_HOST = os.environ.get('PLATFORM_FIXTURE_HOST', '')
FIXTURE_ORIGIN = 'http://localhost:18203' if LOOPBACK_HTTP_TEST else 'https://swarm-acceptance.test'
PUBLIC_LOGIN = '/admin/support/login'
PUBLIC_CUSTOMERS = '/admin/support/customers'
LOGIN_PATH = PUBLIC_LOGIN if LOOPBACK_HTTP_TEST else '/support/login'
HUB_PATH = '/admin/hub' if LOOPBACK_HTTP_TEST else '/hub'
password = os.environ.get('PLATFORM_FIXTURE_ADMIN_PASSWORD', '')
db_path = os.environ.get('PLATFORM_FIXTURE_DB', '')
if len(password) < 16:
    raise RuntimeError('PLATFORM_FIXTURE_ADMIN_PASSWORD must be a disposable secret of 16+ chars')
if not db_path or not Path(db_path).is_absolute():
    raise RuntimeError('PLATFORM_FIXTURE_DB must be an absolute disposable DB path outside the repo')
resolved_db = Path(db_path).resolve()
if Path(__file__).resolve().parents[1] in resolved_db.parents:
    raise RuntimeError('fixture DB must stay outside the repository')
if REAL_PROVISIONING and not LOOPBACK_HTTP_TEST:
    raise RuntimeError('real provisioning is only wired into the loopback fixture')
if REAL_PROVISIONING and not PLATFORM_HOST:
    raise RuntimeError('PLATFORM_FIXTURE_HOST is required when real provisioning is enabled')
resolved_db.parent.mkdir(parents=True, exist_ok=True)
auth = FixtureSessions()
core = SupportCore(resolved_db, admin_verifier=lambda value: value if value == auth.ADMIN_USERNAME else None)
rooms = SupportRooms(core)
edge_policy = admin_edge_policy(FIXTURE_ORIGIN, allow_loopback_http_test_origin=LOOPBACK_HTTP_TEST)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.router.redirect_slashes = False


@app.middleware('http')
async def auth_boundary(request: Request, call_next):
    # Apply the same strict raw-header boundary even to login, Hub and health.
    try:
        edge_policy.validate(request.method, request.scope['headers'])
    except SupportError:
        return JSONResponse({'error': 'rejected'}, status_code=403,
                            headers=SENSITIVE_RESPONSE_HEADERS)
    path = request.scope['path']
    if path == '/':
        response = RedirectResponse(PUBLIC_CUSTOMERS if auth.verify_session(
            request.cookies.get(auth.COOKIE_NAME)) else PUBLIC_LOGIN, status_code=302)
    elif path == LOGIN_PATH and request.scope.get('query_string'):
        response = JSONResponse({'error': 'query not accepted'}, status_code=400)
    elif path in (LOGIN_PATH, '/__fixture/health') or auth.verify_session(
            request.cookies.get(auth.COOKIE_NAME)):
        response = await call_next(request)
    else:
        # Public Location is explicit: nginx does not rewrite relative redirects
        # merely because proxy_pass strips a request prefix.
        response = RedirectResponse(PUBLIC_LOGIN, status_code=302)
    response.headers.update(SENSITIVE_RESPONSE_HEADERS)
    return response


@app.get(LOGIN_PATH, response_class=HTMLResponse)
async def login_page():
    return HTMLResponse('''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>隔離驗收登入</title><h1>隔離驗收登入</h1><form method="post" action="/admin/support/login"><label>測試密碼 <input type="password" name="password" autocomplete="current-password"></label><button>登入</button></form></html>''')


@app.post(LOGIN_PATH)
async def login_submit(password_input: str = Form('', alias='password')):
    if not hmac.compare_digest(password_input.encode(), password.encode()):
        return HTMLResponse('登入失敗', status_code=401)
    response = RedirectResponse(PUBLIC_CUSTOMERS, status_code=303)
    response.set_cookie(auth.COOKIE_NAME, auth.issue_session(), max_age=auth.SESSION_TTL,
                        httponly=True, secure=not LOOPBACK_HTTP_TEST, samesite='lax', path='/')
    return response


@app.get('/__fixture/health')
async def health():
    return {'ok': True, 'fixture': 'isolated-only'}


@app.get(HUB_PATH)
async def hub_placeholder():
    return HTMLResponse('<!doctype html><title>Legacy route fixture</title><p>legacy Hub route remains reachable</p>')


install_swarm_admin_support(app, core, rooms, FIXTURE_ORIGIN,
                           allow_loopback_http_test_origin=LOOPBACK_HTTP_TEST,
                           session_auth=auth, enable_site_job_queue=LOOPBACK_HTTP_TEST,
                           # Loopback customers activate on 'localhost'; a real
                           # platform deployment passes its own DNS host.
                           platform_host=(PLATFORM_HOST or 'localhost') if LOOPBACK_HTTP_TEST else None)

customer_app = None
site_executor = None
if LOOPBACK_HTTP_TEST:
    from support_app import LOOPBACK_ORIGIN
    from support_chat_app import create_customer_chat_app
    from support_model import FakeModel
    from support_model_settings import CustomerModelSettings
    from support_simulated_runners import (SimulatedModelVerifier, SimulatedSiteExecutor,
                                           install_simulated_model_probe)
    from support_site_jobs import SupportSiteJobs
    from support_ui import install_customer_ui

    # Opaque capabilities live only in this module; no request can reach them.
    _verifier_capability, _worker_capability, _recovery_capability = object(), object(), object()
    model_settings = CustomerModelSettings(core, catalog=('fake-model-a', 'fake-model-b'),
                                           verifier_capability=_verifier_capability)
    from support_site_credentials import SiteCredentials

    _credential_capability = object()
    site_credentials = SiteCredentials(core, writer_capability=_credential_capability)
    customer_app = create_customer_chat_app(core, rooms, LOOPBACK_ORIGIN, model=FakeModel(),
                                            model_settings=model_settings,
                                            site_credentials=site_credentials,
                                            loopback_http_test=True)
    install_customer_ui(customer_app)
    install_simulated_model_probe(customer_app, SimulatedModelVerifier(
        model_settings, _verifier_capability, passing_selections=('fake-model-a',)))
    _jobs = SupportSiteJobs(core, worker_capability=_worker_capability,
                            recovery_capability=_recovery_capability)
    if REAL_PROVISIONING:
        # Real containers, DNS and proxy entries. Distinct class and distinct
        # terminal state (succeeded_provisioned), never simulation.
        from support_site_executor import SiteProvisioner

        site_executor = SiteProvisioner(_jobs, _worker_capability, enabled=True,
                                        credentials=site_credentials,
                                        credential_capability=_credential_capability)
    else:
        site_executor = SimulatedSiteExecutor(_jobs, _worker_capability)

    @customer_app.get('/__fixture/health')
    async def customer_health():
        return {'ok': True, 'fixture': 'isolated-only', 'role': 'customer',
                'simulated': not REAL_PROVISIONING,
                'real_provisioning': REAL_PROVISIONING}


def _run_site_executor(stop):
    # Real provisioning takes minutes per site, so poll slowly and never overlap
    # runs. A failure here is already fenced to reconciliation_required by the
    # job store; it must never crash the fixture or trigger an automatic retry.
    interval = 10.0 if REAL_PROVISIONING else 2.0
    while not stop.wait(interval):
        try:
            site_executor.run_once()
        except Exception:
            pass


if __name__ == '__main__':
    import asyncio
    import threading
    import uvicorn

    async def serve():
        admin = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8203, reload=False))
        tasks = [asyncio.create_task(admin.serve())]
        if customer_app is not None:
            customer = uvicorn.Server(uvicorn.Config(customer_app, host='127.0.0.1', port=8204, reload=False))
            tasks.append(asyncio.create_task(customer.serve()))
        await asyncio.gather(*tasks)

    stop = threading.Event()
    if site_executor is not None:
        threading.Thread(target=_run_site_executor, args=(stop,), daemon=True).start()
    try:
        asyncio.run(serve())
    finally:
        stop.set()
