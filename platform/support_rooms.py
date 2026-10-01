"""SQLite support conversations; no HTTP, model calls or ambient credentials.

Actors are verified by the owning SupportCore on every operation. A run token is
an independent, narrowly scoped worker capability: protect it like a session.
Only its digest persists. read_room(after=...) is durable public-message replay.
"""
import secrets
import uuid
from contextlib import contextmanager

from support_core import AdminActor, Conflict, InvalidInput, Unauthorized, _digest


class SupportRooms:
    MODES = ('ai', 'coassist', 'human')

    def __init__(self, core, *, run_ttl=300):
        self.core = core
        self.run_ttl = core._ttl(run_ttl)
        with self._transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS support_rooms_schema (version INTEGER PRIMARY KEY)')
            versions = [r[0] for r in conn.execute('SELECT version FROM support_rooms_schema')]
            if versions not in ([], [1], [2], [3], [4]):
                raise Conflict('unsupported rooms schema')
            # Migrations invalidate legacy leases lacking safety metadata.
            conn.execute('DELETE FROM support_rooms_schema')
            conn.execute('INSERT INTO support_rooms_schema VALUES (4)')
            for sql in (
                "CREATE TABLE IF NOT EXISTS rooms (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), mode TEXT NOT NULL CHECK(mode IN ('ai','coassist','human')), epoch INTEGER NOT NULL DEFAULT 0)",
                "CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id), seq INTEGER NOT NULL, sender_id TEXT NOT NULL, sender_kind TEXT NOT NULL CHECK(sender_kind IN ('customer','human','ai')), client_message_id TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(room_id,seq), UNIQUE(room_id,client_message_id))",
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE, room_id TEXT NOT NULL REFERENCES rooms(id), start_epoch INTEGER NOT NULL, requested INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('running','published','discarded')), result_body TEXT, message_id TEXT REFERENCES messages(id))",
                'CREATE TABLE IF NOT EXISTS internal_notes (id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id), sender_id TEXT NOT NULL, body TEXT NOT NULL)',
            ):
                conn.execute(sql)
            if 'expires_at' not in [r['name'] for r in conn.execute('PRAGMA table_info(runs)')]:
                conn.execute('ALTER TABLE runs ADD COLUMN expires_at REAL NOT NULL DEFAULT 0')
                conn.execute("UPDATE runs SET status='discarded' WHERE status='running'")
            if 'snapshot_seq' not in [r['name'] for r in conn.execute('PRAGMA table_info(runs)')]:
                conn.execute('ALTER TABLE runs ADD COLUMN snapshot_seq INTEGER NOT NULL DEFAULT 0')
                conn.execute("UPDATE runs SET status='discarded' WHERE status='running'")
            if 'session_digest' not in [r['name'] for r in conn.execute('PRAGMA table_info(runs)')]:
                conn.execute('ALTER TABLE runs ADD COLUMN session_digest TEXT REFERENCES sessions(digest)')
                conn.execute("UPDATE runs SET status='discarded' WHERE status='running'")
            # Operator-requested runs carry no customer session; they are
            # authorized by the admin at lease time and by epoch/sequence here.
            if 'operator' not in [r['name'] for r in conn.execute('PRAGMA table_info(runs)')]:
                conn.execute('ALTER TABLE runs ADD COLUMN operator INTEGER NOT NULL DEFAULT 0')
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_running_per_room ON runs(room_id) WHERE status='running'")
            # When each message arrived, for the operator overview. Legacy rows
            # keep 0 ("unknown"); nothing else reads this column.
            if 'created_at' not in [r['name'] for r in conn.execute('PRAGMA table_info(messages)')]:
                conn.execute('ALTER TABLE messages ADD COLUMN created_at REAL NOT NULL DEFAULT 0')

    @contextmanager
    def _transaction(self, write=True):
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield conn

    @staticmethod
    def _text(value, maximum):
        if not isinstance(value, str) or not value.strip():
            raise InvalidInput('nonempty text required')
        try:
            size = len(value.encode('utf-8'))
        except UnicodeError:
            raise InvalidInput('invalid text') from None
        if size > maximum:
            raise InvalidInput('text too long')
        return value

    def _identity(self, conn, actor):
        if isinstance(actor, AdminActor):
            self.core._admin(actor)
            return None, actor.id, 'human'
        principal = self.core._customer(conn, actor)
        return principal.tenant_id, principal.id, 'customer'

    def _room(self, conn, actor, room_id):
        tenant, sender, kind = self._identity(conn, actor)
        row = conn.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone()
        if row is None or (tenant is not None and row['tenant_id'] != tenant):
            raise Unauthorized('room unavailable')
        return row, sender, kind

    def create_room(self, actor, tenant_id, *, mode='ai'):
        with self._transaction() as conn:
            self.core._admin(actor)
            self._mode(mode)
            if not conn.execute('SELECT 1 FROM tenants WHERE id=? AND enabled=1', (tenant_id,)).fetchone():
                raise InvalidInput('tenant unavailable')
            room_id = uuid.uuid4().hex
            conn.execute('INSERT INTO rooms VALUES (?, ?, ?, 0)', (room_id, tenant_id, mode))
            return dict(conn.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone())

    def list_rooms(self, actor):
        with self._transaction(False) as conn:
            tenant, _, _ = self._identity(conn, actor)
            rows = conn.execute('SELECT * FROM rooms ORDER BY id') if tenant is None else conn.execute('SELECT * FROM rooms WHERE tenant_id=? ORDER BY id', (tenant,))
            return [dict(r) for r in rows]

    def read_room(self, actor, room_id, *, after=0):
        if type(after) is not int or not 0 <= after <= 2**63 - 1:
            raise InvalidInput('invalid sequence')
        with self._transaction(False) as conn:
            room, _, _ = self._room(conn, actor, room_id)
            return {**dict(room), 'messages': [dict(r) for r in conn.execute('SELECT * FROM messages WHERE room_id=? AND seq>? ORDER BY seq LIMIT 100', (room_id, after))]}

    def model_context(self, actor, room_id):
        with self._transaction(False) as conn:
            self._room(conn, actor, room_id)
            return list(reversed([dict(r) for r in conn.execute('SELECT * FROM messages WHERE room_id=? ORDER BY seq DESC LIMIT 30', (room_id,))]))

    def _append(self, conn, room_id, sender, kind, client_id, body):
        existing = conn.execute('SELECT * FROM messages WHERE room_id=? AND client_message_id=?', (room_id, client_id)).fetchone()
        if existing:
            if (existing['sender_id'], existing['sender_kind'], existing['body']) != (sender, kind, body):
                raise Conflict('client message id reused')
            return dict(existing)
        seq = conn.execute('SELECT COALESCE(MAX(seq),0)+1 FROM messages WHERE room_id=?', (room_id,)).fetchone()[0]
        message_id = uuid.uuid4().hex
        conn.execute('INSERT INTO messages (id, room_id, seq, sender_id, sender_kind, client_message_id, body, created_at) '
                     'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     (message_id, room_id, seq, sender, kind, client_id, body, self.core.clock()))
        return dict(conn.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone())

    def post_message(self, actor, room_id, client_message_id, body):
        client_message_id = self._text(client_message_id, 128)
        body = self._text(body, 8000)
        if client_message_id.startswith('run:'):
            raise InvalidInput('reserved client message id')
        with self._transaction() as conn:
            _, sender, kind = self._room(conn, actor, room_id)
            return self._append(conn, room_id, sender, kind, client_message_id, body)

    def notify_context(self, room_id):
        """Server-side only (never routed): what a notification may say.

        Site host, mode and how many messages the customer has written; no
        message text, notes or identities.
        """
        with self._transaction(False) as conn:
            row = conn.execute(
                'SELECT r.id AS room_id, r.mode, t.site_host, '
                "(SELECT COUNT(*) FROM messages m WHERE m.room_id=r.id AND m.sender_kind='customer') AS customer_messages "
                'FROM rooms r JOIN tenants t ON t.id=r.tenant_id WHERE r.id=?', (room_id,)).fetchone()
            return dict(row) if row else None

    def admin_reply(self, actor, room_id, client_message_id, body):
        """An operator answers in the room. The mode is NOT changed.

        Who answers is the operator's explicit choice (/control): a reply in an
        'ai' room leaves the assistant answering the customer's next message.
        Any AI run already in flight is discarded by finish_run's sequence
        check, so it cannot land after (and contradict) this reply.
        """
        client_message_id = self._text(client_message_id, 128)
        body = self._text(body, 8000)
        if client_message_id.startswith('run:'):
            raise InvalidInput('reserved client message id')
        with self._transaction() as conn:
            self.core._admin(actor)
            room, sender, kind = self._room(conn, actor, room_id)
            message = self._append(conn, room_id, sender, kind, client_message_id, body)
            room = dict(conn.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone())
            return {'message': message, 'room': room}

    def start_operator_run(self, actor, room_id, instruction):
        """Lease an AI turn the operator asked for, with a private instruction.

        Allowed in every mode: the operator is the one deciding the AI speaks.
        The instruction is kept as an internal note (never shown to the
        customer, never in the public history). Returns (token, history).
        """
        instruction = self._text(instruction, 4000)
        with self._transaction() as conn:
            self.core._admin(actor)
            room, _, _ = self._room(conn, actor, room_id)
            now = self.core.clock()
            conn.execute("UPDATE runs SET status='discarded' WHERE room_id=? AND status='running' AND expires_at<=?", (room_id, now))
            if conn.execute("SELECT 1 FROM runs WHERE room_id=? AND status='running'", (room_id,)).fetchone():
                raise Conflict('run already active')
            conn.execute('INSERT INTO internal_notes VALUES (?, ?, ?, ?)',
                         (uuid.uuid4().hex, room_id, actor.id, '指示 AI：' + instruction))
            history = list(reversed([dict(r) for r in conn.execute('SELECT * FROM messages WHERE room_id=? ORDER BY seq DESC LIMIT 30', (room_id,))]))
            snapshot_seq = history[-1]['seq'] if history else 0
            token = secrets.token_urlsafe(32)
            conn.execute('INSERT INTO runs (id, digest, room_id, start_epoch, requested, status, result_body, message_id, expires_at, snapshot_seq, session_digest, operator) VALUES (?, ?, ?, ?, 1, ?, NULL, NULL, ?, ?, NULL, 1)', (uuid.uuid4().hex, _digest(token), room_id, room['epoch'], 'running', now + self.run_ttl, snapshot_seq))
            return token, history

    def overview(self, actor):
        """Operator summary of every room: owner site, mode, size, last message.

        Only the latest public message is summarized (kind, time, a short
        preview); internal notes and run results are never included.
        """
        with self._transaction(False) as conn:
            self.core._admin(actor)
            rows = conn.execute(
                'SELECT r.id, r.tenant_id, r.mode, r.epoch, t.site_host, '
                '(SELECT COUNT(*) FROM messages m WHERE m.room_id=r.id) AS messages, '
                '(SELECT COUNT(*) FROM messages m WHERE m.room_id=r.id AND m.sender_kind=\'customer\') AS from_customer '
                'FROM rooms r JOIN tenants t ON t.id=r.tenant_id ORDER BY t.site_host, r.id').fetchall()
            out = []
            for row in rows:
                last = conn.execute('SELECT seq, sender_kind, body, created_at FROM messages '
                                    'WHERE room_id=? ORDER BY seq DESC LIMIT 1', (row['id'],)).fetchone()
                item = dict(row)
                item['last'] = None if last is None else {
                    'seq': last['seq'], 'sender_kind': last['sender_kind'],
                    'at': last['created_at'] or None, 'preview': last['body'][:80]}
                # The customer spoke last: someone (AI or human) still owes a reply.
                item['waiting'] = last is not None and last['sender_kind'] == 'customer'
                out.append(item)
            return out

    def _mode(self, mode):
        if mode not in self.MODES:
            raise InvalidInput('invalid mode')

    def set_mode(self, actor, room_id, mode, *, expected_epoch):
        with self._transaction() as conn:
            self.core._admin(actor)
            room, _, _ = self._room(conn, actor, room_id)
            self._mode(mode)
            if type(expected_epoch) is not int or expected_epoch != room['epoch']:
                raise Conflict('stale control epoch')
            conn.execute('UPDATE rooms SET mode=?, epoch=epoch+1 WHERE id=?', (mode, room_id))
            return dict(conn.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone())

    def start_run(self, actor, room_id, *, requested=False):
        token, _ = self.start_run_with_context(actor, room_id, requested=requested)
        return token

    def start_run_with_context(self, actor, room_id, *, requested=False):
        """Authorize and snapshot public history atomically with the worker lease."""
        if type(requested) is not bool:
            raise InvalidInput('requested must be boolean')
        with self._transaction() as conn:
            self.core._customer(conn, actor)
            room, _, _ = self._room(conn, actor, room_id)
            if room['mode'] == 'human' or (room['mode'] == 'coassist' and not requested):
                raise Conflict('mode forbids run')
            now = self.core.clock()
            conn.execute("UPDATE runs SET status='discarded' WHERE room_id=? AND status='running' AND expires_at<=?", (room_id, now))
            if conn.execute("SELECT 1 FROM runs WHERE room_id=? AND status='running'", (room_id,)).fetchone():
                raise Conflict('run already active')
            history = list(reversed([dict(r) for r in conn.execute('SELECT * FROM messages WHERE room_id=? ORDER BY seq DESC LIMIT 30', (room_id,))]))
            snapshot_seq = history[-1]['seq'] if history else 0
            token = secrets.token_urlsafe(32)
            conn.execute('INSERT INTO runs (id, digest, room_id, start_epoch, requested, status, result_body, message_id, expires_at, snapshot_seq, session_digest) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)', (uuid.uuid4().hex, _digest(token), room_id, room['epoch'], requested, 'running', now + self.run_ttl, snapshot_seq, actor._digest))
            return token, history

    def finish_run(self, capability, body):
        """Return published message or None when discarded; retries must match body.

        This is intentionally capability-only, not customer/admin authorization.
        It never exposes room data except the one result the worker submitted.
        """
        digest = _digest(capability)
        body = self._text(body, 8000)
        with self._transaction() as conn:
            run = conn.execute('SELECT * FROM runs WHERE digest=?', (digest,)).fetchone()
            if run is None:
                raise Unauthorized('run unavailable')
            if run['expires_at'] <= self.core.clock():
                conn.execute("UPDATE runs SET status='discarded' WHERE id=? AND status='running'", (run['id'],))
                return None
            if run['status'] == 'discarded' and run['result_body'] is None:
                return None
            if run['status'] != 'running':
                if run['result_body'] != body:
                    raise Conflict('run result reused')
                message = conn.execute('SELECT * FROM messages WHERE id=?', (run['message_id'],)).fetchone()
                return dict(message) if message else None
            room = conn.execute('SELECT r.*, t.enabled FROM rooms r JOIN tenants t ON t.id=r.tenant_id WHERE r.id=?', (run['room_id'],)).fetchone()
            current_seq = conn.execute('SELECT COALESCE(MAX(seq),0) FROM messages WHERE room_id=?', (room['id'],)).fetchone()[0]
            session = None if run['operator'] else conn.execute('SELECT s.*, p.tenant_id FROM sessions s JOIN principals p ON p.id=s.principal_id WHERE s.digest=?', (run['session_digest'],)).fetchone()
            session_valid = session is not None and session['revoked_at'] is None and session['expires_at'] > self.core.clock() and session['tenant_id'] == room['tenant_id']
            unchanged = room['enabled'] and room['epoch'] == run['start_epoch'] and current_seq == run['snapshot_seq']
            if run['operator']:
                allowed = unchanged
            else:
                allowed = session_valid and unchanged and (room['mode'] == 'ai' or (room['mode'] == 'coassist' and run['requested']))
            message = self._append(conn, room['id'], 'ai', 'ai', 'run:' + run['id'], body) if allowed else None
            conn.execute('UPDATE runs SET status=?, result_body=?, message_id=? WHERE id=?', ('published' if message else 'discarded', body, message['id'] if message else None, run['id']))
            return message

    def fail_run(self, capability):
        """Discard an unsuccessful worker lease, idempotently; never undo publication.

        The caller must still cancel its own model work. SQLite bounds active
        leases, not execution in an external provider after a lease expires.
        """
        digest = _digest(capability)
        with self._transaction() as conn:
            run = conn.execute('SELECT * FROM runs WHERE digest=?', (digest,)).fetchone()
            if run is None:
                raise Unauthorized('run unavailable')
            conn.execute("UPDATE runs SET status='discarded' WHERE id=? AND status='running'", (run['id'],))

    def add_note(self, actor, room_id, body):
        body = self._text(body, 8000)
        with self._transaction() as conn:
            self.core._admin(actor)
            self._room(conn, actor, room_id)
            note_id = uuid.uuid4().hex
            conn.execute('INSERT INTO internal_notes VALUES (?, ?, ?, ?)', (note_id, room_id, actor.id, body))
            return {'id': note_id, 'room_id': room_id, 'sender_id': actor.id, 'body': body}

    def list_notes(self, actor, room_id):
        with self._transaction(False) as conn:
            self.core._admin(actor)
            self._room(conn, actor, room_id)
            return [dict(r) for r in conn.execute('SELECT * FROM internal_notes WHERE room_id=? ORDER BY rowid', (room_id,))]
