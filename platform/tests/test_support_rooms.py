"""Pure unittest fixtures: isolated SQLite, no network or service dependencies."""
import dataclasses
import secrets
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from support_core import SupportCore, AdminActor, Conflict, InvalidInput, Unauthorized
from support_rooms import SupportRooms


class RoomsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'support.sqlite')
        self.core = SupportCore(self.path, admin_verifier=lambda x: 'operator-17' if x == 'trusted' else None)
        self.admin = self.core.admin_actor('trusted')
        self.rooms = SupportRooms(self.core)
        self.tenants, self.grants = [], []
        for host in ('one.test', 'two.test'):
            tenant = self.core.create_tenant(self.admin, host)
            invite = self.core.issue_invite(self.admin, tenant.id)
            self.tenants.append(tenant)
            self.grants.append(self.core.redeem_invite(invite, host, 'customer', 'long-password-123'))
        self.customer, self.other = [g.actor for g in self.grants]
        self.room = self.rooms.create_room(self.admin, self.tenants[0].id)['id']

    def race(self, *calls):
        barrier = threading.Barrier(len(calls))
        def invoke(call):
            barrier.wait()
            try:
                return call()
            except (Conflict, Unauthorized) as error:
                return error
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return list(pool.map(invoke, calls))

    def test_admin_reply_keeps_mode_and_discards_inflight_run(self):
        self.rooms.post_message(self.customer, self.room, 'c1', '請問怎麼設定？')
        token = self.rooms.start_run(self.customer, self.room, requested=True)
        result = self.rooms.admin_reply(self.admin, self.room, 'a1', '我來幫你')
        self.assertEqual(result['message']['sender_kind'], 'human')
        # Only the operator's explicit /control changes who answers.
        self.assertEqual((result['room']['mode'], result['room']['epoch']), ('ai', 0))
        # The AI answer started before the operator replied must not land after it.
        self.assertIsNone(self.rooms.finish_run(token, 'AI 搶答'))
        # Still 'ai': the customer's next message is answered on its own.
        self.rooms.post_message(self.customer, self.room, 'c2', '好的謝謝')
        token = self.rooms.start_run(self.customer, self.room, requested=False)
        self.assertEqual(self.rooms.finish_run(token, '不客氣')['sender_kind'], 'ai')
        again = self.rooms.admin_reply(self.admin, self.room, 'a1', '我來幫你')
        self.assertEqual(again['message']['id'], result['message']['id'])
        self.rooms.set_mode(self.admin, self.room, 'human', expected_epoch=0)
        self.assertEqual(self.rooms.admin_reply(self.admin, self.room, 'a2', '還在')['room']['mode'], 'human')
        for actor in (self.customer, self.other, object()):
            with self.assertRaises(Unauthorized):
                self.rooms.admin_reply(actor, self.room, 'x', 'no')
        with self.assertRaises(InvalidInput):
            self.rooms.admin_reply(self.admin, self.room, 'run:forged', 'no')

    def test_operator_run_is_admin_only_private_and_works_in_every_mode(self):
        self.rooms.post_message(self.customer, self.room, 'c1', '怎麼申請 OpenAI key？')
        for actor in (self.customer, self.other, object()):
            with self.assertRaises(Unauthorized):
                self.rooms.start_operator_run(actor, self.room, '說明步驟')
        with self.assertRaises(InvalidInput):
            self.rooms.start_operator_run(self.admin, self.room, '   ')
        self.rooms.set_mode(self.admin, self.room, 'human', expected_epoch=0)
        token, history = self.rooms.start_operator_run(self.admin, self.room, '說明步驟')
        self.assertEqual([m['body'] for m in history], ['怎麼申請 OpenAI key？'])
        # One run per room, operator or not.
        with self.assertRaises(Conflict):
            self.rooms.start_operator_run(self.admin, self.room, '再一次')
        message = self.rooms.finish_run(token, '到 platform.openai.com 申請。')
        self.assertEqual(message['sender_kind'], 'ai')
        # The instruction is an internal note: never in the customer's view.
        public = self.rooms.read_room(self.customer, self.room)
        self.assertNotIn('說明步驟', str(public))
        self.assertIn('說明步驟', self.rooms.list_notes(self.admin, self.room)[0]['body'])
        # A message arriving meanwhile still discards the operator's run.
        token, _ = self.rooms.start_operator_run(self.admin, self.room, '補充')
        self.rooms.post_message(self.customer, self.room, 'c2', '還有嗎')
        self.assertIsNone(self.rooms.finish_run(token, '晚到'))

    def test_overview_summarizes_rooms_for_admin_only(self):
        import time
        before = time.time()
        self.rooms.post_message(self.customer, self.room, 'c1', 'x' * 200)
        [row] = self.rooms.overview(self.admin)
        self.assertEqual((row['site_host'], row['mode'], row['messages'], row['waiting']),
                         ('one.test', 'ai', 1, True))
        at = row['last'].pop('at')
        self.assertTrue(before <= at <= time.time())
        self.assertEqual(row['last'], {'seq': 1, 'sender_kind': 'customer', 'preview': 'x' * 80})
        self.rooms.admin_reply(self.admin, self.room, 'a1', '好')
        [row] = self.rooms.overview(self.admin)
        self.assertEqual((row['messages'], row['waiting'], row['last']['sender_kind']), (2, False, 'human'))
        self.rooms.add_note(self.admin, self.room, 'internal only')
        self.assertNotIn('internal only', repr(self.rooms.overview(self.admin)))
        for actor in (self.customer, object()):
            with self.assertRaises(Unauthorized):
                self.rooms.overview(actor)

    def test_acl_and_forged_actors(self):
        self.assertEqual(self.rooms.list_rooms(self.other), [])
        for actor in (self.other, object(), AdminActor('fake', object()),
                      dataclasses.replace(self.customer, tenant_id=self.tenants[1].id),
                      dataclasses.replace(self.customer, _issuer=object())):
            for call in (lambda: self.rooms.read_room(actor, self.room),
                         lambda: self.rooms.post_message(actor, self.room, 'id', 'text'),
                         lambda: self.rooms.start_run(actor, self.room),
                         lambda: self.rooms.model_context(actor, self.room)):
                with self.assertRaises(Unauthorized):
                    call()
        with self.assertRaises(Unauthorized):
            self.rooms.create_room(self.customer, self.tenants[0].id)
        with self.assertRaises(Unauthorized):
            self.rooms.set_mode(self.customer, self.room, 'human', expected_epoch=0)
        with self.assertRaises(Unauthorized):
            self.rooms.start_run(self.admin, self.room)
        self.assertEqual(len(self.rooms.list_rooms(self.admin)), 1)

    def test_attribution_notes_and_idempotency(self):
        first = self.rooms.post_message(self.customer, self.room, 'c1', 'hello')
        self.assertEqual(first, self.rooms.post_message(self.customer, self.room, 'c1', 'hello'))
        with self.assertRaises(Conflict):
            self.rooms.post_message(self.customer, self.room, 'c1', 'changed')
        with self.assertRaises(Conflict):
            self.rooms.post_message(self.admin, self.room, 'c1', 'hello')
        human = self.rooms.post_message(self.admin, self.room, 'h1', 'human response')
        self.assertEqual((human['sender_kind'], human['sender_id']), ('human', 'operator-17'))
        self.rooms.add_note(self.admin, self.room, 'SECRET internal note')
        for actor in (self.customer, self.other):
            with self.assertRaises(Unauthorized):
                self.rooms.list_notes(actor, self.room)
            with self.assertRaises(Unauthorized):
                self.rooms.add_note(actor, self.room, 'fake')
        self.assertEqual(len(self.rooms.list_notes(self.admin, self.room)), 1)
        context = self.rooms.model_context(self.customer, self.room)
        self.assertNotIn('SECRET', repr(context))
        self.assertEqual([m['seq'] for m in context], [1, 2])
        self.assertEqual(self.rooms.read_room(self.customer, self.room, after=1)['messages'], [human])

    def test_takeover_and_mode_rules(self):
        token = self.rooms.start_run(self.customer, self.room)
        self.rooms.set_mode(self.admin, self.room, 'human', expected_epoch=0)
        self.assertIsNone(self.rooms.finish_run(token, 'late'))
        self.assertIsNone(self.rooms.finish_run(token, 'late'))
        with self.assertRaises(Conflict):
            self.rooms.finish_run(token, 'different')
        with self.assertRaises(Conflict):
            self.rooms.start_run(self.customer, self.room, requested=True)
        with self.assertRaises(Conflict):
            self.rooms.set_mode(self.admin, self.room, 'ai', expected_epoch=0)
        self.rooms.set_mode(self.admin, self.room, 'coassist', expected_epoch=1)
        with self.assertRaises(Conflict):
            self.rooms.start_run(self.customer, self.room)
        token = self.rooms.start_run(self.customer, self.room, requested=True)
        result = self.rooms.finish_run(token, 'requested reply')
        self.assertEqual(result['seq'], 1)
        self.assertEqual(result, self.rooms.finish_run(token, 'requested reply'))
        with self.assertRaises(Unauthorized):
            self.rooms.finish_run(secrets.token_urlsafe(32), 'forged')
        with self.core._connect() as conn:
            self.assertNotIn(token, repr([tuple(r) for r in conn.execute('SELECT * FROM runs')]))

    def test_disabled_and_revoked_reauthorization(self):
        token = self.rooms.start_run(self.customer, self.room)
        self.core.set_tenant_enabled(self.admin, self.tenants[0].id, False)
        self.assertIsNone(self.rooms.finish_run(token, 'disabled reply'))
        for call in (lambda: self.rooms.list_rooms(self.customer),
                     lambda: self.rooms.read_room(self.customer, self.room),
                     lambda: self.rooms.post_message(self.customer, self.room, 'id', 'x'),
                     lambda: self.rooms.start_run(self.customer, self.room)):
            with self.assertRaises(Unauthorized):
                call()
        self.core.set_tenant_enabled(self.admin, self.tenants[0].id, True)
        self.assertIsNone(self.rooms.finish_run(token, 'disabled reply'))
        self.core.logout(self.customer)
        with self.assertRaises(Unauthorized):
            self.rooms.read_room(self.customer, self.room)

    def test_restart_replay_and_capability(self):
        message = self.rooms.post_message(self.customer, self.room, 'persist', 'hello')
        token = self.rooms.start_run(self.customer, self.room)
        core = SupportCore(self.path)
        rooms = SupportRooms(core)
        customer = core.authenticate_session(self.grants[0].token, 'one.test')
        with self.assertRaises(Unauthorized):
            rooms.read_room(self.customer, self.room)
        self.assertEqual(message, rooms.post_message(customer, self.room, 'persist', 'hello'))
        reply = rooms.finish_run(token, 'survived restart')
        self.assertEqual(rooms.read_room(customer, self.room)['messages'], [message, reply])

    def test_race_message_dedup_and_order(self):
        results = self.race(*[lambda: self.rooms.post_message(self.customer, self.room, 'same', 'same body') for _ in range(8)])
        self.assertTrue(all(r == results[0] for r in results))
        results = self.race(*[lambda i=i: self.rooms.post_message(self.customer, self.room, 'distinct' + str(i), str(i)) for i in range(8)])
        self.assertEqual(sorted(r['seq'] for r in results), list(range(2, 10)))
        results = self.race(*[lambda i=i: self.rooms.post_message(self.customer, self.room, 'conflict', str(i)) for i in range(2)])
        self.assertEqual(sum(isinstance(r, Conflict) for r in results), 1)

    def test_race_control_and_finish(self):
        results = self.race(*[lambda mode=mode: self.rooms.set_mode(self.admin, self.room, mode, expected_epoch=0) for mode in ('ai', 'coassist')])
        self.assertEqual(sum(isinstance(r, Conflict) for r in results), 1)
        token = self.rooms.start_run(self.customer, self.room, requested=True)
        results = self.race(*[lambda: self.rooms.finish_run(token, 'one reply') for _ in range(6)])
        self.assertTrue(all(r == results[0] for r in results))
        token = self.rooms.start_run(self.customer, self.room, requested=True)
        results = self.race(lambda: self.rooms.set_mode(self.admin, self.room, 'human', expected_epoch=1), lambda: self.rooms.finish_run(token, 'racing reply'))
        messages = self.rooms.read_room(self.customer, self.room)['messages']
        self.assertEqual(len(messages), 1 if results[1] is None else 2)
        self.assertEqual(self.rooms.read_room(self.customer, self.room)['mode'], 'human')

    def test_run_lease_expiry_and_failure(self):
        now = [self.core.clock()]
        self.core.clock = lambda: now[0]
        rooms = SupportRooms(self.core, run_ttl=10)
        token = rooms.start_run(self.customer, self.room)
        with self.assertRaises(Conflict):
            rooms.start_run(self.customer, self.room)
        now[0] += 10
        replacement = rooms.start_run(self.customer, self.room)
        self.assertIsNone(rooms.finish_run(token, 'expired'))
        rooms.fail_run(replacement)
        rooms.fail_run(replacement)
        self.assertIsNone(rooms.finish_run(replacement, 'failed'))
        token = rooms.start_run(self.customer, self.room)
        result = rooms.finish_run(token, 'success')
        rooms.fail_run(token)
        self.assertEqual(rooms.finish_run(token, 'success'), result)
        now[0] += 10
        self.assertIsNone(rooms.finish_run(token, 'success'))
        self.assertEqual(rooms.read_room(self.customer, self.room)['messages'], [result])
        with self.assertRaises(Unauthorized):
            rooms.fail_run(secrets.token_urlsafe(32))
        for ttl in (0, -1, True, float('inf')):
            with self.assertRaises(InvalidInput):
                SupportRooms(self.core, run_ttl=ttl)

    def test_run_start_race_and_fail_finish_race(self):
        results = self.race(*[lambda: self.rooms.start_run(self.customer, self.room) for _ in range(8)])
        tokens = [r for r in results if isinstance(r, str)]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(sum(isinstance(r, Conflict) for r in results), 7)
        result = self.race(lambda: self.rooms.fail_run(tokens[0]), lambda: self.rooms.finish_run(tokens[0], 'raced'))
        messages = self.rooms.read_room(self.customer, self.room)['messages']
        self.assertEqual(len(messages), 0 if result[1] is None else 1)
        self.rooms.start_run(self.customer, self.room)

    def test_context_snapshot_and_intervening_messages(self):
        first = self.rooms.post_message(self.customer, self.room, 'first', 'question')
        self.rooms.add_note(self.admin, self.room, 'private')
        token, history = self.rooms.start_run_with_context(self.customer, self.room)
        self.assertEqual(history, [first])
        self.rooms.post_message(self.admin, self.room, 'human', 'human update')
        self.assertIsNone(self.rooms.finish_run(token, 'stale response'))
        token, history = self.rooms.start_run_with_context(self.customer, self.room)
        self.assertEqual(len(history), 2)
        self.rooms.post_message(self.customer, self.room, 'followup', 'new question')
        self.assertIsNone(self.rooms.finish_run(token, 'also stale'))
        token, history = self.rooms.start_run_with_context(self.customer, self.room)
        self.rooms.add_note(self.admin, self.room, 'another private note')
        self.rooms.post_message(self.customer, self.room, 'followup', 'new question')
        result = self.rooms.finish_run(token, 'current response')
        self.assertEqual(result['seq'], 4)
        self.assertNotIn('private', repr(history))
        with self.assertRaises(Unauthorized):
            self.rooms.start_run_with_context(self.other, self.room)

    def test_context_snapshot_race(self):
        for index in range(6):
            result = self.race(
                lambda: self.rooms.start_run_with_context(self.customer, self.room),
                lambda: self.rooms.post_message(self.customer, self.room, str(index), 'update'))
            token, history = result[0]
            posted = result[1]
            reply = self.rooms.finish_run(token, 'reply')
            if history and history[-1]['id'] == posted['id']:
                self.assertIsNotNone(reply)
            else:
                self.assertIsNone(reply)

    def test_bounded_replay_snapshot_and_body(self):
        for index in range(105):
            self.rooms.post_message(self.customer, self.room, str(index), 'body')
        page = self.rooms.read_room(self.customer, self.room)['messages']
        self.assertEqual(len(page), 100)
        self.assertEqual(len(self.rooms.read_room(self.customer, self.room, after=100)['messages']), 5)
        token, history = self.rooms.start_run_with_context(self.customer, self.room)
        self.assertEqual([m['seq'] for m in history], list(range(76, 106)))
        self.assertEqual(self.rooms.model_context(self.customer, self.room), history)
        self.assertEqual(self.rooms.finish_run(token, 'bounded')['seq'], 106)
        for after in (-1, 2**63, True):
            with self.assertRaises(InvalidInput):
                self.rooms.read_room(self.customer, self.room, after=after)
        with self.assertRaises(InvalidInput):
            self.rooms.post_message(self.customer, self.room, 'huge', 'x' * 8001)

    def test_run_session_logout_rotation_and_expiry(self):
        token = self.rooms.start_run(self.customer, self.room)
        self.core.logout(self.customer)
        self.assertIsNone(self.rooms.finish_run(token, 'logged out'))
        actor = self.core.login('one.test', 'customer', 'long-password-123').actor
        token = self.rooms.start_run(actor, self.room)
        actor = self.core.login('one.test', 'customer', 'long-password-123').actor
        self.assertIsNone(self.rooms.finish_run(token, 'rotated'))
        self.core.session_ttl = 1
        grant = self.core.login('one.test', 'customer', 'long-password-123')
        token = self.rooms.start_run(grant.actor, self.room)
        self.core.clock = lambda: grant.expires_at
        self.assertIsNone(self.rooms.finish_run(token, 'expired session'))
        self.assertEqual(self.rooms.read_room(self.admin, self.room)['messages'], [])

    def test_invalid_inputs(self):
        for client_id, body in (('', 'a'), ('a', ''), ('run:x', 'a'), ('x' * 129, 'a'), ('a', 'x' * 65537)):
            with self.assertRaises(InvalidInput):
                self.rooms.post_message(self.customer, self.room, client_id, body)
        with self.assertRaises(InvalidInput):
            self.rooms.read_room(self.customer, self.room, after=True)
        with self.assertRaises(InvalidInput):
            self.rooms.start_run(self.customer, self.room, requested='yes')


if __name__ == '__main__':
    unittest.main()
