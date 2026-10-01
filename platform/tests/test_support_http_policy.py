import unittest
from support_http_policy import EdgePolicy
from support_core import Unauthorized, InvalidInput


class EdgePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = EdgePolicy('https://acme.example')
        self.valid = [(b'host', b'acme.example'), (b'origin', b'https://acme.example')]

    def test_same_origin_post(self):
        self.assertEqual(self.policy.validate('POST', self.valid), 'acme.example')

    def test_get_without_origin(self):
        self.assertEqual(self.policy.validate('GET', self.valid[:1]), 'acme.example')

    def test_missing_or_cross_origin_post(self):
        for headers in (self.valid[:1], self.valid[:1] + [(b'origin', b'https://other.example')]):
            with self.assertRaises(Unauthorized):
                self.policy.validate('POST', headers)

    def test_duplicate_host_and_origin_rejected(self):
        for duplicate in self.valid:
            with self.assertRaises(Unauthorized):
                self.policy.validate('POST', self.valid + [duplicate])

    def test_forged_forwarded_headers_do_not_grant_authority(self):
        headers = [(b'host', b'other.example'), (b'x-forwarded-host', b'acme.example'),
                   (b'cf-visitor', b'{"scheme":"https"}'), (b'origin', b'https://acme.example')]
        with self.assertRaises(Unauthorized):
            self.policy.validate('POST', headers)

    def test_cross_site_fetch_rejected(self):
        with self.assertRaises(Unauthorized):
            self.policy.validate('GET', self.valid + [(b'sec-fetch-site', b'cross-site')])

    def test_cross_site_top_level_navigation_is_allowed(self):
        """A customer opening an invite link from mail or chat arrives cross-site.

        Arriving from a sibling subdomain yields 'same-site', which must be
        treated no more strictly than 'cross-site'.
        """
        for value in (b'cross-site', b'same-site'):
            navigation = [(b'sec-fetch-site', value), (b'sec-fetch-mode', b'navigate'),
                          (b'sec-fetch-dest', b'document')]
            self.assertEqual(self.policy.validate('GET', self.valid + navigation),
                             'acme.example', value)
            self.assertEqual(self.policy.validate('HEAD', self.valid + navigation),
                             'acme.example', value)

    def test_same_site_form_post_is_allowed_with_matching_origin(self):
        """Submitting the login form after arriving from a sibling subdomain."""
        post = [(b'sec-fetch-site', b'same-site'), (b'sec-fetch-mode', b'navigate'),
                (b'sec-fetch-dest', b'document')]
        self.assertEqual(self.policy.validate('POST', self.valid + post), 'acme.example')
        # Origin is still mandatory and must match exactly.
        bare = [h for h in self.valid if h[0] != b'origin']
        with self.assertRaises(Unauthorized):
            self.policy.validate('POST', bare + post)
        with self.assertRaises(Unauthorized):
            self.policy.validate('POST', bare + post + [(b'origin', b'https://evil.example')])

    def test_cross_site_state_change_is_still_refused(self):
        """An attacker on another registrable domain is classified cross-site."""
        post = [(b'sec-fetch-site', b'cross-site'), (b'sec-fetch-mode', b'navigate'),
                (b'sec-fetch-dest', b'document')]
        for method in ('POST', 'PUT', 'DELETE'):
            with self.assertRaises(Unauthorized, msg=method):
                self.policy.validate(method, self.valid + post)

    def test_response_policy_does_not_defeat_the_origin_check(self):
        """Regression: 'no-referrer' made browsers send `Origin: null`.

        Browsers derive a form submission's Origin from the referrer policy, so
        'no-referrer' produced `Origin: null`, which can never match and made
        every real login impossible while curl (which sends its own Origin)
        appeared to work.
        """
        from support_http_policy import SENSITIVE_RESPONSE_HEADERS
        self.assertEqual(SENSITIVE_RESPONSE_HEADERS['Referrer-Policy'], 'same-origin')
        # A literal null Origin is still refused rather than treated as absent.
        bare = [h for h in self.valid if h[0] != b'origin']
        with self.assertRaises(Unauthorized):
            self.policy.validate('POST', bare + [(b'origin', b'null')])

    def test_cross_site_exception_is_narrow(self):
        navigation = [(b'sec-fetch-site', b'cross-site'), (b'sec-fetch-mode', b'navigate'),
                      (b'sec-fetch-dest', b'document')]
        # A foreign Origin never qualifies, whatever the method or navigation.
        for method in ('GET', 'POST', 'PUT', 'DELETE'):
            with self.assertRaises(Unauthorized, msg=method):
                self.policy.validate(method, self.valid + navigation +
                                     [(b'origin', b'https://evil.example')])
        # Missing or wrong companion headers must not widen the exception:
        # a subresource, frame or fetch is still refused.
        for partial in (
            [(b'sec-fetch-site', b'same-site')],
            [(b'sec-fetch-site', b'same-site'), (b'sec-fetch-mode', b'cors'),
             (b'sec-fetch-dest', b'document')],
            [(b'sec-fetch-site', b'cross-site')],
            [(b'sec-fetch-site', b'cross-site'), (b'sec-fetch-mode', b'navigate')],
            [(b'sec-fetch-site', b'cross-site'), (b'sec-fetch-dest', b'document')],
            [(b'sec-fetch-site', b'cross-site'), (b'sec-fetch-mode', b'cors'),
             (b'sec-fetch-dest', b'document')],
            [(b'sec-fetch-site', b'cross-site'), (b'sec-fetch-mode', b'navigate'),
             (b'sec-fetch-dest', b'iframe')],
            [(b'sec-fetch-site', b'cross-site'), (b'sec-fetch-mode', b'navigate'),
             (b'sec-fetch-dest', b'image')],
        ):
            with self.assertRaises(Unauthorized, msg=str(partial)):
                self.policy.validate('GET', self.valid + partial)
        # Duplicated headers cannot smuggle the exception past the check.
        with self.assertRaises(Unauthorized):
            self.policy.validate('GET', self.valid + navigation +
                                 [(b'sec-fetch-dest', b'document')])
        # A cross-site navigation carrying a foreign Origin is still refused.
        with self.assertRaises(Unauthorized):
            self.policy.validate('GET', self.valid + navigation +
                                 [(b'origin', b'https://evil.example')])

    def test_config_rejects_untrusted_origin_shapes(self):
        for origin in ('http://acme.example', 'https://u@acme.example',
                       'https://acme.example/path', 'https://acme.example:443',
                       'https://acme.example?x=1'):
            with self.assertRaises(InvalidInput):
                EdgePolicy(origin)


if __name__ == '__main__':
    unittest.main()
