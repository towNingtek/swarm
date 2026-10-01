"""Let a customer set their own site's login password from the platform.

Replaces the old model where provisioning generated a password and parked it
in the database for one-time collection. Now nobody holds the generated one;
the customer chooses a password here, the platform hands it straight to the
site controller, and keeps no copy.

Boundaries:

* Only the signed-in customer's own tenant, and only a site this platform
  actually finished provisioning under the tenant's current host.
* "Reuse my platform password" is accepted only after that password verifies
  against the customer's own platform hash, so this is never a way to set a
  site password from a guessed or stolen session without knowing the password.
* The password is never stored, logged or echoed; failures carry no detail.
* One change at a time per platform, and a per-tenant cooldown, because each
  change recreates the site container.
"""
from __future__ import annotations

import threading
import time

from support_core import Conflict, InvalidInput, Unauthorized, _password_matches

MIN_LENGTH = 12
MAX_LENGTH = 1024
COOLDOWN_SECONDS = 30


class SitePasswordBusy(Conflict):
    """Another change is running, or this tenant changed it moments ago."""


class SitePassword:
    def __init__(self, core, *, setter, cooldown_seconds=COOLDOWN_SECONDS, clock=time.monotonic):
        if not callable(setter):
            raise ValueError('setter required')
        self.core = core
        self._setter = setter            # (site_name, password) -> None, raises on failure
        self._lock = threading.Lock()
        self._last = {}
        self._cooldown = cooldown_seconds
        self._clock = clock

    @staticmethod
    def _validate(password):
        if not isinstance(password, str) or not MIN_LENGTH <= len(password) <= MAX_LENGTH:
            raise InvalidInput('invalid site password')
        if any(ord(c) < 32 or ord(c) == 127 for c in password):
            raise InvalidInput('invalid site password')
        return password

    def _target(self, actor):
        with self.core._connect() as conn:
            conn.execute('BEGIN')
            principal = self.core._customer(conn, actor)
            tenant = conn.execute('SELECT site_host, enabled FROM tenants WHERE id=?',
                                  (principal.tenant_id,)).fetchone()
            if tenant is None or not tenant['enabled']:
                raise Conflict('site unavailable')
            job = conn.execute('SELECT status, site_host, site_name FROM support_site_jobs '
                               'WHERE tenant_id=?', (principal.tenant_id,)).fetchone()
            if (job is None or job['status'] != 'succeeded_provisioned'
                    or job['site_host'] != tenant['site_host'] or not job['site_name']):
                raise Conflict('site not provisioned')
            row = conn.execute('SELECT password_hash FROM principals WHERE id=?',
                               (principal.id,)).fetchone()
            return principal.tenant_id, job['site_name'], job['site_host'], row['password_hash']

    def set(self, actor, *, password=None, platform_password=None):
        """Exactly one of password / platform_password. Returns {site_host, username}."""
        if (password is None) == (platform_password is None):
            raise InvalidInput('choose one source')
        tenant_id, site_name, site_host, platform_hash = self._target(actor)
        if platform_password is not None:
            if not isinstance(platform_password, str) or not _password_matches(platform_password,
                                                                               platform_hash):
                raise Unauthorized('platform password mismatch')
            chosen = self._validate(platform_password)
        else:
            chosen = self._validate(password)
        if not self._lock.acquire(blocking=False):
            raise SitePasswordBusy('change in progress')
        try:
            now = self._clock()
            last = self._last.get(tenant_id)
            if last is not None and now - last < self._cooldown:
                raise SitePasswordBusy('changed moments ago')
            self._last[tenant_id] = now
            # Re-check authority right before the external effect: the session
            # may have been revoked or the tenant disabled while we hashed.
            self._target(actor)
            self._setter(site_name, chosen)
        finally:
            self._lock.release()
        return {'site_host': site_host, 'username': 'admin'}


def dsh_setter(site_name, password):
    """Production setter: the site controller's reversible password swap."""
    import _site_path  # noqa: F401
    import dsh_sitectl

    dsh_sitectl.set_site_password(site_name, password)
