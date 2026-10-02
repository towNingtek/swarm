"""Import-safe, standard-library customer identity foundation (no HTTP).

Trust boundary: only trusted in-process code may configure admin_verifier. It
verifies an adapter assertion and returns a nonempty stable administrator ID;
None/False rejects. There is deliberately no default administrator identity.
CustomerActor is a capability issued by this instance, NOT a tenant selector.
Reauthenticate a persisted bearer after reopening the core. Adapters must still
implement origin/CSRF checks, TLS cookies, rate limits and credential redaction.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable


class SupportError(Exception):
    """Safe public error: contains no credentials or hashes."""


class Unauthorized(SupportError):
    pass


class InvalidInput(SupportError):
    pass


class InvalidInvite(SupportError):
    pass


class Conflict(SupportError):
    pass


@dataclass(frozen=True)
class AdminActor:
    id: str
    _issuer: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class CustomerActor:
    principal_id: str
    tenant_id: str
    host: str
    _digest: str = field(repr=False)
    _issuer: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class Tenant:
    id: str
    site_host: str
    enabled: bool
    quota_mode: str
    platform_host: str = ''


@dataclass(frozen=True)
class Principal:
    id: str
    tenant_id: str
    username: str
    role: str


@dataclass(frozen=True)
class SessionGrant:
    token: str = field(repr=False)
    actor: CustomerActor
    expires_at: float


@dataclass(frozen=True)
class InvitePreview:
    tenant_id: str
    site_host: str
    expires_at: float


def normalize_host(host: str) -> str:
    """Canonical DNS host only: no scheme, port, userinfo, path or proxy data."""
    if not isinstance(host, str):
        raise InvalidInput('invalid host')
    host = host.lower()
    if len(host) > 253 or not host or any(
        not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
        for part in host.split('.')
    ):
        raise InvalidInput('invalid host')
    return host


def _digest(token: str) -> str:
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise Unauthorized('invalid credential')
    return hashlib.sha256(token.encode('ascii')).hexdigest()


# Owner decision: 8 is the usability floor for customer activation. Offline
# cracking risk is real at this length, so the mitigations that remain must not
# be weakened: scrypt hashing, per-activation rate limiting, and session
# rotation on login.
MIN_PASSWORD_BYTES = 8
MAX_PASSWORD_BYTES = 1024


def _password_bytes(password: str) -> bytes:
    message = f'password must be {MIN_PASSWORD_BYTES} to {MAX_PASSWORD_BYTES} UTF-8 bytes'
    if not isinstance(password, str):
        raise InvalidInput(message)
    try:
        value = password.encode('utf-8')
    except UnicodeError:
        raise InvalidInput('invalid password') from None
    if not MIN_PASSWORD_BYTES <= len(value) <= MAX_PASSWORD_BYTES:
        raise InvalidInput(message)
    return value


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(_password_bytes(password), salt=salt, n=16384, r=8, p=1, dklen=32)
    return 'scrypt$16384$8$1$' + salt.hex() + '$' + value.hex()


def _password_matches(password: str, encoded: str) -> bool:
    try:
        value = _password_bytes(password)
        algorithm, n, r, p, salt, expected = encoded.split('$')
        if (algorithm, n, r, p) != ('scrypt', '16384', '8', '1'):
            return False
        actual = hashlib.scrypt(value, salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, InvalidInput):
        return False


class SupportCore:
    SCHEMA_VERSION = 2

    def __init__(self, db_path: str, *, clock: Callable[[], float] = time.time,
                 admin_verifier: Callable[[object], str | None] | None = None,
                 session_ttl: float = 86400):
        self.db_path = str(db_path)
        if self.db_path == ':memory:':
            raise InvalidInput('use a persistent file path')
        self.clock = clock
        self.session_ttl = self._ttl(session_ttl)
        self._admin_verifier = admin_verifier
        # Customer issuer must never grant administrator authority. Each verified
        # admin identity gets a separate opaque capability bound to that ID.
        # This guards data-class misuse, not hostile Python introspection.
        self._admin_capabilities: dict[str, object] = {}
        self._issuer = object()
        with self._connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('BEGIN IMMEDIATE')
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            # Accept every version this code knows how to migrate FROM, not just
            # the current one: rejecting v1 here would lock out existing databases.
            if version not in (0, 1, self.SCHEMA_VERSION):
                raise Conflict('unsupported schema version')
            for statement in (
                'CREATE TABLE IF NOT EXISTS tenants (id TEXT PRIMARY KEY, site_host TEXT NOT NULL UNIQUE, platform_host TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), quota_mode TEXT NOT NULL)',
                # Customers of every tenant authenticate on ONE platform host, so a
                # username must be unique per platform host, not per tenant: two
                # tenants both holding "alice" would otherwise collide at login.
                # platform_host is denormalised here because SQLite cannot enforce
                # uniqueness across a join, and this MUST be a database constraint
                # rather than an application check (which would race).
                "CREATE TABLE IF NOT EXISTS principals (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), platform_host TEXT NOT NULL, username TEXT NOT NULL, role TEXT NOT NULL CHECK(role = 'customer'), password_hash TEXT NOT NULL, UNIQUE(tenant_id, username), UNIQUE(platform_host, username))",
                'CREATE TABLE IF NOT EXISTS invites (digest TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), expires_at REAL NOT NULL, redeemed_at REAL, revoked_at REAL)',
                'CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals(id), site_host TEXT NOT NULL, expires_at REAL NOT NULL, revoked_at REAL)',
            ):
                conn.execute(statement)
            if version == 1:
                # v1 had no platform host: customers authenticated on the site host
                # itself. Backfill preserves that exact behaviour for existing rows
                # rather than silently moving anyone onto a new authority.
                # Tolerate a partially applied upgrade: an interrupted migration
                # must be resumable rather than permanently wedging the database.
                for table in ('tenants', 'principals'):
                    columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
                    if 'platform_host' not in columns:
                        conn.execute(f'ALTER TABLE {table} ADD COLUMN platform_host TEXT NOT NULL DEFAULT ""')
                conn.execute('UPDATE tenants SET platform_host=site_host WHERE platform_host=""')
                conn.execute('''UPDATE principals SET platform_host=(
                    SELECT t.platform_host FROM tenants t WHERE t.id=principals.tenant_id)
                    WHERE platform_host=""''')
                # A v1 database may already contain colliding usernames across
                # tenants. Fail loudly instead of creating an index that would
                # silently let one account shadow another at login.
                clash = conn.execute('''SELECT platform_host, username FROM principals
                    GROUP BY platform_host, username HAVING COUNT(*) > 1 LIMIT 1''').fetchone()
                if clash is not None:
                    raise Conflict('duplicate usernames per platform host; manual migration required')
                conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS principals_platform_username
                    ON principals(platform_host, username)''')
            conn.execute('PRAGMA user_version=2')

    @contextmanager
    def _connect(self):
        """Extension hook: connection with FK enforcement; commits or rolls back."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _ttl(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise InvalidInput('invalid lifetime')
        return float(value)

    @staticmethod
    def _username(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value):
            raise InvalidInput('invalid username')
        return value.lower()

    def admin_actor(self, assertion: object) -> AdminActor:
        if self._admin_verifier is None:
            raise Unauthorized('administrator adapter is disabled')
        try:
            identity = self._admin_verifier(assertion)
        except Exception:
            raise Unauthorized('administrator authentication failed') from None
        if not isinstance(identity, str) or not identity.strip():
            raise Unauthorized('administrator authentication failed')
        capability = self._admin_capabilities.setdefault(identity, object())
        return AdminActor(identity, capability)

    def _admin(self, actor):
        if (not isinstance(actor, AdminActor)
                or not isinstance(actor.id, str)
                or actor.id not in self._admin_capabilities
                or actor._issuer is not self._admin_capabilities[actor.id]):
            raise Unauthorized('administrator required')

    def create_tenant(self, actor: AdminActor, site_host: str, *, quota_mode: str = 'metered',
                      platform_host: str | None = None) -> Tenant:
        """Create tenant metadata. No site is provisioned by this call.

        site_host is the authority the customer will EVENTUALLY receive; it need
        not exist yet. platform_host is where the customer activates and signs in
        today. Defaulting platform_host to site_host preserves single-host
        deployments, but a real platform must pass it explicitly.
        """
        self._admin(actor)
        host = normalize_host(site_host)
        platform = host if platform_host is None else normalize_host(platform_host)
        if quota_mode not in ('metered', 'unlimited'):
            raise InvalidInput('invalid quota mode')
        tenant = Tenant(uuid.uuid4().hex, host, True, quota_mode, platform)
        try:
            with self._connect() as conn:
                conn.execute('INSERT INTO tenants VALUES (?, ?, ?, 1, ?)',
                             (tenant.id, host, platform, quota_mode))
        except sqlite3.IntegrityError:
            raise Conflict('tenant host already exists') from None
        return tenant

    def set_tenant_enabled(self, actor: AdminActor, tenant_id: str, enabled: bool):
        self._admin(actor)
        if not isinstance(enabled, bool):
            raise InvalidInput('enabled must be boolean')
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT enabled FROM tenants WHERE id=?', (tenant_id,)).fetchone()
            if row is None:
                raise InvalidInput('unknown tenant')
            # Additive extension shared with provisioning intents. A disable /
            # re-enable cycle must never revive an old in-flight worker lease.
            conn.execute('''CREATE TABLE IF NOT EXISTS support_tenant_lifecycle (
                tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
                version INTEGER NOT NULL CHECK(version >= 0))''')
            conn.execute('INSERT OR IGNORE INTO support_tenant_lifecycle VALUES (?,0)', (tenant_id,))
            if bool(row['enabled']) != enabled:
                conn.execute('UPDATE support_tenant_lifecycle SET version=version+1 WHERE tenant_id=?', (tenant_id,))
                conn.execute('UPDATE tenants SET enabled=? WHERE id=?', (enabled, tenant_id))

    def issue_invite(self, actor: AdminActor, tenant_id: str, *, ttl: float = 3600) -> str:
        self._admin(actor)
        ttl = self._ttl(ttl)
        token = secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM tenants WHERE id=? AND enabled=1', (tenant_id,)).fetchone():
                raise InvalidInput('tenant unavailable')
            conn.execute('INSERT INTO invites VALUES (?, ?, ?, NULL, NULL)', (_digest(token), tenant_id, self.clock() + ttl))
        return token

    def revoke_invite(self, actor: AdminActor, token: str):
        self._admin(actor)
        with self._connect() as conn:
            conn.execute('UPDATE invites SET revoked_at=COALESCE(revoked_at, ?) WHERE digest=?', (self.clock(), _digest(token)))

    def _invite(self, conn, token, host):
        try:
            digest = _digest(token)
        except Unauthorized:
            raise InvalidInvite('invite unavailable') from None
        row = conn.execute('SELECT i.*, t.site_host, t.platform_host FROM invites i JOIN tenants t ON t.id=i.tenant_id WHERE i.digest=? AND t.enabled=1', (digest,)).fetchone()
        # Redemption is bound to the PLATFORM host: the customer's own site may
        # not exist yet, so requiring it here would make every invite unusable.
        if not row or row['platform_host'] != host or row['expires_at'] <= self.clock() or row['redeemed_at'] is not None or row['revoked_at'] is not None:
            raise InvalidInvite('invite unavailable')
        return row

    def preview_invite(self, token: str, host: str) -> InvitePreview:
        host = normalize_host(host)
        with self._connect() as conn:
            row = self._invite(conn, token, host)
            return InvitePreview(row['tenant_id'], row['site_host'], row['expires_at'])

    def _new_session(self, conn, principal: Principal, host: str) -> SessionGrant:
        token = secrets.token_urlsafe(32)
        digest = _digest(token)
        expires = self.clock() + self.session_ttl
        conn.execute('INSERT INTO sessions VALUES (?, ?, ?, ?, NULL)', (digest, principal.id, host, expires))
        actor = CustomerActor(principal.id, principal.tenant_id, host, digest, self._issuer)
        return SessionGrant(token, actor, expires)

    def redeem_invite(self, token: str, host: str, username: str, password: str) -> SessionGrant:
        host, username = normalize_host(host), self._username(username)
        self.preview_invite(token, host)
        password_hash = _password_hash(password)  # expensive work before write lock
        try:
            with self._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                invite = self._invite(conn, token, host)  # race/expiry/revoke recheck
                principal = Principal(uuid.uuid4().hex, invite['tenant_id'], username, 'customer')
                conn.execute('INSERT INTO principals VALUES (?, ?, ?, ?, ?, ?)',
                             (principal.id, principal.tenant_id, host, username,
                              principal.role, password_hash))
                grant = self._new_session(conn, principal, host)
                conn.execute('UPDATE invites SET redeemed_at=? WHERE digest=?', (self.clock(), _digest(token)))
                return grant
        except sqlite3.IntegrityError:
            # Usernames are unique per platform, so "taken" reveals that a name
            # exists somewhere. Each conflict counts against the invite, which
            # is revoked after a few: the holder cannot probe a list of names.
            self._count_conflict(token)
            raise Conflict('account unavailable') from None

    MAX_INVITE_CONFLICTS = 5

    def _count_conflict(self, token):
        digest = _digest(token)
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('CREATE TABLE IF NOT EXISTS invite_conflicts '
                         '(digest TEXT PRIMARY KEY REFERENCES invites(digest), count INTEGER NOT NULL)')
            conn.execute('INSERT INTO invite_conflicts VALUES (?,1) ON CONFLICT(digest) '
                         'DO UPDATE SET count=count+1', (digest,))
            count = conn.execute('SELECT count FROM invite_conflicts WHERE digest=?', (digest,)).fetchone()[0]
            if count >= self.MAX_INVITE_CONFLICTS:
                conn.execute('UPDATE invites SET revoked_at=? WHERE digest=? AND revoked_at IS NULL '
                             'AND redeemed_at IS NULL', (self.clock(), digest))

    def login(self, host: str, username: str, password: str) -> SessionGrant:
        host, username = normalize_host(host), self._username(username)
        with self._connect() as conn:
            row = conn.execute('SELECT p.* FROM principals p JOIN tenants t ON t.id=p.tenant_id WHERE t.platform_host=? AND t.enabled=1 AND p.username=?', (host, username)).fetchone()
        # Fixed dummy work prevents the obvious missing-account fast path.
        encoded = row['password_hash'] if row else 'scrypt$16384$8$1$' + '00' * 16 + '$' + '00' * 32
        if not _password_matches(password, encoded) or row is None:
            raise Unauthorized('invalid credentials')
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current = conn.execute('SELECT p.* FROM principals p JOIN tenants t ON t.id=p.tenant_id WHERE p.id=? AND t.enabled=1 AND t.platform_host=?', (row['id'], host)).fetchone()
            if not current or current['password_hash'] != encoded:
                raise Unauthorized('invalid credentials')
            # Login rotates all existing sessions for this principal.
            conn.execute('UPDATE sessions SET revoked_at=COALESCE(revoked_at, ?) WHERE principal_id=?', (self.clock(), row['id']))
            return self._new_session(conn, Principal(row['id'], row['tenant_id'], row['username'], row['role']), host)

    def _session(self, conn, digest, host):
        row = conn.execute('SELECT p.*, s.expires_at, s.revoked_at, s.site_host AS session_host, t.platform_host FROM sessions s JOIN principals p ON p.id=s.principal_id JOIN tenants t ON t.id=p.tenant_id WHERE s.digest=? AND t.enabled=1', (digest,)).fetchone()
        if not row or row['revoked_at'] is not None or row['expires_at'] <= self.clock() or row['platform_host'] != host or row['session_host'] != host:
            raise Unauthorized('session unavailable')
        return row

    def authenticate_session(self, token: str, host: str) -> CustomerActor:
        host, digest = normalize_host(host), _digest(token)
        with self._connect() as conn:
            row = self._session(conn, digest, host)
            return CustomerActor(row['id'], row['tenant_id'], host, digest, self._issuer)

    def _customer(self, conn, actor: CustomerActor) -> Principal:
        """Extensions must call this inside every resource transaction/read."""
        if not isinstance(actor, CustomerActor) or actor._issuer is not self._issuer:
            raise Unauthorized('customer session required')
        row = self._session(conn, actor._digest, actor.host)
        if row['id'] != actor.principal_id or row['tenant_id'] != actor.tenant_id:
            raise Unauthorized('invalid actor')
        return Principal(row['id'], row['tenant_id'], row['username'], row['role'])

    def authorize_customer(self, actor: CustomerActor) -> Principal:
        with self._connect() as conn:
            return self._customer(conn, actor)

    def get_tenant(self, actor: CustomerActor) -> Tenant:
        with self._connect() as conn:
            conn.execute('BEGIN')
            principal = self._customer(conn, actor)
            row = conn.execute('SELECT * FROM tenants WHERE id=?', (principal.tenant_id,)).fetchone()
            return Tenant(row['id'], row['site_host'], bool(row['enabled']), row['quota_mode'],
                          row['platform_host'])

    def logout(self, actor: CustomerActor):
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._customer(conn, actor)
            conn.execute('UPDATE sessions SET revoked_at=? WHERE digest=?', (self.clock(), actor._digest))
