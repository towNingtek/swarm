"""Offline SQLite admission ledger; NOT connected to a live model or billing.

Trusted callers must enforce the reserved maximum at generation time: settlement
above the estimate is rejected, never silently billed. None means unknown usage
and retains the entire hold. Cancel is safe ONLY before dispatch / with proof of
zero usage; this module cannot verify that proof. No automatic expiry of holds.

reserve retries require the returned capability via reservation=; only its digest
is persisted, so a lost first response cannot be recovered/reissued. Request IDs
are tenant-wide and permanent, not recycled at month boundaries. UTC month uses
core.clock. Policy disabled stops new admission, but allows authenticated cleanup.
Unlimited tenant metadata never grants admission or bypasses auth.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from support_core import Conflict, InvalidInput, Unauthorized, SupportError


class QuotaDenied(SupportError):
    pass


@dataclass(frozen=True)
class Reservation:
    token: str = field(repr=False)
    request_id: str
    month: str
    estimated_tokens: int


class SupportQuota:
    MAX_TOKENS = 1_000_000_000
    MAX_MONTHLY_LIMIT = 1_000_000_000_000

    def __init__(self, core, *, max_outstanding=32):
        self.core = core
        self._integer(max_outstanding, 1, 10000)
        self.max_outstanding = max_outstanding
        with core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("CREATE TABLE IF NOT EXISTS support_quota_policies (tenant_id TEXT PRIMARY KEY REFERENCES tenants(id), mode TEXT NOT NULL CHECK(mode IN ('disabled','capped','unlimited')), monthly_limit INTEGER)")
            conn.execute("CREATE TABLE IF NOT EXISTS support_quota_ledger (tenant_id TEXT NOT NULL REFERENCES tenants(id), request_id TEXT NOT NULL, principal_id TEXT NOT NULL REFERENCES principals(id), digest TEXT NOT NULL UNIQUE, month TEXT NOT NULL, estimate INTEGER NOT NULL CHECK(estimate>0), state TEXT NOT NULL CHECK(state IN ('held','settled','cancelled')), actual INTEGER, PRIMARY KEY(tenant_id,request_id))")
            conn.execute('CREATE INDEX IF NOT EXISTS support_quota_month ON support_quota_ledger(tenant_id,month,state)')

    @staticmethod
    def _integer(value, low, high):
        if type(value) is not int or not low <= value <= high:
            raise InvalidInput('invalid token limit')

    @staticmethod
    def _digest(reservation):
        token = reservation.token if isinstance(reservation, Reservation) else reservation
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise Unauthorized('reservation unavailable')
        return hashlib.sha256(token.encode('ascii')).hexdigest()

    def set_policy(self, admin, tenant_id, mode, monthly_limit=None):
        self.core._admin(admin)
        if mode not in ('disabled', 'capped', 'unlimited'):
            raise InvalidInput('invalid quota mode')
        if mode == 'capped':
            self._integer(monthly_limit, 0, self.MAX_MONTHLY_LIMIT)
        elif monthly_limit is not None:
            raise InvalidInput('monthly_limit requires capped policy')
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM tenants WHERE id=?', (tenant_id,)).fetchone():
                raise InvalidInput('unknown tenant')
            conn.execute('INSERT INTO support_quota_policies VALUES (?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET mode=excluded.mode, monthly_limit=excluded.monthly_limit', (tenant_id, mode, monthly_limit))

    def reserve(self, actor, request_id, estimated_tokens, *, reservation=None):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id):
            raise InvalidInput('invalid request id')
        self._integer(estimated_tokens, 1, self.MAX_TOKENS)
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            principal = self.core._customer(conn, actor)
            tenant = principal.tenant_id
            old = conn.execute('SELECT * FROM support_quota_ledger WHERE tenant_id=? AND request_id=?', (tenant, request_id)).fetchone()
            if old:
                if old['principal_id'] != principal.id or old['estimate'] != estimated_tokens:
                    raise Conflict('request id conflict')
                if reservation is None:
                    raise Conflict('retry requires original reservation capability')
                if self._digest(reservation) != old['digest']:
                    raise Unauthorized('reservation unavailable')
                if old['state'] != 'held':
                    raise Conflict('request already finalized')
                token = reservation.token if isinstance(reservation, Reservation) else reservation
                return Reservation(token, request_id, old['month'], estimated_tokens)
            if reservation is not None:
                raise Conflict('unknown retry')
            policy = conn.execute('SELECT * FROM support_quota_policies WHERE tenant_id=?', (tenant,)).fetchone()
            if not policy or policy['mode'] == 'disabled':
                raise QuotaDenied('quota disabled')
            outstanding = conn.execute("SELECT count(*) FROM support_quota_ledger WHERE tenant_id=? AND state='held'", (tenant,)).fetchone()[0]
            if outstanding >= self.max_outstanding:
                raise QuotaDenied('outstanding limit')
            month = datetime.fromtimestamp(self.core.clock(), timezone.utc).strftime('%Y-%m')
            rows = conn.execute("SELECT estimate,state,actual FROM support_quota_ledger WHERE tenant_id=? AND month=? AND state!='cancelled'", (tenant, month))
            used = sum(row['estimate'] if row['state'] == 'held' else row['actual'] for row in rows)
            if policy['mode'] == 'capped' and used + estimated_tokens > policy['monthly_limit']:
                raise QuotaDenied('monthly budget exhausted')
            token = secrets.token_urlsafe(32)
            result = Reservation(token, request_id, month, estimated_tokens)
            conn.execute("INSERT INTO support_quota_ledger VALUES (?,?,?,?,?,?,'held',NULL)", (tenant, request_id, principal.id, self._digest(result), month, estimated_tokens))
            return result

    def _finish(self, actor, reservation, actual, cancel=False):
        if actual is not None:
            self._integer(actual, 0, self.MAX_TOKENS)
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            principal = self.core._customer(conn, actor)
            digest = self._digest(reservation)
            row = conn.execute('SELECT * FROM support_quota_ledger WHERE digest=? AND tenant_id=? AND principal_id=?', (digest, principal.tenant_id, principal.id)).fetchone()
            if not row:
                raise Unauthorized('reservation unavailable')
            desired = 'cancelled' if cancel else 'settled'
            if row['state'] != 'held':
                if row['state'] == desired and row['actual'] == actual:
                    return row['state']
                raise Conflict('settlement conflict')
            if actual is None:
                return 'held'
            if actual > row['estimate']:
                raise Conflict('actual usage exceeds reservation')
            conn.execute('UPDATE support_quota_ledger SET state=?,actual=? WHERE digest=?', (desired, actual, digest))
            return desired

    def settle(self, actor, reservation, actual_tokens):
        return self._finish(actor, reservation, actual_tokens)

    def cancel(self, actor, reservation):
        return self._finish(actor, reservation, 0, cancel=True)
