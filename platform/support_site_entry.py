"""Send an authenticated customer into their own site without a second password.

The customer already proved who they are on the platform. Rather than handing
them the site's password, the platform mints a short-lived, single-use ticket
that the site verifies with a shared secret, and redirects them there.

Boundaries this keeps:

* The ticket authorises entry to ONE site and expires in seconds. It carries no
  password, and the site burns the nonce on first use, so a leaked URL in
  history or a proxy log is useless moments later.
* The signing secret is not the site password or its hash. Holding it cannot
  reveal or verify the password, so the platform never becomes a password
  oracle for sites it provisioned.
* Entry is refused unless this platform actually provisioned that site for that
  customer's tenant, and the tenant is still enabled.
* The site password still exists for operator recovery; it simply stops being
  something the customer must know.
* Each site verifies with its OWN key, derived from the platform master
  secret and the site host (``site_entry_secret``). A site runs customer code
  and can read its own key; that key cannot mint tickets for any other site.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from support_core import Conflict, InvalidInput

# Deliberately short: this is a redirect the browser follows immediately.
TICKET_TTL_SECONDS = 60


def site_entry_secret(master: str, site_host: str) -> str:
    """The key one site uses to verify its entry tickets (64 hex chars)."""
    if not isinstance(master, str) or len(master) < 32:
        raise InvalidInput('entry secret must be at least 32 characters')
    if not isinstance(site_host, str) or not site_host or site_host != site_host.strip().lower():
        raise InvalidInput('invalid site host')
    return hmac.new(master.encode('utf-8'), b'dsh-site-entry\0' + site_host.encode('ascii'),
                    hashlib.sha256).hexdigest()


class SiteEntry:
    def __init__(self, core, *, entry_secret, ttl_seconds=TICKET_TTL_SECONDS):
        if not isinstance(entry_secret, str) or len(entry_secret) < 32:
            raise InvalidInput('entry secret must be at least 32 characters')
        if type(ttl_seconds) not in (int, float) or not 5 <= ttl_seconds <= 300:
            raise InvalidInput('invalid ticket ttl')
        self.core = core
        self._secret = entry_secret.encode('utf-8')
        self.ttl = ttl_seconds

    def _sign(self, expiry_ms, nonce, site_host):
        key = site_entry_secret(self._secret.decode('utf-8'), site_host).encode('ascii')
        digest = hmac.new(key, f'{expiry_ms}.{nonce}'.encode('ascii'),
                          hashlib.sha256).digest()
        import base64
        return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')

    def entry_url(self, actor):
        """Return the one-time URL for this customer's provisioned site."""
        with self.core._connect() as conn:
            conn.execute('BEGIN')
            principal = self.core._customer(conn, actor)
            tenant = conn.execute('SELECT site_host, enabled FROM tenants WHERE id=?',
                                  (principal.tenant_id,)).fetchone()
            if tenant is None or not tenant['enabled']:
                raise Conflict('site unavailable')
            job = conn.execute(
                'SELECT status, site_host FROM support_site_jobs WHERE tenant_id=?',
                (principal.tenant_id,)).fetchone()
            # Only a site this platform actually finished provisioning, and only
            # under the tenant's current host: a changed host means the record
            # no longer describes what exists.
            if (job is None or job['status'] != 'succeeded_provisioned'
                    or job['site_host'] != tenant['site_host']):
                raise Conflict('site not provisioned')
            host = tenant['site_host']

        expiry_ms = int((time.time() + self.ttl) * 1000)
        nonce = secrets.token_urlsafe(24)
        ticket = f'{expiry_ms}.{nonce}.{self._sign(expiry_ms, nonce, host)}'
        return f'https://{host}/auth/enter?ticket={ticket}'
