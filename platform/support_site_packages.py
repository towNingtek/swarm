"""Operator-chosen extra system packages for a customer site.

The site runs unprivileged (no root, no sudo, all capabilities dropped), so its
AI cannot install software. The operator lists packages here; the platform
builds a derived image as root at build time and recreates the container with
the same safety as a password change (old container kept until the new one
passes an authenticated self-test). Nothing about the site's privileges changes.

Boundaries:
* Administrator only, and only a site this platform finished provisioning under
  the tenant's current host (the recorded site name, never operator input).
* One change at a time platform-wide: each change builds an image and
  recreates a container.
"""
from __future__ import annotations

import threading

from support_core import Conflict, InvalidInput


class SitePackagesBusy(Conflict):
    """Another package change is running."""


class SitePackages:
    def __init__(self, core, *, setter=None, reader=None):
        self.core = core
        self._setter = setter or _dsh_setter
        self._reader = reader or _dsh_reader
        self._lock = threading.Lock()

    def _site(self, actor, tenant_id):
        self.core._admin(actor)
        if not isinstance(tenant_id, str) or not tenant_id:
            raise InvalidInput('invalid tenant')
        with self.core._connect() as conn:
            tenant = conn.execute('SELECT site_host FROM tenants WHERE id=?', (tenant_id,)).fetchone()
            if tenant is None:
                raise InvalidInput('unknown tenant')
            has_jobs = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                    "AND name='support_site_jobs'").fetchone()
            job = conn.execute('SELECT status, site_host, site_name FROM support_site_jobs '
                               'WHERE tenant_id=?', (tenant_id,)).fetchone() if has_jobs else None
        if (job is None or job['status'] != 'succeeded_provisioned'
                or job['site_host'] != tenant['site_host'] or not job['site_name']):
            raise Conflict('site not provisioned')
        return job['site_name']

    def read(self, actor, tenant_id):
        return {'packages': self._reader(self._site(actor, tenant_id))}

    def set(self, actor, tenant_id, packages):
        if not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
            raise InvalidInput('invalid packages')
        site_name = self._site(actor, tenant_id)
        if not self._lock.acquire(blocking=False):
            raise SitePackagesBusy('change in progress')
        try:
            self._site(actor, tenant_id)  # re-check right before the external effect
            result = self._setter(site_name, packages)
        finally:
            self._lock.release()
        return {'packages': list(result.get('packages', []))}


def _dsh_setter(site_name, packages):
    import _site_path  # noqa: F401  (site/ on sys.path)
    import dsh_sitectl

    try:
        return dsh_sitectl.set_site_packages(site_name, packages)
    except dsh_sitectl.SiteError as exc:
        if str(exc).startswith(('invalid package name', 'at most', 'packages must')):
            raise InvalidInput('invalid packages') from exc
        raise


def _dsh_reader(site_name):
    import _site_path  # noqa: F401  (site/ on sys.path)
    import dsh_sitectl

    return dsh_sitectl.site_packages(site_name)
