"""Pure edge policy shared by future ASGI adapters.

Public customer traffic must reach a dedicated configured authority. Forwarded
headers never change that authority. This validates requests, not identities.
"""
from urllib.parse import urlsplit
from support_core import Unauthorized, InvalidInput, normalize_host


class EdgePolicy:
    def __init__(self, public_origin):
        parsed = urlsplit(public_origin)
        if (parsed.scheme != 'https' or parsed.username or parsed.password or
                parsed.port is not None or parsed.path not in ('', '/') or
                parsed.query or parsed.fragment or not parsed.hostname):
            raise InvalidInput('an HTTPS DNS origin without port is required')
        self.host = normalize_host(parsed.hostname)
        self.origin = 'https://' + self.host

    def validate(self, method, headers):
        # Headers must retain duplicates (ASGI raw headers), not collapse them.
        values = {}
        for key, value in headers:
            key = key.decode('latin1') if isinstance(key, bytes) else key
            value = value.decode('latin1') if isinstance(value, bytes) else value
            values.setdefault(key.lower(), []).append(value)
        if values.get('host') != [self.host]:
            raise Unauthorized('invalid authority')
        origin = values.get('origin')
        if origin is not None and origin != [self.origin]:
            raise Unauthorized('invalid origin')
        if method.upper() not in ('GET', 'HEAD', 'OPTIONS') and origin != [self.origin]:
            raise Unauthorized('origin required')
        site = values.get('sec-fetch-site', ['same-origin'])
        if site not in (['same-origin'], ['none']):
            # Two legitimate arrivals are not 'same-origin':
            #   cross-site - opening an invite link from mail or chat
            #   same-site  - navigating from a sibling subdomain
            # Permit only a top-level DOCUMENT NAVIGATION in either case: never a
            # subresource, frame, or fetch/CORS request. Requiring all three
            # headers means a stripped or forged single header cannot widen this.
            navigation = (site in (['cross-site'], ['same-site'])
                          and values.get('sec-fetch-mode') == ['navigate']
                          and values.get('sec-fetch-dest') == ['document'])
            if not navigation:
                raise Unauthorized('cross-site request refused')
            if method.upper() not in ('GET', 'HEAD'):
                # A form POST is state-changing, so it additionally requires an
                # exactly-matching Origin (already enforced above) AND that the
                # browser itself classified the request as same-site. An attacker
                # on another registrable domain gets 'cross-site', so this does
                # not open cross-origin CSRF; it only allows a submission that
                # began on one of our own subdomains.
                if site != ['same-site']:
                    raise Unauthorized('cross-site state change refused')
                if origin != [self.origin]:
                    raise Unauthorized('origin required')
        return self.host


SENSITIVE_RESPONSE_HEADERS = {
    'Cache-Control': 'no-store',
    # NOT 'no-referrer': browsers derive the Origin header of a form submission
    # from the referrer policy, so 'no-referrer' makes them send `Origin: null`,
    # which can never satisfy the exact-match CSRF check above. 'same-origin'
    # keeps referrers (and Origin) for our own requests while still sending
    # nothing to other sites, so invite tokens in a URL are not leaked outward.
    'Referrer-Policy': 'same-origin',
    'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': "default-src 'self'; frame-ancestors 'none'; base-uri 'none'",
}
