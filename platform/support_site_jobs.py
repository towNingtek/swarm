"""Durable, isolated provisioning intent ledger; performs NO provisioning.

Trusted composition must inject an opaque ``object()`` worker capability, never
accept one from HTTP. Default None creates a status/admin-intent handle with all
worker transitions disabled. Queue authority is a verified core AdminActor. Workers
receive database-derived site identity; customers only receive redacted status.
A lease expiry fences the worker and requires explicit trusted reconciliation:
retry_safe is an operator/executor assertion that prior effects have been checked,
NOT a claim that this module discovered or rolled them back. No ready_real API
exists. Completion can record only succeeded_simulated (never ready_for_work).
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import math
import re
import secrets
import sqlite3

from support_core import Conflict, InvalidInput, Unauthorized

ERROR_CODES = frozenset({'executor_failed', 'verification_failed', 'cancelled',
                         'reconciliation_failed'})


@dataclass(frozen=True)
class Lease:
    job_id: str
    tenant_id: str
    site_id: str
    site_host: str
    epoch: int
    token: str = field(repr=False)


class SupportSiteJobs:
    def __init__(self, core, *, worker_capability=None, recovery_capability=None):
        for cap in (worker_capability, recovery_capability):
            if cap is not None and type(cap) is not object:
                raise InvalidInput('inject opaque object capabilities')
        if worker_capability is not None and worker_capability is recovery_capability:
            raise InvalidInput('worker and recovery capabilities must differ')
        self.core = core
        self._worker_capability = worker_capability
        self._recovery_capability = recovery_capability
        with core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('CREATE TABLE IF NOT EXISTS support_site_jobs_version (version INTEGER PRIMARY KEY)')
            versions = [r[0] for r in conn.execute('SELECT version FROM support_site_jobs_version')]
            if versions not in ([], [1], [2], [3]):
                raise Conflict('unsupported site jobs schema')
            conn.execute('''CREATE TABLE IF NOT EXISTS support_site_jobs (
                tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
                job_id TEXT NOT NULL UNIQUE, site_id TEXT NOT NULL UNIQUE,
                site_host TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK(status IN ('queued','running',
                    'reconciliation_required','failed','succeeded_simulated',
                    'succeeded_provisioned')),
                epoch INTEGER NOT NULL DEFAULT 0,
                lifecycle_version INTEGER NOT NULL DEFAULT 0,
                lease_until REAL, lease_digest TEXT, error_code TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                site_name TEXT, site_port INTEGER, dns_record_id TEXT,
                CHECK(error_code IS NULL OR error_code IN ('executor_failed',
                    'verification_failed','cancelled','reconciliation_failed')))''')
            conn.execute('''CREATE TABLE IF NOT EXISTS support_site_job_requests (
                request_id TEXT PRIMARY KEY, admin_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL REFERENCES support_site_jobs(tenant_id))''')
            conn.execute('''CREATE TABLE IF NOT EXISTS support_tenant_lifecycle (
                tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
                version INTEGER NOT NULL CHECK(version >= 0))''')
            if versions == [1]:
                conn.execute('ALTER TABLE support_site_jobs ADD COLUMN lifecycle_version INTEGER NOT NULL DEFAULT 0')
                # v1 had no lifecycle evidence. Invalidate every old intent,
                # including completed simulation, rather than assume continuity.
                conn.execute('''UPDATE support_site_jobs SET status='reconciliation_required',
                    epoch=epoch+1,lease_digest=NULL,lease_until=NULL,lifecycle_version=-1''')
            if versions in ([1], [2]):
                # v3 adds real-provisioning evidence. SQLite cannot widen a CHECK
                # constraint in place, so older databases keep the narrower one;
                # that is safe because it only forbids the NEW real-success value.
                for column, kind in (('site_name', 'TEXT'), ('site_port', 'INTEGER'),
                                     ('dns_record_id', 'TEXT')):
                    try:
                        conn.execute(f'ALTER TABLE support_site_jobs ADD COLUMN {column} {kind}')
                    except sqlite3.OperationalError:
                        pass  # Column already present.
            conn.execute('DELETE FROM support_site_jobs_version')
            conn.execute('INSERT INTO support_site_jobs_version VALUES (3)')

    def _worker(self, capability):
        if self._worker_capability is None or capability is not self._worker_capability:
            raise Unauthorized('trusted worker required')

    def _recovery(self, capability):
        if self._recovery_capability is None or capability is not self._recovery_capability:
            raise Unauthorized('trusted recovery required')

    @staticmethod
    def _lifecycle(conn, tenant_id):
        row = conn.execute('SELECT version FROM support_tenant_lifecycle WHERE tenant_id=?', (tenant_id,)).fetchone()
        return row['version'] if row else 0

    def _now(self):
        now = self.core.clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise InvalidInput('invalid clock')
        return now

    @staticmethod
    def _enabled(conn, tenant_id):
        tenant = conn.execute('SELECT * FROM tenants WHERE id=? AND enabled=1', (tenant_id,)).fetchone()
        if tenant is None:
            raise Unauthorized('tenant unavailable')
        return tenant

    def _row(self, conn, job_id, *, recovery=False):
        row = conn.execute('SELECT * FROM support_site_jobs WHERE job_id=?', (job_id,)).fetchone()
        if row is None:
            raise Unauthorized('job unavailable')
        tenant = conn.execute('SELECT * FROM tenants WHERE id=?', (row['tenant_id'],)).fetchone()
        if tenant is None or (not recovery and not tenant['enabled']):
            raise Unauthorized('tenant unavailable')
        if not recovery and tenant['site_host'] != row['site_host']:
            raise Conflict('site identity changed')
        return row

    @staticmethod
    def _view(row):
        value = {key: row[key] for key in ('job_id', 'tenant_id', 'site_id', 'site_host',
                                           'status', 'epoch', 'error_code')}
        keys = row.keys()
        # Real provisioning evidence; absent on simulated or unfinished work.
        value['provisioned'] = row['status'] == 'succeeded_provisioned'
        value['simulated'] = row['status'] == 'succeeded_simulated'
        for column in ('site_name', 'site_port', 'dns_record_id'):
            if column in keys:
                value[column] = row[column]
        return value

    def _expire(self, conn, row, now, *, recovery=False):
        version = self._lifecycle(conn, row['tenant_id'])
        expired = row['status'] == 'running' and row['lease_until'] <= now
        changed = row['lifecycle_version'] != version
        if expired or changed:
            conn.execute('''UPDATE support_site_jobs SET status='reconciliation_required',
                epoch=epoch+1, lease_until=NULL, lease_digest=NULL,lifecycle_version=?,
                updated_at=? WHERE job_id=?''', (version, now, row['job_id']))
            return self._row(conn, row['job_id'], recovery=recovery)
        return row

    def queue(self, admin_actor, tenant_id, request_id):
        """Idempotent admin intent; second request for same tenant reuses ownership.

        Request IDs are globally bound to the verified administrator and tenant;
        conflicting replay rejects rather than acquiring another tenant's job.
        Replay returns current job state (not a historical queued snapshot).
        """
        self.core._admin(admin_actor)
        if (not isinstance(request_id, str) or
                not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id) or
                not isinstance(tenant_id, str)):
            raise InvalidInput('invalid queue request')
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            tenant = self._enabled(conn, tenant_id)
            receipt = conn.execute('SELECT * FROM support_site_job_requests WHERE request_id=?',
                                   (request_id,)).fetchone()
            if receipt and (receipt['tenant_id'] != tenant_id or receipt['admin_id'] != admin_actor.id):
                raise Conflict('queue request conflict')
            row = conn.execute('SELECT * FROM support_site_jobs WHERE tenant_id=?', (tenant_id,)).fetchone()
            now = self._now()
            if row is None:
                # Both identifiers depend solely on the existing tenant identity.
                site_id = 'site-' + tenant['id']
                job_id = 'provision-' + tenant['id']
                conn.execute('''INSERT INTO support_site_jobs
                    (tenant_id,job_id,site_id,site_host,status,created_at,updated_at,lifecycle_version)
                    VALUES (?,?,?,?,'queued',?,?,?)''',
                             (tenant_id, job_id, site_id, tenant['site_host'], now, now,
                              self._lifecycle(conn, tenant_id)))
            else:
                job_id = row['job_id']
            conn.execute('INSERT OR IGNORE INTO support_site_job_requests VALUES (?,?,?)',
                         (request_id, admin_actor.id, tenant_id))
            return self._view(self._expire(conn, self._row(conn, job_id), now))

    def worker_status(self, capability, job_id):
        """Trusted inspection also durably fences expired work."""
        self._worker(capability)
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return self._view(self._expire(conn, self._row(conn, job_id), self._now()))

    def recovery_status(self, capability, job_id):
        """Recovery-only read/fence of disabled or identity-changed ownership.

        Returned identity is the original job ownership, NOT a replacement target.
        This method never claims work, performs cleanup, or asserts readiness.
        """
        self._recovery(capability)
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._expire(conn, self._row(conn, job_id, recovery=True), self._now(), recovery=True)
            result = self._view(row)
            tenant = conn.execute('SELECT enabled,site_host FROM tenants WHERE id=?', (row['tenant_id'],)).fetchone()
            result['tenant_enabled'] = bool(tenant['enabled'])
            result['identity_matches'] = tenant['site_host'] == row['site_host']
            return result

    def queued_job_ids(self, capability):
        """Worker-only enumeration of queued, enabled-tenant jobs (ids only)."""
        self._worker(capability)
        with self.core._connect() as conn:
            conn.execute('BEGIN')
            rows = conn.execute('''SELECT j.job_id FROM support_site_jobs j JOIN tenants t ON t.id=j.tenant_id
                WHERE j.status='queued' AND t.enabled=1 AND t.site_host=j.site_host ORDER BY j.created_at''')
            return [r['job_id'] for r in rows]

    def claim(self, capability, job_id, *, lease_seconds=60):
        """Return Lease only for queued work, otherwise None. No blind reclaim."""
        self._worker(capability)
        if type(lease_seconds) not in (int, float) or not math.isfinite(lease_seconds) or not 0 < lease_seconds <= 3600:
            raise InvalidInput('invalid lease duration')
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            now = self._now()
            row = self._expire(conn, self._row(conn, job_id), now)
            if row['status'] != 'queued':
                return None
            token = secrets.token_urlsafe(32)
            epoch = row['epoch'] + 1
            conn.execute('''UPDATE support_site_jobs SET status='running',epoch=?,lease_until=?,
                lease_digest=?,error_code=NULL,updated_at=? WHERE job_id=?''',
                         (epoch, now+lease_seconds, hashlib.sha256(token.encode()).hexdigest(), now, job_id))
            return Lease(job_id, row['tenant_id'], row['site_id'], row['site_host'], epoch, token)

    def _finish(self, capability, lease, status, error_code=None, evidence=None, on_commit=None):
        self._worker(capability)
        if not isinstance(lease, Lease):
            raise Unauthorized('worker lease required')
        stale = False
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            now = self._now()
            row = self._expire(conn, self._row(conn, lease.job_id), now)
            matches = (row['status'] == 'running' and type(lease.epoch) is int and
                       row['epoch'] == lease.epoch and row['tenant_id'] == lease.tenant_id and
                       row['site_id'] == lease.site_id and row['site_host'] == lease.site_host and
                       isinstance(lease.token, str) and hmac.compare_digest(row['lease_digest'],
                           hashlib.sha256(lease.token.encode()).hexdigest()))
            if not matches:
                stale = True
            else:
                if evidence is None:
                    conn.execute('''UPDATE support_site_jobs SET status=?,error_code=?,lease_until=NULL,
                        lease_digest=NULL,updated_at=? WHERE job_id=?''', (status, error_code, now, lease.job_id))
                else:
                    conn.execute('''UPDATE support_site_jobs SET status=?,error_code=?,lease_until=NULL,
                        lease_digest=NULL,updated_at=?,site_name=?,site_port=?,dns_record_id=?
                        WHERE job_id=?''', (status, error_code, now, *evidence, lease.job_id))
                if on_commit is not None:
                    on_commit(conn, row['tenant_id'], row['site_host'])
                result = self._view(self._row(conn, lease.job_id))
        # Commit expiration fence before reporting stale completion.
        if stale:
            raise Conflict('stale worker lease')
        return result

    def complete_simulated(self, capability, lease):
        return self._finish(capability, lease, 'succeeded_simulated')

    def complete_provisioned(self, capability, lease, *, site_name, site_port, dns_record_id,
                             credential_writer=None):
        """Record a REAL provisioned site. Distinct state from simulation.

        Evidence is written in the same transaction as the state change so a
        `succeeded_provisioned` row can never lack the identity of what was
        actually created.
        """
        if (not isinstance(site_name, str) or not re.fullmatch(r'[a-z0-9-]{1,63}', site_name)
                or type(site_port) is not int or not 1 <= site_port <= 65535
                or (dns_record_id is not None and (not isinstance(dns_record_id, str)
                                                   or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', dns_record_id)))):
            raise InvalidInput('invalid provisioning evidence')
        # credential_writer runs inside the same transaction as the state change,
        # so a provisioned site can never exist without its delivered password.
        return self._finish(capability, lease, 'succeeded_provisioned',
                            evidence=(site_name, site_port, dns_record_id),
                            on_commit=credential_writer)

    def fail(self, capability, lease, error_code):
        if not isinstance(error_code, str) or error_code not in ERROR_CODES:
            raise InvalidInput('unsupported error code')
        # Executor failure may have partial external effects; always reconcile.
        return self._finish(capability, lease, 'reconciliation_required', error_code)

    def reconcile(self, capability, job_id, epoch, *, outcome):
        """Trusted explicit reconciliation; retry_safe attests prior effects checked.

        Does not inspect infra or run anything. No automatic or customer retry.
        Failed and simulated-success states are terminal in this initial API.
        """
        self._recovery(capability)
        if outcome not in ('retry_safe', 'failed') or type(epoch) is not int:
            raise InvalidInput('invalid reconciliation')
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            now = self._now()
            row = self._expire(conn, self._row(conn, job_id, recovery=True), now, recovery=True)
            if outcome == 'retry_safe':
                # Disabled/identity-changed tenants may be inspected or safely
                # closed as failed, never requeued for external provisioning.
                self._row(conn, job_id)
            if row['status'] != 'reconciliation_required' or row['epoch'] != epoch:
                raise Conflict('reconciliation state changed')
            conn.execute('''UPDATE support_site_jobs SET status=?,epoch=epoch+1,
                error_code=?,updated_at=? WHERE job_id=?''',
                         ('queued' if outcome == 'retry_safe' else 'failed',
                          None if outcome == 'retry_safe' else 'reconciliation_failed', now, job_id))
            return self._view(self._row(conn, job_id, recovery=True))

    def customer_status(self, actor):
        """Only status+simulated; derives tenant from live customer capability.

        Expired running work is reported conservatively, without requiring a
        worker sweep. Worker claim/status persists the corresponding epoch fence.
        """
        with self.core._connect() as conn:
            conn.execute('BEGIN')
            principal = self.core._customer(conn, actor)
            row = conn.execute('SELECT * FROM support_site_jobs WHERE tenant_id=?',
                               (principal.tenant_id,)).fetchone()
            status = row['status'] if row else 'not_requested'
            if row:
                # Detect the job drifting from the tenant's current site identity.
                # Compare against the TENANT record, not the session host: the
                # customer authenticates on the platform host, which is a
                # different authority from the site being provisioned.
                tenant = conn.execute('SELECT site_host FROM tenants WHERE id=?',
                                      (principal.tenant_id,)).fetchone()
                if tenant is None or tenant['site_host'] != row['site_host']:
                    raise Conflict('site identity changed')
                if (row['lifecycle_version'] != self._lifecycle(conn, row['tenant_id']) or
                        (status == 'running' and row['lease_until'] <= self._now())):
                    status = 'reconciliation_required'
            # A provisioned site exposes its public host so the customer can
            # actually go there; simulation never gains that field.
            value = {'status': status, 'simulated': status == 'succeeded_simulated',
                     'provisioned': status == 'succeeded_provisioned'}
            if status == 'succeeded_provisioned':
                value['site_host'] = row['site_host']
            return value
