"""Pause and resume a customer site from the operator overview.

Some sites are temporary (a class, a workshop): between sessions they should
not use CPU, memory or model quota, yet their data must stay. Pausing stops the
site's container; nothing is deleted. The container's restart policy is
``unless-stopped``, so a paused site also stays down across host reboots.
Resuming starts the same container again.

Boundaries:
* Administrator only, and only a site this platform finished provisioning under
  the tenant's current host (the recorded site name, never operator input).
* The pause is recorded here so the overview can show 「已暫停」 instead of an
  offline alert, and so the site scheduler skips the site while it is paused.
* One change at a time platform-wide.
"""
from __future__ import annotations

import threading
import time

from support_core import Conflict, InvalidInput


class SitePauseBusy(Conflict):
    """Another pause or resume is running."""


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS support_site_pause (
        tenant_id TEXT PRIMARY KEY,
        paused_at REAL NOT NULL)''')


def paused_tenants(conn):
    """tenant_id -> paused_at, for every paused site (empty when never used)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='support_site_pause'").fetchone():
        return {}
    return {r['tenant_id']: r['paused_at']
            for r in conn.execute('SELECT tenant_id, paused_at FROM support_site_pause')}


class SitePause:
    def __init__(self, core, *, stopper=None, starter=None, now=time.time):
        self.core = core
        self._stop = stopper or _docker_stop
        self._start = starter or _docker_start
        self._now = now
        self._lock = threading.Lock()
        with core._connect() as conn:
            ensure_schema(conn)

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

    def check_host(self, actor, tenant_id, expected_host):
        """Refuse unless the caller names the tenant's current site host."""
        self.core._admin(actor)
        if not isinstance(expected_host, str) or not expected_host:
            raise InvalidInput('invalid host')
        with self.core._connect() as conn:
            tenant = conn.execute('SELECT site_host FROM tenants WHERE id=?', (tenant_id,)).fetchone()
        if tenant is None:
            raise InvalidInput('unknown tenant')
        if tenant['site_host'] != expected_host.strip().lower():
            raise Conflict('host changed')

    def _change(self, actor, tenant_id, paused):
        site_name = self._site(actor, tenant_id)
        if not self._lock.acquire(blocking=False):
            raise SitePauseBusy('change in progress')
        try:
            self._site(actor, tenant_id)  # re-check right before the external effect
            if paused:
                # Record first: if the stop fails half way, the overview still
                # says paused (no false offline alert) and resume stays offered.
                with self.core._connect() as conn:
                    conn.execute('INSERT OR REPLACE INTO support_site_pause (tenant_id, paused_at) '
                                 'VALUES (?, ?)', (tenant_id, self._now()))
                self._stop(site_name)
            else:
                # Start first: the record is cleared only once the site runs.
                self._start(site_name)
                with self.core._connect() as conn:
                    conn.execute('DELETE FROM support_site_pause WHERE tenant_id=?', (tenant_id,))
        finally:
            self._lock.release()
        return {'paused': paused}

    def pause(self, actor, tenant_id):
        return self._change(actor, tenant_id, True)

    def resume(self, actor, tenant_id):
        return self._change(actor, tenant_id, False)


def _docker_stop(site_name):
    import _site_path  # noqa: F401  (site/ on sys.path)
    import common

    common.run(['docker', 'stop', common.container_name(site_name)])


def _docker_start(site_name):
    import _site_path  # noqa: F401  (site/ on sys.path)
    import common

    common.run(['docker', 'start', common.container_name(site_name)])
