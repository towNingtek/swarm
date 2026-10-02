"""Scripted OpenAI-compatible model for demo recordings. No real AI.

Answers are picked by keywords in the last user message and streamed in
small pieces when the request asks for a stream, so a recording looks
natural. Listens on DEMO_GATEWAY_ADDR (default 172.17.0.1:4000) and accepts
only the key in DEMO_GATEWAY_KEY (default sk-test-gateway).
"""
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = os.environ.get('DEMO_GATEWAY_KEY', 'sk-test-gateway')
HOST, PORT = os.environ.get('DEMO_GATEWAY_ADDR', '172.17.0.1:4000').rsplit(':', 1)
DELAY = float(os.environ.get('DEMO_DELAY', '0.03'))

SCRIPT = [
    (r'新手|公司|開始', '好的，我們先建立你的第一間公司。\n\n'
     '1. **公司名稱**：街角咖啡\n'
     '2. **AI 員工**：店長助理（排班、進貨）、行銷小編（社群貼文）\n'
     '3. **排程**：每週一 09:00 由行銷小編草擬本週貼文\n\n'
     '確認沒問題的話，我就把它們寫進辦公室設定。要我先讓行銷小編試寫第一篇貼文嗎？'),
    (r'貼文|社群|行銷', '這是本週的第一篇貼文草稿：\n\n'
     '> ☕ 秋天第一杯桂花拿鐵上市！\n'
     '> 週一到週五 14:00 前點任一飲品，第二杯半價。\n'
     '> 帶自己的杯子再折 5 元。\n\n'
     '要的話我可以把它存成草稿，週一 09:00 的排程再接手整理。'),
]


def answer(messages):
    said = ''
    for message in reversed(messages):
        if message.get('role') != 'user':
            continue
        content = message.get('content')
        text = content if isinstance(content, str) else ' '.join(
            p.get('text', '') for p in content or [] if isinstance(p, dict))
        if 'runtime context' in text[:200].lower() or '<system-reminder>' in text[:200]:
            continue
        said = text
        break
    for pattern, reply in SCRIPT:
        if re.search(pattern, said):
            return reply
    return '收到，我會依照目前的辦公室設定繼續協助你。'


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._json({'object': 'list', 'data': [{'id': 'cloud-fast', 'object': 'model'}]})

    def do_POST(self):
        length = int(self.headers.get('content-length') or 0)
        request = json.loads(self.rfile.read(length) or b'{}')
        if self.headers.get('authorization') != 'Bearer ' + KEY:
            return self._json({'error': 'bad key'}, 401)
        text = answer(request.get('messages') or [])
        usage = {'prompt_tokens': 120, 'completion_tokens': len(text), 'total_tokens': 120 + len(text)}
        base = {'id': 'demo', 'created': int(time.time()), 'model': request.get('model')}
        if not request.get('stream'):
            return self._json({**base, 'object': 'chat.completion', 'usage': usage, 'choices': [
                {'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': text}}]})
        self.send_response(200)
        self.send_header('content-type', 'text/event-stream')
        self.end_headers()

        def send(obj):
            self.wfile.write(b'data: ' + json.dumps(obj, ensure_ascii=False).encode() + b'\n\n')
            self.wfile.flush()
        chunk = {**base, 'object': 'chat.completion.chunk'}
        for i in range(0, len(text), 4):
            send({**chunk, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': text[i:i + 4]},
                                        'finish_reason': None}]})
            time.sleep(DELAY)
        send({**chunk, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': usage})
        self.wfile.write(b'data: [DONE]\n\n')

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer((HOST, int(PORT)), Handler).serve_forever()
