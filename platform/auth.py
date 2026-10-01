"""swarm-admin 登入（M1，issue #267）。

admin 帳密 + HMAC 簽章 session cookie + 登入失敗鎖定。
設定來源（環境變數）：
  ADMIN_USERNAME        登入帳號（預設 admin）
  ADMIN_PASSWORD        登入密碼（未設定時所有登入一律失敗 = fail-closed）
  ADMIN_SESSION_SECRET  cookie 簽章密鑰（未設定時每次啟動隨機產生）
  ADMIN_SESSION_TTL     session 有效秒數（預設 43200 = 12 小時）
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SESSION_SECRET = os.environ.get("ADMIN_SESSION_SECRET") or secrets.token_hex(32)
SESSION_TTL = int(os.environ.get("ADMIN_SESSION_TTL", "43200"))
COOKIE_NAME = "swarm_admin_session"

MAX_FAILURES = 5
LOCKOUT_SECONDS = 900
_failures: dict[str, list[float]] = {}

PUBLIC_PATHS = frozenset({"/login", "/favicon.ico"})


def _sign(payload: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_session() -> str:
    payload = f"{ADMIN_USERNAME}:{int(time.time()) + SESSION_TTL}"
    return f"{payload}:{_sign(payload)}"


def verify_session(token: str | None) -> bool:
    if not token:
        return False
    username, _, rest = token.partition(":")
    expires, _, signature = rest.partition(":")
    payload = f"{username}:{expires}"
    if not signature or not hmac.compare_digest(_sign(payload), signature):
        return False
    try:
        return username == ADMIN_USERNAME and int(expires) > time.time()
    except ValueError:
        return False


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def locked_remaining(ip: str) -> int:
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < LOCKOUT_SECONDS]
    _failures[ip] = recent
    if len(recent) >= MAX_FAILURES:
        return int(LOCKOUT_SECONDS - (now - recent[0]))
    return 0


def record_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())


def clear_failures(ip: str) -> None:
    _failures.pop(ip, None)


def check_credentials(username: str, password: str) -> bool:
    if not ADMIN_PASSWORD:
        return False
    return hmac.compare_digest(username, ADMIN_USERNAME) and hmac.compare_digest(
        password, ADMIN_PASSWORD
    )


def route_path(request: Request) -> str:
    """去掉 root_path 前綴的路徑。

    ASGI 規格下 scope["path"] 含 root_path（uvicorn 會補回來），
    反代掛在 /admin 這種子路徑時，PUBLIC_PATHS 比對必須先剝掉前綴，
    否則 /admin/login 不會被視為公開頁，會無限 302 回自己。
    """
    path = request.url.path
    root = request.scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root):] or "/"
    return path


async def auth_middleware(request: Request, call_next):
    path = route_path(request)
    if path in PUBLIC_PATHS:
        return await call_next(request)
    if verify_session(request.cookies.get(COOKIE_NAME)):
        return await call_next(request)
    if path.startswith(("/api/", "/hub/api/")):
        return JSONResponse({"error": "未登入"}, status_code=401)
    root = request.scope.get("root_path", "")
    return RedirectResponse(url=f"{root}/login", status_code=302)
