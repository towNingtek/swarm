"""Starter model for customer sites: per-site tokens, admission and usage ledger.

A new DSH site needs a working model before the customer has registered one of
their own. The platform lends its starter model (e.g. ``cloud-fast``) through a
relay (``site_model_relay.py``); this module is the relay's bookkeeping.

Boundaries:

* The platform's gateway key never reaches a site. A site holds only its own
  token; the database keeps just its SHA-256 digest. Rotating replaces it, and
  deleting the tenant (or disabling it) stops it working.
* The budget is the tenant's existing policy in ``support_quota_policies`` (the
  one edited on /admin/customers): disabled, unlimited or a monthly token cap.
  One monthly pool per tenant: this ledger and the platform Copilot ledger
  (``support_quota_ledger``) are summed.
* Holds are conservative. A request reserves an upper-bound estimate; it is
  settled to the provider's reported usage, or kept at the estimate when usage
  is unknown (e.g. the stream broke). Unknown is never recorded as zero.
"""
from __future__ import annotations

import hashlib
import math
import re
import secrets
from datetime import datetime, timezone

from support_core import InvalidInput, SupportError

TOKEN_PREFIX = 'sms_'
_TOKEN_RE = re.compile(r'sms_[A-Za-z0-9_-]{43}')


class SiteModelDenied(SupportError):
    """Admission refused. ``status`` is the HTTP status the relay returns."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def _digest(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


class SiteModelAccess:
    MAX_TOKENS = 1_000_000_000

    def __init__(self, core, *, max_outstanding=4):
        if type(max_outstanding) is not int or not 1 <= max_outstanding <= 64:
            raise InvalidInput('invalid outstanding limit')
        self.core = core
        self.max_outstanding = max_outstanding
        with core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('CREATE TABLE IF NOT EXISTS support_site_model_tokens ('
                         'tenant_id TEXT PRIMARY KEY REFERENCES tenants(id), '
                         'digest TEXT NOT NULL UNIQUE, created_at REAL NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS support_site_model_usage ('
                         'tenant_id TEXT NOT NULL REFERENCES tenants(id), request_id TEXT NOT NULL, '
                         'month TEXT NOT NULL, model TEXT NOT NULL, '
                         'estimate INTEGER NOT NULL CHECK(estimate>0), '
                         "state TEXT NOT NULL CHECK(state IN ('held','settled','unknown')), "
                         'actual INTEGER, created_at REAL NOT NULL, PRIMARY KEY(tenant_id,request_id))')
            conn.execute('CREATE INDEX IF NOT EXISTS support_site_model_usage_month '
                         'ON support_site_model_usage(tenant_id,month,state)')

    # -- tokens -----------------------------------------------------------------
    # Trusted in-process callers only (provisioner, operator CLI): there is no
    # HTTP route to these.

    def issue(self, tenant_id):
        """Create (or rotate) the tenant's site token and return it once."""
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM tenants WHERE id=?', (tenant_id,)).fetchone():
                raise InvalidInput('unknown tenant')
            conn.execute('INSERT INTO support_site_model_tokens VALUES (?,?,?) ON CONFLICT(tenant_id) '
                         'DO UPDATE SET digest=excluded.digest, created_at=excluded.created_at',
                         (tenant_id, _digest(token), self.core.clock()))
        return token

    def revoke(self, tenant_id):
        with self.core._connect() as conn:
            conn.execute('DELETE FROM support_site_model_tokens WHERE tenant_id=?', (tenant_id,))

    # -- admission --------------------------------------------------------------

    def _month(self):
        return datetime.fromtimestamp(self.core.clock(), timezone.utc).strftime('%Y-%m')

    @staticmethod
    def _used(conn, tenant, month):
        used = 0
        for row in conn.execute('SELECT estimate,state,actual FROM support_site_model_usage '
                                'WHERE tenant_id=? AND month=?', (tenant, month)):
            used += row['estimate'] if row['state'] == 'held' else row['actual']
        # The platform Copilot ledger, when present, shares the same pool.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='support_quota_ledger'").fetchone():
            for row in conn.execute("SELECT estimate,state,actual FROM support_quota_ledger "
                                    "WHERE tenant_id=? AND month=? AND state!='cancelled'",
                                    (tenant, month)):
                used += row['estimate'] if row['state'] == 'held' else (row['actual'] or 0)
        return used

    def authenticate(self, token):
        """Return the tenant id for a valid token of an enabled tenant."""
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            raise SiteModelDenied(401, 'invalid_api_key', 'invalid site model token')
        with self.core._connect() as conn:
            row = conn.execute('SELECT t.id, t.enabled FROM support_site_model_tokens k '
                               'JOIN tenants t ON t.id=k.tenant_id WHERE k.digest=?',
                               (_digest(token),)).fetchone()
        if row is None:
            raise SiteModelDenied(401, 'invalid_api_key', 'invalid site model token')
        if not row['enabled']:
            raise SiteModelDenied(403, 'site_disabled', 'this site is disabled')
        return row['id']

    def admit(self, token, model, estimate):
        """Authenticate the site token and hold ``estimate`` tokens.

        Returns ``(tenant_id, request_id)``. Raises SiteModelDenied.
        """
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            raise SiteModelDenied(401, 'invalid_api_key', 'invalid site model token')
        if type(estimate) is not int or not 1 <= estimate <= self.MAX_TOKENS:
            raise SiteModelDenied(400, 'invalid_request', 'request too large')
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT t.id, t.enabled FROM support_site_model_tokens k '
                               'JOIN tenants t ON t.id=k.tenant_id WHERE k.digest=?',
                               (_digest(token),)).fetchone()
            if row is None:
                raise SiteModelDenied(401, 'invalid_api_key', 'invalid site model token')
            tenant = row['id']
            if not row['enabled']:
                raise SiteModelDenied(403, 'site_disabled', 'this site is disabled')
            policy = conn.execute('SELECT mode, monthly_limit FROM support_quota_policies '
                                  'WHERE tenant_id=?', (tenant,)).fetchone()
            if policy is None or policy['mode'] == 'disabled':
                raise SiteModelDenied(403, 'starter_model_disabled',
                                      '起步模型未開放給這個站台。請到 Settings → Models 接上你自己的模型。')
            outstanding = conn.execute("SELECT count(*) FROM support_site_model_usage "
                                       "WHERE tenant_id=? AND state='held'", (tenant,)).fetchone()[0]
            if outstanding >= self.max_outstanding:
                raise SiteModelDenied(429, 'too_many_requests', 'too many concurrent requests')
            month = self._month()
            if policy['mode'] == 'capped' and \
                    self._used(conn, tenant, month) + estimate > policy['monthly_limit']:
                raise SiteModelDenied(403, 'monthly_quota_exhausted',
                                      '本月起步模型額度已用完。請到 Settings → Models 接上你自己的模型。')
            request_id = secrets.token_hex(16)
            conn.execute("INSERT INTO support_site_model_usage VALUES (?,?,?,?,?,'held',NULL,?)",
                         (tenant, request_id, month, model, estimate, self.core.clock()))
        return tenant, request_id

    def settle(self, tenant_id, request_id, actual):
        """Record real usage; ``None`` (unknown) keeps the whole estimate."""
        if actual is not None and (type(actual) is not int or not 0 <= actual <= self.MAX_TOKENS):
            actual = None
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT estimate, state FROM support_site_model_usage '
                               'WHERE tenant_id=? AND request_id=?', (tenant_id, request_id)).fetchone()
            if row is None or row['state'] != 'held':
                return
            if actual is None:
                conn.execute("UPDATE support_site_model_usage SET state='unknown', actual=estimate "
                             "WHERE tenant_id=? AND request_id=?", (tenant_id, request_id))
            else:
                # Actual may exceed the estimate; bill what was used, never less.
                conn.execute("UPDATE support_site_model_usage SET state='settled', actual=? "
                             "WHERE tenant_id=? AND request_id=?", (actual, tenant_id, request_id))

    def release_stale_holds(self):
        """After a restart no request is in flight: holds become 'unknown'."""
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return conn.execute("UPDATE support_site_model_usage SET state='unknown', actual=estimate "
                                "WHERE state='held'").rowcount

    def month_usage(self, tenant_id):
        with self.core._connect() as conn:
            return self._used(conn, tenant_id, self._month())


def estimate_tokens(body_bytes, max_output):
    """Hold size for admission: request bytes / 3 plus the output cap.

    Text tokenizes at roughly 3-4 bytes per token, so bytes/3 covers the prompt
    in practice; settlement then uses the provider's real count, which may be
    higher or lower. This only decides whether to START a request.
    """
    return max(1, math.ceil(len(body_bytes) / 3) + int(max_output))
