"""Version-bound customer model selection and simulated probe evidence.

No credentials, endpoints, provider calls or native DSH config are accepted here.
Trusted composition supplies an allowlisted catalog; default catalog is empty.
An opaque verifier capability can record SIMULATION ONLY, never readiness.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import re
import secrets

from support_core import Conflict, InvalidInput, Unauthorized


@dataclass(frozen=True)
class ProbeTicket:
    principal_id: str
    tenant_id: str
    host: str
    revision: int
    selection_id: str
    lifecycle_version: int
    token: str = field(repr=False)
    session_digest: str = field(repr=False)


class CustomerModelSettings:
    def __init__(self, core, *, catalog=(), verifier_capability=None):
        self.core = core
        if verifier_capability is not None and type(verifier_capability) is not object:
            raise InvalidInput('opaque verifier capability required')
        self._verifier = verifier_capability
        self.catalog = tuple(catalog)
        if (len(set(self.catalog)) != len(self.catalog) or
                any(not isinstance(x, str) or not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,80}', x) for x in self.catalog)):
            raise InvalidInput('invalid model catalog')
        with core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''CREATE TABLE IF NOT EXISTS support_tenant_lifecycle (
                tenant_id TEXT PRIMARY KEY REFERENCES tenants(id), version INTEGER NOT NULL CHECK(version>=0))''')
            conn.execute('CREATE TABLE IF NOT EXISTS support_model_settings_schema (version INTEGER PRIMARY KEY)')
            versions = [r[0] for r in conn.execute('SELECT version FROM support_model_settings_schema')]
            if versions not in ([], [1]):
                raise Conflict('unsupported model settings schema')
            if not versions:
                conn.execute('''CREATE TABLE support_model_settings (
                    principal_id TEXT PRIMARY KEY REFERENCES principals(id),
                    revision INTEGER NOT NULL, selection_id TEXT,
                    state TEXT NOT NULL CHECK(state IN ('unconfigured','pending','probing','simulated_pass','failed')),
                    probe_digest TEXT, probe_session TEXT, probe_host TEXT, probe_lifecycle INTEGER)''')
                conn.execute('INSERT INTO support_model_settings_schema VALUES (1)')
            conn.execute('SELECT principal_id,revision,selection_id,state,probe_digest,probe_session,probe_host FROM support_model_settings LIMIT 0')

    def _worker(self, capability):
        if self._verifier is None or capability is not self._verifier:
            raise Unauthorized('trusted model verifier required')

    @staticmethod
    def _revision(value):
        if type(value) is not int or value < 0 or value >= 2**63-1:
            raise InvalidInput('invalid model revision')

    @staticmethod
    def _row(conn, principal_id):
        return conn.execute('SELECT * FROM support_model_settings WHERE principal_id=?', (principal_id,)).fetchone()

    @staticmethod
    def _lifecycle(conn, tenant_id):
        row = conn.execute('SELECT version FROM support_tenant_lifecycle WHERE tenant_id=?', (tenant_id,)).fetchone()
        return row[0] if row else 0

    def _view(self, conn, actor):
        principal = self.core._customer(conn, actor)
        row = self._row(conn, principal.id)
        state = row['state'] if row else 'unconfigured'
        # Catalog withdrawal or session rotation invalidates reported test evidence.
        if row and row['selection_id'] is not None and row['selection_id'] not in self.catalog:
            state = 'unavailable'
        elif row and state in ('probing', 'simulated_pass', 'failed') and (
                row['probe_session'] != actor._digest or row['probe_host'] != actor.host or
                row['probe_lifecycle'] != self._lifecycle(conn, principal.tenant_id)):
            state = 'pending'
        return {'revision': row['revision'] if row else 0,
                'selection_id': row['selection_id'] if row and state != 'unavailable' else None,
                'state': state, 'simulated': state == 'simulated_pass',
                'verified_for_work': False, 'credentials_configured': False,
                'available_selections': list(self.catalog),
                # True only when trusted composition injected the SIMULATED verifier.
                'simulated_probe_available': self._verifier is not None,
                'adapter_connected': False}

    def status(self, actor):
        with self.core._connect() as conn:
            conn.execute('BEGIN')
            return self._view(conn, actor)

    def configure(self, actor, selection_id, expected_revision):
        self._revision(expected_revision)
        if type(selection_id) is not str or selection_id not in self.catalog:
            raise InvalidInput('model not in trusted catalog')
        return self._change(actor, selection_id, expected_revision)

    def revoke(self, actor, expected_revision):
        self._revision(expected_revision)
        return self._change(actor, None, expected_revision)

    def _change(self, actor, selection_id, expected_revision):
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            principal = self.core._customer(conn, actor)
            row = self._row(conn, principal.id)
            if (row['revision'] if row else 0) != expected_revision:
                raise Conflict('model settings changed; reload required')
            conn.execute('''INSERT INTO support_model_settings VALUES (?,?,?,?,NULL,NULL,NULL,NULL)
                ON CONFLICT(principal_id) DO UPDATE SET revision=excluded.revision,
                selection_id=excluded.selection_id,state=excluded.state,
                probe_digest=NULL,probe_session=NULL,probe_host=NULL,probe_lifecycle=NULL''',
                (principal.id, expected_revision+1, selection_id,
                 'pending' if selection_id is not None else 'unconfigured'))
            return self._view(conn, actor)

    def begin_simulated_probe(self, capability, actor, expected_revision):
        self._worker(capability)
        self._revision(expected_revision)
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            principal = self.core._customer(conn, actor)
            row = self._row(conn, principal.id)
            if not row or row['revision'] != expected_revision or row['selection_id'] not in self.catalog:
                raise Conflict('model selection unavailable')
            token = secrets.token_urlsafe(32)
            lifecycle = self._lifecycle(conn, principal.tenant_id)
            conn.execute('''UPDATE support_model_settings SET state='probing',probe_digest=?,
                probe_session=?,probe_host=?,probe_lifecycle=? WHERE principal_id=?''',
                (hashlib.sha256(token.encode()).hexdigest(), actor._digest, actor.host,
                 lifecycle, principal.id))
            return ProbeTicket(principal.id, principal.tenant_id, actor.host, expected_revision,
                               row['selection_id'], lifecycle, token, actor._digest)

    def finish_simulated_probe(self, capability, ticket, *, passed):
        self._worker(capability)
        if not isinstance(ticket, ProbeTicket) or type(passed) is not bool:
            raise InvalidInput('invalid probe result')
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # Recheck session, tenant and principal at publish time; no stale reply.
            principal = self.core._session(conn, ticket.session_digest, ticket.host)
            row = self._row(conn, ticket.principal_id)
            if (principal['id'] != ticket.principal_id or principal['tenant_id'] != ticket.tenant_id or
                    not row or row['revision'] != ticket.revision or row['selection_id'] != ticket.selection_id or
                    ticket.selection_id not in self.catalog or row['state'] != 'probing' or
                    row['probe_session'] != ticket.session_digest or row['probe_host'] != ticket.host or
                    row['probe_lifecycle'] != ticket.lifecycle_version or
                    ticket.lifecycle_version != self._lifecycle(conn, ticket.tenant_id) or
                    type(ticket.token) is not str or not hmac.compare_digest(row['probe_digest'] or '',
                        hashlib.sha256(ticket.token.encode()).hexdigest())):
                raise Conflict('stale model probe')
            conn.execute('UPDATE support_model_settings SET state=?,probe_digest=NULL WHERE principal_id=?',
                         ('simulated_pass' if passed else 'failed', ticket.principal_id))
            return {'state':'simulated_pass' if passed else 'failed', 'simulated': True,
                    'verified_for_work':False}
