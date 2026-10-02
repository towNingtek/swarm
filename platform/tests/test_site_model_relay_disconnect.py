"""A site that hangs up mid-stream must not leave its hold behind.

Real uvicorn servers on loopback: the relay, and a slow fake upstream. Before
the fix four disconnects locked the tenant out (four 'held' rows, then 429)
until the relay restarted.
"""
import asyncio
import json
import socket
import sqlite3
import tempfile
import threading
import time
import unittest

import uvicorn
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

from site_model_relay import create_app
from support_core import SupportCore
from support_quota import SupportQuota
from support_site_model import SiteModelAccess


def _port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class RelayDisconnectTests(unittest.TestCase):
    def test_disconnects_settle_their_holds(self):
        async def slow(request):
            async def gen():
                for _ in range(30):
                    yield b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'
                    await asyncio.sleep(1)
            return StreamingResponse(gen(), media_type='text/event-stream')

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        core = SupportCore(tmp.name + '/t.db', admin_verifier=lambda a: 'admin' if a == 'ok' else None)
        admin = core.admin_actor('ok')
        tenant = core.create_tenant(admin, 'ab.test')
        SupportQuota(core).set_policy(admin, tenant.id, 'capped', 10_000_000)
        access = SiteModelAccess(core, max_outstanding=2)
        token = access.issue(tenant.id)
        servers = []
        up, relay = _port(), _port()
        for app, port in ((Starlette(routes=[Route('/v1/chat/completions', slow, methods=['POST'])]), up),
                          (create_app(access, base_url=f'http://127.0.0.1:{up}/v1', api_key='sk-test-relay',
                                      models=['cloud-fast'], peer_check=lambda host: True), relay)):
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
            threading.Thread(target=server.run, daemon=True).start()
            servers.append(server)
        self.addCleanup(lambda: [setattr(s, 'should_exit', True) for s in servers])
        for _ in range(100):
            if all(s.started for s in servers):
                break
            time.sleep(0.05)
        body = json.dumps({'model': 'cloud-fast', 'stream': True,
                           'messages': [{'role': 'user', 'content': 'hi'}]})

        def hang_up_after_first_bytes():
            with socket.create_connection(('127.0.0.1', relay)) as c:
                c.sendall((f'POST /v1/chat/completions HTTP/1.1\r\nHost: relay\r\n'
                           f'Authorization: Bearer {token}\r\nContent-Type: application/json\r\n'
                           f'Content-Length: {len(body)}\r\n\r\n{body}').encode())
                return c.recv(200).split(b'\r\n')[0]

        def held():
            with sqlite3.connect(tmp.name + '/t.db') as db:
                return db.execute("SELECT count(*) FROM support_site_model_usage WHERE state='held'").fetchone()[0]

        for _ in range(4):  # twice the concurrency limit
            self.assertEqual(hang_up_after_first_bytes(), b'HTTP/1.1 200 OK')
            for _ in range(60):
                if held() == 0:
                    break
                time.sleep(0.1)
        self.assertEqual(held(), 0)
        with sqlite3.connect(tmp.name + '/t.db') as db:
            states = db.execute('SELECT state, actual > 0 FROM support_site_model_usage').fetchall()
        # Unknown usage is billed at the estimate, never as zero.
        self.assertEqual(states, [('unknown', 1)] * 4)


class StaleHoldTests(unittest.TestCase):
    def test_holds_older_than_any_request_stop_blocking(self):
        now = [1_000_000.0]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        core = SupportCore(tmp.name + '/t.db', clock=lambda: now[0],
                           admin_verifier=lambda a: 'admin' if a == 'ok' else None)
        admin = core.admin_actor('ok')
        tenant = core.create_tenant(admin, 'ab.test')
        SupportQuota(core).set_policy(admin, tenant.id, 'unlimited')
        access = SiteModelAccess(core, max_outstanding=1)
        token = access.issue(tenant.id)
        access.admit(token, 'cloud-fast', 100)
        with self.assertRaises(Exception):
            access.admit(token, 'cloud-fast', 100)
        now[0] += SiteModelAccess.HOLD_TTL + 1
        access.admit(token, 'cloud-fast', 100)
        self.assertEqual(access.month_usage(tenant.id), 200)


if __name__ == '__main__':
    unittest.main()
