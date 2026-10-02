"""Delete a customer tenant and the site this platform provisioned for it.

Destructive and irreversible. The safety rules that matter:

* A site is removed ONLY when this platform's own job record says it provisioned
  that site, and only under its recorded name. An operator-supplied name is
  never used to pick what to destroy, so a typo cannot delete someone else's
  site, and a tenant whose row was tampered with cannot redirect the deletion.
* `dsh_sitectl.cmd_delete` independently refuses anything whose state file is
  not `engine == "dsh"`, so sites managed by anything else cannot be reached
  through this path at all.
* The tenant is disabled FIRST, in its own transaction. If teardown then fails
  half way, the customer cannot keep using a site that is being dismantled, and
  the failure is reported rather than swallowed.
* Database rows are removed only after external resources are gone. Losing the
  record first would orphan a running container with no way to find it again.
"""
from __future__ import annotations

import sys
from pathlib import Path

import _site_path  # noqa: E402,F401  (site/ on sys.path)

from support_core import Conflict, InvalidInput

# Verified reference graph (see PRAGMA foreign_key_list):
#   principals, invites, support_site_jobs, support_tenant_lifecycle,
#   support_site_credentials  -> tenants
#   sessions, support_model_settings, support_onboarding_ack -> principals
#   support_site_job_requests -> support_site_jobs
# Rows keyed by principal must be deleted via a subquery: they have no
# tenant_id column, so a tenant_id DELETE would silently match nothing.
_PRINCIPAL_TABLES = ('sessions', 'support_model_settings', 'support_onboarding_ack')
_TENANT_TABLES = (
    'support_site_job_requests',   # -> support_site_jobs
    'support_site_credentials',
    'support_site_jobs',
    'principals',
    'invites',
    'support_tenant_lifecycle',
    'support_quota_policies',
    'support_quota_ledger',
    'support_site_model_usage',
    'support_site_model_tokens',
    'support_site_templates',
    'rooms',
)
# Room children have no tenant_id; they are deleted by room, before rooms.
# runs references messages, so it goes first.
_ROOM_TABLES = ('runs', 'internal_notes', 'messages')


class TeardownError(RuntimeError):
    """Teardown did not complete; external resources may remain."""


class SiteTeardown:
    def __init__(self, core, *, enabled=False, purge_data=True):
        if enabled is not True:
            raise InvalidInput('teardown must be enabled explicitly')
        self.core = core
        self.purge_data = purge_data

    def _provisioned_site(self, conn, tenant_id):
        """Return the site name this platform recorded, or None.

        Reads `site_name` written at provisioning time, not the tenant's host:
        the recorded name is what was actually created.
        """
        try:
            row = conn.execute(
                'SELECT status, site_name FROM support_site_jobs WHERE tenant_id=?',
                (tenant_id,)).fetchone()
        except Exception:
            return None
        if row is None or row['status'] != 'succeeded_provisioned':
            return None
        return row['site_name']

    def delete_tenant(self, admin_actor, tenant_id, *, expected_host):
        """Remove the site (if any) and then the tenant's records.

        expected_host must match the stored tenant host: the caller states what
        it believes it is deleting, and a mismatch aborts rather than proceeds.
        """
        self.core._admin(admin_actor)
        if not isinstance(tenant_id, str) or not tenant_id:
            raise InvalidInput('invalid tenant')

        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            tenant = conn.execute('SELECT id, site_host FROM tenants WHERE id=?',
                                  (tenant_id,)).fetchone()
            if tenant is None:
                raise InvalidInput('unknown tenant')
            if tenant['site_host'] != expected_host:
                raise Conflict('tenant host changed; refusing to delete')
            site_name = self._provisioned_site(conn, tenant_id)

        # Disable first and separately: a partially torn-down site must not stay
        # usable, and this must be durable even if the next step fails.
        self.core.set_tenant_enabled(admin_actor, tenant_id, False)

        removed_site = None
        if site_name:
            import argparse

            import dsh_sitectl

            try:
                dsh_sitectl.cmd_delete(argparse.Namespace(name=site_name,
                                                          purge_data=self.purge_data))
                removed_site = site_name
            except Exception as exc:
                # Keep the tenant rows: they are the only record of what exists.
                raise TeardownError(
                    f'site teardown failed for {site_name!r}; tenant disabled and records '
                    f'kept for recovery: {exc}') from exc

        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            # Only a table that is absent in this deployment is skipped. Any
            # other error (a foreign key, a missing column) aborts the whole
            # transaction, so the records stay consistent and a retry works.
            # Room and principal children first, then tenant rows, then tenant.
            for table in _ROOM_TABLES:
                if table in present:
                    conn.execute(f'''DELETE FROM {table} WHERE room_id IN
                        (SELECT id FROM rooms WHERE tenant_id=?)''', (tenant_id,))
            if 'invite_conflicts' in present:
                conn.execute('''DELETE FROM invite_conflicts WHERE digest IN
                    (SELECT digest FROM invites WHERE tenant_id=?)''', (tenant_id,))
            for table in _PRINCIPAL_TABLES:
                if table in present:
                    conn.execute(f'''DELETE FROM {table} WHERE principal_id IN
                        (SELECT id FROM principals WHERE tenant_id=?)''', (tenant_id,))
            for table in _TENANT_TABLES:
                if table in present:
                    conn.execute(f'DELETE FROM {table} WHERE tenant_id=?', (tenant_id,))
            if 'support_bootstrap_requests' in present:
                # Idempotency receipts: the tenant is only inside the JSON result.
                conn.execute("DELETE FROM support_bootstrap_requests "
                             "WHERE json_extract(result, '$.tenant.id')=?", (tenant_id,))
            conn.execute('DELETE FROM tenants WHERE id=?', (tenant_id,))
        return {'tenant_id': tenant_id, 'site_host': expected_host,
                'site_removed': removed_site, 'records_removed': True}
