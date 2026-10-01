"""One-time delivery of a provisioned site's initial password.

DSH password reset is deliberately disabled (`_reject_unsafe_replacement`), so
the password generated during provisioning is the ONLY one that site will ever
have. It must therefore reach its customer, but it must not linger in the
platform database forever.

Design:

* The provisioner writes the secret once, in the same transaction that records
  the job's real success. A site can never be `succeeded_provisioned` with a
  lost password.
* The customer may read it exactly once. Reading DELETES it: the platform stops
  holding a credential to the customer's own site.
* A row that was never read can be discarded by a trusted operator, and an
  unread secret expires: an undelivered password should not outlive its
  usefulness.
* Nothing here is reachable without a live customer session for the owning
  tenant, and the value is never logged, listed, or included in status views.
"""
from __future__ import annotations

import re
import time

from support_core import Conflict, InvalidInput, Unauthorized

# An undelivered password expires; the customer must then ask an operator to
# rebuild the site rather than receive a stale secret of unknown exposure.
DEFAULT_TTL = 7 * 24 * 3600


class SiteCredentials:
    def __init__(self, core, *, ttl=DEFAULT_TTL, writer_capability=None):
        if writer_capability is not None and type(writer_capability) is not object:
            raise InvalidInput('opaque writer capability required')
        if type(ttl) not in (int, float) or not 0 < ttl <= 90 * 24 * 3600:
            raise InvalidInput('invalid credential ttl')
        self.core = core
        self.ttl = ttl
        self._writer = writer_capability
        with core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''CREATE TABLE IF NOT EXISTS support_site_credentials (
                tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
                site_host TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL)''')

    def _writer_check(self, capability):
        if self._writer is None or capability is not self._writer:
            raise Unauthorized('trusted credential writer required')

    def store(self, capability, conn, tenant_id, site_host, username, password):
        """Record the initial password. Called inside the provisioning transaction.

        `conn` is supplied by the caller so the secret and the job's success are
        committed together; a partial write would leave an unreachable site.
        """
        self._writer_check(capability)
        if (not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', username)
                or not isinstance(password, str) or not 8 <= len(password) <= 256
                or not isinstance(site_host, str) or not site_host):
            raise InvalidInput('invalid site credential')
        now = self.core.clock()
        conn.execute('''INSERT INTO support_site_credentials
            VALUES (?,?,?,?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET
            site_host=excluded.site_host, username=excluded.username,
            password=excluded.password, created_at=excluded.created_at,
            expires_at=excluded.expires_at''',
            (tenant_id, site_host, username, password, now, now + self.ttl))

    def peek(self, actor):
        """Report whether a secret is waiting, WITHOUT consuming it."""
        with self.core._connect() as conn:
            conn.execute('BEGIN')
            principal = self.core._customer(conn, actor)
            row = conn.execute('SELECT site_host,username,expires_at FROM support_site_credentials'
                               ' WHERE tenant_id=?', (principal.tenant_id,)).fetchone()
            if row is None:
                return {'available': False, 'reason': 'none'}
            if row['expires_at'] <= self.core.clock():
                return {'available': False, 'reason': 'expired'}
            # Username and host are not secret; the password is never included.
            return {'available': True, 'site_host': row['site_host'],
                    'username': row['username'], 'expires_at': row['expires_at']}

    def reveal(self, actor):
        """Return the password ONCE, then delete it.

        A second call reports that it was already delivered rather than failing
        ambiguously, so a customer who loses it knows to contact an operator
        instead of assuming a transient error.
        """
        expired = False
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            principal = self.core._customer(conn, actor)
            row = conn.execute('SELECT * FROM support_site_credentials WHERE tenant_id=?',
                               (principal.tenant_id,)).fetchone()
            if row is None:
                raise Conflict('no undelivered site credential')
            expired = row['expires_at'] <= self.core.clock()
            # Delete on read either way: an expired secret must not stay stored.
            conn.execute('DELETE FROM support_site_credentials WHERE tenant_id=?',
                         (principal.tenant_id,))
            result = {'site_host': row['site_host'], 'username': row['username'],
                      'password': row['password'], 'delivered_once': True}
        # Raise only AFTER the deletion commits: raising inside the transaction
        # would roll the delete back and leave the stale secret stored.
        if expired:
            raise Conflict('site credential expired')
        return result

    def purge_expired(self, capability):
        """Trusted housekeeping: drop secrets nobody collected."""
        self._writer_check(capability)
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            cursor = conn.execute('DELETE FROM support_site_credentials WHERE expires_at <= ?',
                                  (self.core.clock(),))
            return cursor.rowcount
