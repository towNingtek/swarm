# OpenAI-compatible stub gateway for the clean-install test only.
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self._send({"object": "list", "data": [{"id": "cloud-fast", "object": "model"}]})
    def do_POST(self):
        n = int(self.headers.get("content-length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        ok = self.headers.get("authorization") == "Bearer sk-test-gateway"
        if not ok: return self._send({"error": "bad key"}, 401)
        self._send({"id": "c1", "object": "chat.completion", "model": body.get("model"), "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "stub answer"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}})
    def log_message(self, *a): pass
ThreadingHTTPServer(("172.17.0.1", 4000), H).serve_forever()
