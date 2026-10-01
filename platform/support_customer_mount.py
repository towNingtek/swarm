"""Invite-only customer path policy; NOT a combined DSH reverse proxy.

Public root ?token= belongs exclusively to PLATFORM invitations. Token shape,
value or cookies never select native DSH authentication. This helper neither
validates nor redeems an invitation: only authenticated activation machinery
may redeem it. Live composition remains forbidden pending a pinned, tokenless
native exchange; see docs/platform-native-handoff-contract.md.
"""
import re

from starlette.responses import PlainTextResponse
from support_http_policy import SENSITIVE_RESPONSE_HEADERS

_INVITE_QUERY = re.compile(rb'token=[A-Za-z0-9_-]{43}')


class CustomerSupportPath:
    def __init__(self, app, *, native_handoff=None):
        # A label claiming "tokenless" is not proof of an implemented exchange.
        # No native composition is currently implemented, including legacy URLs.
        if native_handoff is not None:
            raise ValueError('native DSH composition is not implemented; invite-only helper required')
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        path = scope.get('path', '')
        query = scope.get('query_string', b'')
        if path == '/' and query:
            # Canonical URL-safe invites need no percent decoding. Reject all
            # malformed/ambiguous encodings, duplicates and additional fields.
            # Do not normalize non-ASCII away or let parsing errors become 500s.
            if not isinstance(query, bytes) or not _INVITE_QUERY.fullmatch(query):
                response = PlainTextResponse('request rejected', status_code=400,
                                             headers=SENSITIVE_RESPONSE_HEADERS)
            elif scope.get('method') != 'GET':
                response = PlainTextResponse('method not allowed', status_code=405,
                                             headers={**SENSITIVE_RESPONSE_HEADERS, 'Allow': 'GET'})
            else:
                # Browser retains the original URL until WELCOME_JS scrubs it.
                # Neither HTML nor redirects nor downstream ASGI query receive
                # the invite. The edge access logger still needs query redaction.
                child_scope = dict(scope)
                child_scope['path'] = '/customer/welcome'
                child_scope['raw_path'] = b'/customer/welcome'
                child_scope['query_string'] = b''
                await self.app(child_scope, receive, send)
                return
            await response(scope, receive, send)
            return
        if path == '/':
            # Deliberate test-only signal. Never implement proxy fallback on an
            # arbitrary 404 response, and never retry rejected invites at DSH.
            response = PlainTextResponse('route to DSH', status_code=404,
                                         headers=SENSITIVE_RESPONSE_HEADERS)
            await response(scope, receive, send)
            return
        if path.startswith('/customer/'):
            await self.app(scope, receive, send)
            return
        response = PlainTextResponse('not found', status_code=404,
                                     headers=SENSITIVE_RESPONSE_HEADERS)
        await response(scope, receive, send)
