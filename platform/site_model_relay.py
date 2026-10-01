"""Starter-model relay: customer sites -> platform model gateway.

Customer DSH sites call this as an OpenAI-compatible endpoint with their own
site token. The relay authenticates the token, applies the tenant's monthly
budget (support_site_model.SiteModelAccess), and forwards to the platform
gateway with the PLATFORM key, which therefore never enters a site.

Only what a chat client needs crosses:

* ``GET /v1/models`` lists the starter models; ``POST /v1/chat/completions``
  forwards. Every other path is 404.
* Allowlisted request fields only. LiteLLM honours client-side routing fields
  (``api_base``, ``api_key``, ...) in some configurations; they are dropped here,
  so a site can never redirect the platform key.
* ``n`` is forced to 1, the output cap is clamped, and streaming requests
  always ask for usage so the ledger records real counts.
* Upstream error bodies are not relayed (they can echo request data); the site
  sees the status and a generic message.

Binds to the Docker bridge address (default 172.17.0.1:8212): reachable by site
containers and the host, not published through nginx.

Configuration:
  PLATFORM_DB               platform SQLite database (shared with platform_service)
  PLATFORM_MODEL_BASE_URL   upstream gateway, e.g. http://172.17.0.1:4000/v1
  PLATFORM_MODEL_KEY        platform gateway key
  SITE_MODEL_IDS            comma-separated starter models (default cloud-fast)
  SITE_MODEL_RELAY_HOST     bind address (default 172.17.0.1)
  SITE_MODEL_RELAY_PORT     bind port (default 8212)
"""
from __future__ import annotations

import ipaddress
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_TOKENS = 16384
DEFAULT_OUTPUT_TOKENS = 4096
ALLOWED_FIELDS = frozenset({
    'model', 'messages', 'stream', 'stream_options', 'max_tokens', 'max_completion_tokens',
    'temperature', 'top_p', 'stop', 'seed', 'presence_penalty', 'frequency_penalty',
    'tools', 'tool_choice', 'parallel_tool_calls', 'response_format', 'reasoning_effort',
    'store', 'n', 'user',
})
# Sites live on Docker bridge networks; nothing else should reach the relay.
TRUSTED_PEERS = (ipaddress.ip_network('172.16.0.0/12'), ipaddress.ip_network('127.0.0.0/8'))


def _error(status, code, message):
    from starlette.responses import JSONResponse

    return JSONResponse({'error': {'message': message, 'type': code, 'code': code}},
                        status_code=status, headers={'Cache-Control': 'no-store'})


def sanitize(body, models):
    """Return (clean_body, max_output) or raise ValueError(message)."""
    if not isinstance(body, dict):
        raise ValueError('request body must be a JSON object')
    if body.get('model') not in models:
        raise ValueError('model not available on the starter plan')
    if not isinstance(body.get('messages'), list) or not body['messages']:
        raise ValueError('messages are required')
    clean = {k: v for k, v in body.items() if k in ALLOWED_FIELDS}
    clean['n'] = 1
    requested = [v for v in (clean.get('max_completion_tokens'), clean.get('max_tokens'))
                 if type(v) is int and v > 0]
    max_output = min(requested[0] if requested else DEFAULT_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS)
    clean.pop('max_tokens', None)
    clean['max_completion_tokens'] = max_output
    if clean.get('stream') is True:
        clean['stream_options'] = {'include_usage': True}
    else:
        clean['stream'] = False
        clean.pop('stream_options', None)
    return clean, max_output


def usage_total(usage):
    if not isinstance(usage, dict):
        return None
    total = usage.get('total_tokens')
    if type(total) is int and total >= 0:
        return total
    parts = [usage.get('prompt_tokens'), usage.get('completion_tokens')]
    if all(type(p) is int and p >= 0 for p in parts):
        return parts[0] + parts[1]
    return None


class SseUsage:
    """Incrementally find the usage object in an OpenAI SSE stream."""

    def __init__(self):
        self._buffer = b''
        self.total = None

    def feed(self, chunk):
        self._buffer += chunk
        *lines, self._buffer = self._buffer.split(b'\n')
        if len(self._buffer) > 1024 * 1024:  # one runaway line: stop tracking it
            self._buffer = b''
        for line in lines:
            line = line.strip()
            if not line.startswith(b'data:'):
                continue
            data = line[5:].strip()
            if not data or data == b'[DONE]' or b'"usage"' not in data:
                continue
            try:
                total = usage_total(json.loads(data).get('usage'))
            except Exception:
                continue
            if total is not None:
                self.total = total


def create_app(access, *, base_url, api_key, models, transport=None, peer_check=None):
    import httpx
    from starlette.applications import Starlette
    from starlette.background import BackgroundTask
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse, Response, StreamingResponse
    from starlette.routing import Route

    from support_site_model import SiteModelDenied, estimate_tokens

    if not base_url.startswith(('http://', 'https://')) or not api_key:
        raise ValueError('upstream base url and key are required')
    models = tuple(models)
    if not models:
        raise ValueError('at least one starter model is required')
    upstream = base_url.rstrip('/') + '/chat/completions'
    client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                               timeout=httpx.Timeout(connect=10, read=300, write=60, pool=10))

    def peer_ok(request):
        if peer_check is not None:
            return peer_check(request.client.host if request.client else None)
        try:
            address = ipaddress.ip_address(request.client.host)
        except Exception:
            return False
        return any(address in network for network in TRUSTED_PEERS)

    def bearer(request):
        header = request.headers.get('authorization', '')
        return header[7:].strip() if header[:7].lower() == 'bearer ' else ''

    async def list_models(request):
        if not peer_ok(request):
            return _error(403, 'forbidden', 'forbidden')
        try:
            await run_in_threadpool(access.authenticate, bearer(request))
        except SiteModelDenied as denied:
            return _error(denied.status, denied.code, str(denied))
        return JSONResponse({'object': 'list', 'data': [
            {'id': m, 'object': 'model', 'owned_by': 'platform'} for m in models]},
            headers={'Cache-Control': 'no-store'})

    async def chat(request):
        if not peer_ok(request):
            return _error(403, 'forbidden', 'forbidden')
        declared = request.headers.get('content-length')
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
            return _error(413, 'request_too_large', 'request too large')
        raw = b''
        async for piece in request.stream():
            raw += piece
            if len(raw) > MAX_BODY_BYTES:
                return _error(413, 'request_too_large', 'request too large')
        try:
            clean, max_output = sanitize(json.loads(raw), models)
        except ValueError as exc:
            return _error(400, 'invalid_request', str(exc))
        except Exception:
            return _error(400, 'invalid_request', 'invalid JSON')
        try:
            tenant, request_id = await run_in_threadpool(
                access.admit, bearer(request), clean['model'], estimate_tokens(raw, max_output))
        except SiteModelDenied as denied:
            return _error(denied.status, denied.code, str(denied))

        async def settle(total):
            await run_in_threadpool(access.settle, tenant, request_id, total)

        headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json',
                   'Accept': 'text/event-stream' if clean['stream'] else 'application/json'}
        payload = json.dumps(clean).encode('utf-8')
        try:
            upstream_request = client.build_request('POST', upstream, content=payload, headers=headers)
            response = await client.send(upstream_request, stream=True)
        except Exception:
            await settle(0)  # Nothing reached the model.
            return _error(502, 'upstream_unavailable', 'starter model is unreachable')

        if response.status_code >= 400:
            await response.aclose()
            # Rejected before generation; nothing to bill.
            await settle(0)
            status = response.status_code if response.status_code in (400, 408, 413, 429) else 502
            return _error(status, 'upstream_error',
                          f'starter model refused the request (HTTP {response.status_code})')

        if not clean['stream']:
            try:
                body = await response.aread()
            finally:
                await response.aclose()
            try:
                total = usage_total(json.loads(body).get('usage'))
            except Exception:
                total = None
            await settle(total)
            return Response(body, media_type='application/json',
                            headers={'Cache-Control': 'no-store'})

        tracker = SseUsage()
        state = {'settled': False}

        async def finish():
            if not state['settled']:
                state['settled'] = True
                await response.aclose()
                await settle(tracker.total)

        async def relay():
            try:
                async for chunk in response.aiter_bytes():
                    tracker.feed(chunk)
                    yield chunk
            finally:
                await finish()

        return StreamingResponse(relay(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'},
                                 background=BackgroundTask(finish))

    async def not_found(request):
        return _error(404, 'not_found', 'not found')

    import contextlib

    @contextlib.asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await client.aclose()

    app = Starlette(routes=[
        Route('/v1/models', list_models, methods=['GET']),
        Route('/v1/chat/completions', chat, methods=['POST']),
        Route('/{path:path}', not_found, methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE']),
    ], lifespan=lifespan)
    return app


def _require(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise RuntimeError(f'{name} is required')
    return value


def build():
    from support_core import SupportCore
    from support_site_model import SiteModelAccess

    database = Path(_require('PLATFORM_DB'))
    if not database.is_absolute() or not database.exists():
        raise RuntimeError('PLATFORM_DB must be an existing absolute path')
    from support_quota import SupportQuota

    core = SupportCore(database)
    SupportQuota(core)  # owns support_quota_policies; ensures the schema exists
    access = SiteModelAccess(core)
    released = access.release_stale_holds()
    if released:
        print(f'[site-model-relay] {released} interrupted request(s) billed at their estimate',
              flush=True)
    models = [m.strip() for m in os.environ.get('SITE_MODEL_IDS', 'cloud-fast').split(',') if m.strip()]
    return create_app(access, base_url=_require('PLATFORM_MODEL_BASE_URL'),
                      api_key=_require('PLATFORM_MODEL_KEY'), models=models)


def main():
    import uvicorn

    app = build()
    uvicorn.run(app, host=os.environ.get('SITE_MODEL_RELAY_HOST', '172.17.0.1'),
                port=int(os.environ.get('SITE_MODEL_RELAY_PORT', '8212')),
                proxy_headers=False, server_header=False, date_header=False)


if __name__ == '__main__':
    main()
