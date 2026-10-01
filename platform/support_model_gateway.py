"""OpenAI-compatible gateway adapter for the platform support model.

The credential belongs to the platform and never leaves this process: it is not
written to a customer site, a browser bundle, or any response. Only the reply
text and the provider's own usage counts cross this boundary.

Deliberate properties:

* Bounded: connect/read timeouts, a response size cap, and a single attempt.
  A support assistant that hangs is worse than one that says it is unavailable,
  and silent retries would double-charge a metered account.
* Honest about usage: when the provider omits counts they stay ``None`` so the
  ledger records "unknown", never zero (issue: unknown usage must be marked).
* No fallback: a failure raises ModelUnavailable. It never degrades to a
  fabricated answer, and never quietly switches to another model or key.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from support_model import ModelUnavailable, Reply, validate_request

# A support answer should arrive while the customer is still reading the page.
DEFAULT_TIMEOUT = 30.0
# Enough for a long answer, small enough that a broken upstream cannot exhaust
# memory before the size check runs.
MAX_RESPONSE_BYTES = 256 * 1024


TRUNCATED_NOTE = '…\n（回覆超過長度上限被截斷，請再問一次或把問題拆小。）'


class GatewayModel:
    def __init__(self, base_url, api_key, model, *, timeout=DEFAULT_TIMEOUT,
                 opener=None):
        if not isinstance(base_url, str) or not base_url.startswith(('http://', 'https://')):
            raise ValueError('gateway base url must be http(s)')
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError('gateway api key is required')
        if not isinstance(model, str) or not model.strip():
            raise ValueError('gateway model id is required')
        if type(timeout) not in (int, float) or not 1 <= timeout <= 120:
            raise ValueError('invalid gateway timeout')
        self.base_url = base_url.rstrip('/')
        self.model = model
        self._key = api_key.strip()
        self.timeout = timeout
        # Injectable for tests; production uses a plain opener with no proxies
        # or redirect following beyond urllib's defaults.
        self._opener = opener or urllib.request.build_opener()

    def reply(self, turns, *, max_output_tokens):
        validate_request(turns, max_output_tokens)
        payload = json.dumps({
            'model': self.model,
            'messages': [{'role': t.role, 'content': t.content} for t in turns],
            'max_tokens': max_output_tokens,
            # Deterministic enough to be reviewable; this is setup guidance,
            # not creative writing.
            'temperature': 0.2,
            'stream': False,
        }).encode('utf-8')
        request = urllib.request.Request(
            self.base_url + '/chat/completions', data=payload, method='POST',
            headers={'Authorization': f'Bearer {self._key}',
                     'Content-Type': 'application/json',
                     'Accept': 'application/json'})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            # Never surface the provider's body: it can echo the request, and
            # upstream errors have carried key fragments before.
            raise ModelUnavailable(
                f'support model gateway refused the request (HTTP {error.code})') from None
        except Exception:
            raise ModelUnavailable('support model gateway is unreachable') from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise ModelUnavailable('support model response was too large')
        try:
            data = json.loads(body)
            first = data['choices'][0]
            text = first['message']['content']
            finish = first.get('finish_reason')
        except Exception:
            raise ModelUnavailable('support model returned an unusable response') from None
        if not isinstance(text, str) or not text.strip():
            raise ModelUnavailable('support model returned an empty response')
        if finish == 'length':
            # Cut off by the output limit: say so, never present half a
            # sentence as the whole answer.
            text = text.rstrip() + TRUNCATED_NOTE

        usage = data.get('usage') or {}

        def count(name):
            value = usage.get(name)
            # Unknown must stay unknown: reporting 0 would understate spend.
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return value

        return Reply(text, self.model, count('prompt_tokens'),
                     count('completion_tokens'), simulated=False)
