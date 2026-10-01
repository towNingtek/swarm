"""Mount the isolated support-admin ASGI app inside swarm-admin.

The public nginx location `/admin/` strips that prefix before proxying to the
swarm-admin process. Mount this adapter at `/support` internally, yielding the
public URL `/admin/support/...` without replacing any legacy Hub routes. The
existing auth middleware should wrap the parent app; this adapter independently
verifies the same signed session before dispatching.
"""
import auth as swarm_auth
from fastapi import Request
from starlette.responses import RedirectResponse, Response
from support_chat_app import AdminBoundary, create_admin_chat_app
from support_core import SupportError
from support_management import install_management
from support_management_ui import install_management_ui
from support_quota import SupportQuota
from support_http_policy import SENSITIVE_RESPONSE_HEADERS
from support_http_policy import EdgePolicy


UPSTREAM_PREFIX = '/support'  # nginx strips external /admin/ before upstream
PUBLIC_PREFIX = '/admin/support'  # browser-visible path on the admin origin


def admin_edge_policy(public_origin, *, allow_loopback_http_test_origin=False):
    """Strict production policy, or explicit fixed-authority local fixture policy."""
    if not allow_loopback_http_test_origin:
        return EdgePolicy(public_origin)
    if public_origin != 'http://localhost:18203':
        raise ValueError('loopback HTTP test mode is restricted to http://localhost:18203')
    # Reuse the complete duplicate-preserving Host/Origin/Sec-Fetch policy.
    # No request headers are rewritten or manufactured.
    policy = EdgePolicy('https://localhost')
    policy.host = 'localhost:18203'
    policy.origin = public_origin
    return policy


def install_swarm_admin_support(app, core, rooms, public_origin, *, prefix=UPSTREAM_PREFIX,
                                public_prefix=PUBLIC_PREFIX,
                                allow_loopback_http_test_origin=False, session_auth=None,
                                enable_site_job_queue=False, platform_host=None, live=False,
                                site_domain='', teardown=None, model=None):
    # Trusted composition dependency, never a request-selectable auth provider.
    session_auth = swarm_auth if session_auth is None else session_auth
    if getattr(app.state, 'swarm_admin_support_installed', False):
        raise ValueError('support adapter already installed')
    if rooms.core is not core:
        raise ValueError('rooms must belong to core')
    if (not isinstance(prefix, str) or not prefix.startswith('/') or prefix == '/' or
            prefix.endswith('/') or '//' in prefix or '?' in prefix or '#' in prefix):
        raise ValueError('prefix must be an absolute non-root path without trailing slash')

    def assertion(request):
        if not session_auth.verify_session(request.cookies.get(session_auth.COOKIE_NAME)):
            return None
        return session_auth.ADMIN_USERNAME

    edge_policy = admin_edge_policy(public_origin,
                                    allow_loopback_http_test_origin=allow_loopback_http_test_origin)
    support = create_admin_chat_app(
        core, rooms, 'https://localhost' if allow_loopback_http_test_origin else public_origin,
        request_assertion=assertion, model=model)
    if allow_loopback_http_test_origin:
        # The factory remains production-HTTPS-only. Override only this private,
        # not-yet-served child's policy; keep AdminBoundary and raw headers intact.
        boundaries = [m for m in support.user_middleware if m.cls is AdminBoundary]
        if len(boundaries) != 1 or support.middleware_stack is not None:
            raise ValueError('unexpected support edge composition')
        boundaries[0].kwargs['policy'] = edge_policy
    quota = SupportQuota(core)
    install_management(support, core, rooms, quota, enable_site_job_queue=enable_site_job_queue,
                       platform_host=platform_host, teardown=teardown)
    # The old standalone support console is retired: conversations live in
    # 客戶管理. Relative Location so it resolves under any public prefix.
    async def retired_support_page(request: Request):
        return RedirectResponse('customers#chat-section', status_code=303,
                                headers=SENSITIVE_RESPONSE_HEADERS)
    support.add_api_route('/admin/support', retired_support_page, methods=['GET'],
                          include_in_schema=False)
    # Propagate the invite origin so the child UI builds links for the authority
    # customers actually reach, not this console's own host.
    support.state.support_invite_origin = getattr(app.state, 'support_invite_origin', '')
    install_management_ui(support, core, loopback_fixture=allow_loopback_http_test_origin,
                          live=live, site_domain=site_domain)

    async def dispatch(request: Request):
        if not session_auth.verify_session(request.cookies.get(session_auth.COOKIE_NAME)):
            return Response('unauthorized', status_code=401)
        public_path = request.scope.get('path', '')
        # Behind nginx the path is `/support/...`; for a direct localhost SSH
        # tunnel the fixture can opt into `/admin/support/...` without nginx.
        active_prefix = prefix if public_path == prefix or public_path.startswith(prefix + '/') else None
        # The public prefix is served directly when nginx does not strip it:
        # the loopback fixture (SSH tunnel) and the standalone live console.
        if (allow_loopback_http_test_origin or live) and (
                public_path == public_prefix or public_path.startswith(public_prefix + '/')):
            active_prefix = public_prefix
        if active_prefix is None:
            return Response('not found', status_code=404)
        if public_path == active_prefix:
            inner_path = '/admin'
        else:
            # The child UI/API routes live under `/admin`; the adapter mount
            # prefix itself is independent of that child route prefix.
            inner_path = '/admin' + public_path[len(active_prefix):]

        try:
            edge_policy.validate(request.method, request.scope['headers'])
        except SupportError:
            return Response('request rejected', status_code=403)
        scope = dict(request.scope)
        scope['path'] = inner_path
        scope['raw_path'] = inner_path.encode('utf-8')
        scope['root_path'] = ''
        sent = []
        async def capture(message):
            sent.append(message)
        await support(scope, request.receive, capture)
        start = next((m for m in sent if m['type'] == 'http.response.start'), None)
        body = b''.join(m.get('body', b'') for m in sent if m['type'] == 'http.response.body')
        if start is None:
            return Response(status_code=500)
        headers = [(k.decode('latin1'), v.decode('latin1'))
                   for k, v in start.get('headers', [])
                   if k.lower() != b'content-length']
        content_type = next((v for k,v in headers if k.lower() == 'content-type'), '')
        if any(kind in content_type for kind in ('text/html', 'javascript', 'text/css')):
            text = body.decode('utf-8')
            text = text.replace('/admin/', public_prefix + '/')
            body = text.encode('utf-8')
        response = Response(body, status_code=start['status'])
        # Preserve duplicate headers (notably Set-Cookie) and the support app's
        # no-store/referrer/CSP policy while using recalculated body length.
        response.raw_headers = [(k.lower().encode('latin1'), v.encode('latin1'))
                                for k,v in headers if k.lower() != 'content-length']
        response.raw_headers.append((b'content-length', str(len(body)).encode('ascii')))
        return response

    async def endpoint(request: Request, rest: str = ''):
        return await dispatch(request)

    if any(getattr(route, 'path', None) == prefix + '/{rest:path}' for route in app.routes):
        raise ValueError('support mount prefix conflicts with existing route')
    app.add_api_route(prefix + '/{rest:path}', endpoint,
                      methods=['GET', 'POST', 'HEAD'], include_in_schema=False)
    app.add_api_route(prefix, endpoint, methods=['GET', 'POST', 'HEAD'], include_in_schema=False)
    if allow_loopback_http_test_origin or live:
        # Skip paths the host app already owns (e.g. its own login form), so the
        # mount never shadows an existing route.
        owned = {getattr(route, 'path', None) for route in app.routes}
        for path in (public_prefix + '/{rest:path}', public_prefix):
            if path not in owned:
                app.add_api_route(path, endpoint, methods=['GET', 'POST', 'HEAD'],
                                  include_in_schema=False)
    app.state.swarm_admin_support_installed = True
    app.state.support_core = core
    app.state.support_rooms = rooms
    app.state.support_quota = quota
    app.state.support_public_prefix = public_prefix
    return app
