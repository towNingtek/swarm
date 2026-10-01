"""Site name rules: one DNS label under SWARM_DOMAIN (name.example.com)."""

from __future__ import annotations

import re

RESERVED_NAMES = frozenset({
    "swarm", "admin", "api", "www", "status", "test", "mail", "hub", "app", "dev",
    "stage", "staging", "prod", "smtp", "imap", "pop", "ftp", "ns", "ns1", "ns2",
    "mx", "vpn", "dashboard", "auth", "login", "static", "cdn", "docs", "root",
    "ssh", "platform",
})

NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def validate_name(name: str) -> str:
    """Return the name if valid; raise ValueError with a user-facing message."""
    if name in RESERVED_NAMES:
        raise ValueError(f"站名 {name!r} 是保留字，不可使用")
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"站名 {name!r} 格式不合法：只允許小寫英數與 '-'（不可含點、"
            "不可以 '-' 開頭或結尾，長度 1-63）"
        )
    return name
