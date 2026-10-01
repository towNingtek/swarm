"""Tell the operator, on Discord, when a customer conversation needs a human.

Sent when:
* a customer writes in a room the operator took over ('coassist' or 'human');
* a customer writes the FIRST message of a room (a new conversation);
* the assistant failed to answer a customer.

At most one notice per room per ``quiet`` seconds, so a customer typing several
lines produces one ping. Delivery is best effort on a background thread: a slow
or failing Discord never delays or breaks the customer's request, and the bot
token is never logged. Customer text is shown as a short preview (Discord is a
third party); mentions are disabled so a customer cannot ping @everyone.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

API = 'https://discord.com/api/v10'
PREVIEW = 100
MODE_TEXT = {'ai': 'AI 自動回覆中', 'coassist': '真人接手', 'human': '只由真人'}


def _preview(text):
    text = ' '.join(str(text).split())
    if len(text) > PREVIEW:
        text = text[:PREVIEW] + '…'
    # Keep the quote on one line and out of code/markdown formatting tricks.
    return text.replace('`', "'")


class DiscordNotifier:
    def __init__(self, token, channel_id, admin_url, *, quiet=120.0, clock=time.monotonic,
                 send=None):
        token, channel_id = (token or '').strip(), (channel_id or '').strip()
        if not token or not channel_id.isdigit():
            raise ValueError('discord notifier needs a bot token and a numeric channel id')
        self._token = token
        self._channel = channel_id
        self.admin_url = admin_url
        self.quiet = quiet
        self._clock = clock
        self._last = {}
        self._lock = threading.Lock()
        # Injected in tests; the default posts to Discord on a daemon thread.
        self._send = send or self._post_async

    def __repr__(self):  # never show the token
        return f'DiscordNotifier(channel={self._channel})'

    def _due(self, room_id):
        now = self._clock()
        with self._lock:
            last = self._last.get(room_id)
            if last is not None and now - last < self.quiet:
                return False
            self._last[room_id] = now
            return True

    def customer_message(self, context, body):
        """context: {room_id, site_host, mode, customer_messages}."""
        first = context.get('customer_messages') == 1
        if context.get('mode') not in ('coassist', 'human') and not first:
            return False
        if not self._due(context['room_id']):
            return False
        head = '🆕 新對話' if first and context.get('mode') == 'ai' else '🔔 需要你回覆'
        self._send(f"{head}：**{context['site_host']}**（{MODE_TEXT.get(context.get('mode'), '')}）\n"
                   f"> {_preview(body)}\n{self.admin_url}")
        return True

    def ai_failed(self, context):
        if not self._due(context['room_id']):
            return False
        self._send(f"⚠️ AI 助手沒能回覆：**{context['site_host']}**，客戶在等。\n{self.admin_url}")
        return True

    def _post_async(self, content):
        threading.Thread(target=self._post, args=(content,), daemon=True).start()

    def _post(self, content):
        data = json.dumps({'content': content[:1900],
                           'allowed_mentions': {'parse': []}}).encode()
        request = urllib.request.Request(
            f'{API}/channels/{self._channel}/messages', data=data, method='POST',
            headers={'Authorization': 'Bot ' + self._token, 'Content-Type': 'application/json',
                     # Discord's documented bot User-Agent format.
                     'User-Agent': 'DiscordBot (https://github.com/towNingtek/swarm, 1)'})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read(1)
        except Exception as exc:
            # Status/type only: never the request (it carries the token).
            print(f'[notify] discord delivery failed: {type(exc).__name__} '
                  f'{getattr(exc, "code", "")}', flush=True)
