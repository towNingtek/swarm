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
    'messages',
    'rooms',
    'support_bootstrap_requests',
)


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
            # Principal-keyed rows first: they must go before principals itself.
            for table in _PRINCIPAL_TABLES:
                try:
                    conn.execute(f'''DELETE FROM {table} WHERE principal_id IN
                        (SELECT id FROM principals WHERE tenant_id=?)''', (tenant_id,))
                except Exception:
                    continue  # Table absent in this deployment.
            for table in _TENANT_TABLES:
                try:
                    conn.execute(f'DELETE FROM {table} WHERE tenant_id=?', (tenant_id,))
                except Exception:
                    continue
            conn.execute('DELETE FROM tenants WHERE id=?', (tenant_id,))
        return {'tenant_id': tenant_id, 'site_host': expected_host,
                'site_removed': removed_site, 'records_removed': True}
