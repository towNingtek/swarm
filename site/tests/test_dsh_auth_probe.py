"""Offline auth contract tests: no Docker, DNS or socket connections."""
import io
import json
import socket
import unittest
from email.message import Message
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.response import addinfourl

import dsh_sitectl as dsh

BASE = 'http://127.0.0.1:18000'
PASSWORD = 'secret-password'
TOKEN = 'secret-launch-token'
HTML = b'<html><head><script src="/auth/bootstrap.js"></script><script>window.__DSH_BOOT__={}</script></head></html>'


def response(code, body=b'', **headers):
    msg = Message()
    for key, value in headers.items():
        for item in value if isinstance(value, list) else [value]:
            msg.add_header(key.replace('_', '-'), item)
    return code, msg, body


def status(auth=False):
    return response(200 if auth else 401, json.dumps({
        'required': True, 'authenticated': auth, 'username': 'admin' if auth else None,
    }).encode(), Content_Type='application/json')


# Step roles in the scripted flow, so tests refer to meaning not position.
STEP = {
    'readiness': 0, 'anonymous_index': 1, 'anonymous_api': 2,
    'wrong_password': 3, 'wrong_status': 4, 'login': 5,
    'handoff_page': 6, 'handoff_script': 7, 'exchange': 8,
    'index': 9, 'status': 10, 'reload': 11,
}


def script():
    """The tokenless composition: login never hands a token to the browser.

    Login redirects to a fixed /auth/handoff page, which performs a same-origin
    POST exchange; the native cookie is minted server-side.
    """
    return [
        status(),
        response(302, Location='/auth/login?next=%2F'),
        response(401, b'{"error":"authentication_required"}'),
        response(401), status(),
        response(303, Location='/auth/handoff?next=%2F',
                 Set_Cookie='dsh_web_auth=web-session; Path=/; HttpOnly'),
        response(200, b'<!doctype html><title>handoff</title>'
                 b'<script defer src="/auth/handoff.js"></script>', Content_Type='text/html',
                 Content_Security_Policy="default-src 'none'; connect-src 'self'; "
                                         "script-src 'self'; frame-ancestors 'none'"),
        response(200, b"'use strict';", Content_Type='text/javascript'),
        response(204, Set_Cookie='native=native-session; Path=/; HttpOnly'),
        response(200, HTML, Content_Type='text/html'),
        status(True),
        response(200, HTML, Content_Type='text/html'),
    ]


class AuthProbeTests(unittest.TestCase):
    def setUp(self):
        self.guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.calls = []

    def probe(self, rows, upgrade_status=404, **kwargs):
        # Exercise the real urllib cookie processor and no-redirect handler;
        # replace only the HTTP wire operation, never create a listening server.
        calls = self.calls

        recorded = self.upgrades = []

        def fake_upgrade(self_transport, url, *, timeout=10):
            recorded.append(url)
            if isinstance(upgrade_status, Exception):
                raise upgrade_status
            return upgrade_status

        patcher = patch.object(dsh._DshHTTPTransport, 'upgrade_status', fake_upgrade)
        patcher.start()
        self.addCleanup(patcher.stop)

        def wire(handler, req):
            calls.append(req)
            self.assertGreater(req.timeout, 0)
            self.assertLessEqual(req.timeout, 5)
            if not rows:
                raise AssertionError('unexpected HTTP call')
            value = rows.pop(0)
            if isinstance(value, Exception):
                raise value
            code, headers, body = value
            result = addinfourl(io.BytesIO(body), headers, req.full_url, code)
            result.msg = 'mock'
            return result

        with patch('urllib.request.HTTPHandler.http_open', wire), \
                patch('urllib.request.HTTPSHandler.https_open', wire):
            dsh._dsh_self_test(18000, PASSWORD, retries=1, wait=0, **kwargs)

    def fails(self, rows, stage=None, **kwargs):
        with self.assertRaises(dsh.SiteError) as caught:
            self.probe(rows, **kwargs)
        message = str(caught.exception)
        self.assertNotIn(PASSWORD, message)
        self.assertNotIn(TOKEN, message)
        self.assertNotIn('web-session', message)
        if stage:
            self.assertIn(stage, message)

    def test_complete_login_and_cookie_handoff(self):
        rows = script()
        self.probe(rows)
        self.assertEqual(rows, [])
        self.assertEqual(len(self.calls), 12)
        wrong = parse_qs(self.calls[3].data.decode())
        good = parse_qs(self.calls[5].data.decode())
        self.assertNotEqual(wrong['password'], [PASSWORD])
        self.assertEqual(good['password'], [PASSWORD])
        self.assertEqual(good['username'], ['admin'])
        self.assertEqual(self.calls[5].get_header('Origin'), BASE)
        self.assertIsNone(self.calls[5].get_header('Cookie'))
        # The tokenless handoff page, carrying the web session.
        page = self.calls[STEP['handoff_page']]
        self.assertEqual(page.full_url, BASE + '/auth/handoff?next=%2F')
        self.assertIn('dsh_web_auth=web-session', page.get_header('Cookie'))
        # Its script is fetched separately: inline would be blocked by the CSP.
        self.assertEqual(self.calls[STEP['handoff_script']].full_url, BASE + '/auth/handoff.js')
        # The exchange itself: a POST with an empty body and no token.
        exchange = self.calls[STEP['exchange']]
        self.assertEqual(exchange.full_url, BASE + '/auth/native-session')
        self.assertEqual(exchange.get_method(), 'POST')
        self.assertEqual(exchange.data, b'{}')
        self.assertEqual(exchange.get_header('Origin'), BASE)
        self.assertEqual(exchange.get_header('Sec-fetch-site'), 'same-origin')
        # No request in the whole flow may carry a launch token.
        self.assertTrue(all(TOKEN not in req.full_url for req in self.calls))
        self.assertIn('native=native-session', self.calls[STEP['reload']].get_header('Cookie'))
        self.assertEqual(self.calls[STEP['reload']].full_url, BASE + '/')

    def test_bare_200_and_401_are_not_auth_success(self):
        for code in (200, 401):
            with self.subTest(code=code):
                self.fails([response(code)], 'readiness failed')

    def test_unprotected_page_or_api_rejected(self):
        for index in (1, 2):
            with self.subTest(index=index):
                rows = script()
                rows[index] = response(200, HTML, Content_Type='text/html')
                self.fails(rows, 'anonymous protection')

    def test_wrong_password_must_reject_and_remain_anonymous(self):
        for index, value in [(3, response(303, Location='/')), (3, response(429)), (4, status(True))]:
            rows = script()
            rows[index] = value
            self.fails(rows, 'wrong-password rejection')

    def test_good_password_denied_or_missing_cookie(self):
        for value in (response(401), response(303, Location='/')):
            rows = script()
            rows[5] = value
            self.fails(rows, 'password login')

    def test_redirects_never_leave_authority(self):
        for location in ('https://127.0.0.1:18000/', '//evil.invalid/?token=' + TOKEN,
                         'http://127.0.0.1:18001/', 'http://user@127.0.0.1:18000/',
                         '/\\evil.invalid/', '/\nsecret', ''):
            # 1 = anonymous redirect, 5 = login redirect. Index 6 is now the
            # handoff page (HTML, no Location), so it is not a redirect case.
            for index in (1, 5):
                with self.subTest(location=location, index=index):
                    rows = script()
                    code, headers, body = rows[index]
                    headers.replace_header('Location', location)
                    self.fails(rows)
                    self.assertTrue(all(urlsplit(req.full_url).netloc == '127.0.0.1:18000' for req in self.calls))

    def test_unauthenticated_websocket_fails_provisioning(self):
        """HTML alone never proved the site works: the app needs its socket."""
        for status in (401, 403):
            with self.subTest(status=status):
                with self.assertRaises(dsh.SiteError) as caught:
                    self.probe(script(), upgrade_status=status)
                message = str(caught.exception)
                # The message must name the status, or the failure is undiagnosable.
                self.assertIn(str(status), message)
                self.assertIn('websocket', message)
                # The redaction guarantee must survive this new path.
                for secret in (PASSWORD, TOKEN, 'web-session', 'native-session'):
                    self.assertNotIn(secret, message)
        # A route that simply does not exist is not an auth failure.
        self.probe(script(), upgrade_status=404)
        self.assertEqual(self.upgrades, [BASE + '/api/ws'])

    def test_a_broken_probe_is_not_reported_as_a_site_failure(self):
        """Regression: a stray ImportError in the upgrade check failed every
        build with 'websocket authorization', blaming a healthy site."""
        with self.assertRaises(dsh.SiteError) as caught:
            self.probe(script(), upgrade_status=ImportError('cannot import name Request'))
        message = str(caught.exception)
        self.assertIn('probe defect', message)
        self.assertIn('ImportError', message)
        self.assertNotIn(PASSWORD, message)

    def test_handoff_csp_must_allow_the_exchange(self):
        """A page whose CSP blocks its own fetch leaves the customer stuck."""
        for csp in ("default-src 'none'; script-src 'self'",      # no connect-src
                    "default-src 'none'; connect-src 'self'",      # no script-src
                    ""):
            with self.subTest(csp=csp):
                rows = script()
                page = rows[STEP['handoff_page']]
                page[1].replace_header('Content-Security-Policy', csp)
                self.fails(rows)

    def test_login_loop_native_401_fake_html_and_reload_failure(self):
        for index, value in [
            (STEP['handoff_page'], response(303, Location='/auth/login')),  # no session
            (STEP['handoff_page'], response(401)),
            (STEP['handoff_script'], response(401)),       # script refused
            (STEP['exchange'], response(401)),             # exchange refused
            (STEP['exchange'], response(503, b'{"error":"native_session_unsupported"}')),
            (STEP['exchange'], response(200)),             # must be exactly 204
            (STEP['index'], response(200, b'<html>login</html>', Content_Type='text/html')),
            (STEP['index'], response(200, HTML + b'<form action="/auth/login">',
                                     Content_Type='text/html')),
            (STEP['index'], response(200, HTML, Content_Type='application/json')),
            (STEP['status'], status(False)),
            (STEP['reload'], response(401)),
        ]:
            rows = script()
            rows[index] = value
            # Either handoff stage is acceptable; the point is that it fails.
            with self.subTest(index=index):
                with self.assertRaises(dsh.SiteError) as caught:
                    self.probe(rows)
                message = str(caught.exception)
                self.assertIn('authenticated evidence failed', message)
                for secret in (PASSWORD, TOKEN, 'web-session'):
                    self.assertNotIn(secret, message)

    def test_redirect_limit(self):
        rows = script()[:6] + [response(302, Location='/?token=' + TOKEN) for _ in range(6)]
        self.fails(rows, 'native handoff')

    def test_auth_disabled_fails_readiness(self):
        self.fails([response(401, b'{"required":false,"authenticated":false}',
                             Content_Type='application/json')], 'readiness failed')

    def test_exceptions_are_redacted(self):
        self.fails([TimeoutError(PASSWORD + TOKEN)], 'readiness failed')
        self.fails(script()[:6] + [TimeoutError(PASSWORD + TOKEN)], 'native handoff')

    def test_readiness_retry_does_not_repeat_bad_password(self):
        class Fake:
            def request(inner, method, url, **kwargs):
                self.calls.append(url)
                raise TimeoutError(TOKEN)
        with patch('time.sleep') as sleep:
            with self.assertRaises(dsh.SiteError):
                dsh._dsh_self_test(18000, PASSWORD, retries=2, wait=0, transport_factory=Fake)
        self.assertEqual(self.calls, [BASE + '/auth/status'] * 2)
        sleep.assert_called_once()

    def test_transport_body_limit(self):
        self.fails([response(401, b'x' * (1024 * 1024 + 1))], 'readiness failed')

    def test_cookie_domain_and_secure_rules_are_respected(self):
        rows = script()
        rows[5][1].replace_header('Set-Cookie',
                                 'dsh_web_auth=web-session; Path=/; Secure')
        # On plain HTTP the web cookie cannot be sent; the handoff page then
        # bounces back to login and the probe must refuse to continue.
        rows[STEP['handoff_page']] = response(303, Location='/auth/login')
        self.fails(rows, 'native handoff')
        self.assertIsNone(self.calls[STEP['handoff_page']].get_header('Cookie'))

    def test_public_https_origin_secure_cookie_handoff(self):
        public = 'https://probe.example'
        rows = script()
        for index in (STEP['login'], STEP['exchange']):
            headers = rows[index][1]
            headers.replace_header('Set-Cookie', headers['Set-Cookie'] + '; Secure')
        rows[STEP['login']][1].replace_header('Location', public + '/auth/handoff?next=%2F')
        self.probe(rows, base_url=public + '/')
        self.assertEqual(rows, [])
        for req in self.calls:
            self.assertEqual(urlsplit(req.full_url).scheme, 'https')
            self.assertEqual(req.get_header('Host'), 'probe.example')
        self.assertEqual(self.calls[5].get_header('Origin'), public)
        self.assertIn('dsh_web_auth=web-session',
                      self.calls[STEP['handoff_page']].get_header('Cookie'))
        # The final clean reload carries BOTH cookies over https.
        reload_call = self.calls[STEP['reload']]
        self.assertIn('dsh_web_auth=web-session', reload_call.get_header('Cookie'))
        self.assertIn('native=native-session', reload_call.get_header('Cookie'))
        self.assertEqual(reload_call.full_url, public + '/')

    def test_public_https_redirect_downgrade_or_authority_change(self):
        for location in ('http://probe.example/', 'https://other.example/',
                         'https://probe.example:444/', '//other.example/',
                         'https://user@probe.example/'):
            for index in (1, 5):
                with self.subTest(location=location, index=index):
                    self.calls.clear()
                    rows = script()
                    rows[index][1].replace_header('Location', location)
                    self.fails(rows, base_url='https://probe.example')
                    self.assertEqual(len(self.calls), index + 1)
                    self.assertTrue(all(req.full_url.startswith('https://probe.example/')
                                        for req in self.calls))

    def test_invalid_public_origin_rejected_before_transport(self):
        invalid = ('http://probe.example', 'https://127.0.0.1', 'https://localhost',
                   'https://probe.example/path', 'https://probe.example?',
                   'https://probe.example#', 'https://probe.example:0',
                   'https://probe.example:65536', 'https://probe.example:',
                   'https://user:' + PASSWORD + '@probe.example',
                   'https://probe.example\\evil', 'https://probe.example\n',
                   'https://[::1]', 'https://-bad.example', 123)
        for value in invalid:
            with self.subTest(value=value), patch.object(dsh, '_DshHTTPTransport') as factory:
                self.fails([], 'HTTPS DNS origin', base_url=value)
                factory.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_public_tls_error_is_redacted(self):
        import ssl
        self.fails([ssl.SSLCertVerificationError(PASSWORD + TOKEN)],
                   'readiness failed', base_url='https://probe.example')

    def test_https_transport_keeps_default_certificate_validation(self):
        import urllib.request
        transport = dsh._DshHTTPTransport()
        handler = next(item for item in transport.opener.handlers
                       if isinstance(item, urllib.request.HTTPSHandler))
        # None delegates to HTTPSConnection's default verified TLS context.
        self.assertIsNone(handler._context)
        self.assertIsNone(handler._check_hostname)

    def test_create_probe_uses_public_origin(self):
        # Inspect only call wiring without running create, allocation or any
        # lifecycle effects; dedicated lifecycle tests already mock this call.
        import ast
        import inspect
        source = ast.parse(inspect.getsource(dsh._cmd_create_locked))
        calls = [node for node in ast.walk(source) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == '_dsh_self_test']
        self.assertEqual(len(calls), 1)
        value = next(kw.value for kw in calls[0].keywords if kw.arg == 'base_url')
        self.assertEqual(eval(compile(ast.Expression(value), '<probe-wiring>', 'eval'),
                              {'trusted_host': 'probe.example'}), 'https://probe.example')

    def test_bounds(self):
        for kwargs in ({'retries': 0}, {'retries': 61}, {'wait': float('nan')}, {'wait': 31}):
            with self.assertRaises(dsh.SiteError):
                dsh._dsh_self_test(18000, PASSWORD, **kwargs)


if __name__ == '__main__':
    unittest.main()
