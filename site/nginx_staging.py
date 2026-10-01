"""Apply nginx configs through the root-owned staging watcher.

This provisioner runs in an environment where setuid is disabled, so `sudo`
cannot obtain root at all. Instead a root systemd path unit
(`swarm-nginx-apply.path` → `/usr/local/sbin/swarm-nginx-apply`) watches
`$SWARM_NGINX_STAGING` and applies only well-formed `swarm-site-<name>.conf`
files, refusing to overwrite or delete configs it does not own.

The handoff is asynchronous, so writing a staged file is NOT success. Each
call here waits for the watcher to move the staged file to `.applied`
(success) or `.failed` / `.rejected` (failure) and fails closed on timeout.
Callers must treat a timeout as "unknown, needs reconciliation", never as
success: the watcher may still apply the config after we stop waiting.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

STAGING_DIR = Path(os.environ.get("SWARM_NGINX_STAGING", "/var/lib/swarm/nginx-staging"))
# File name prefix; must match deploy/nginx-apply/swarm-nginx-apply.
CONF_PREFIX = "swarm-site-"
# Must stay in sync with the allowlist in /usr/local/sbin/swarm-nginx-apply.
_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
DEFAULT_TIMEOUT = 60.0


class StagingError(RuntimeError):
    """Staged change did not reach a confirmed applied state."""


class StagingTimeout(StagingError):
    """Outcome unknown: the watcher may still act. Requires reconciliation."""


def available() -> bool:
    return STAGING_DIR.is_dir() and os.access(STAGING_DIR, os.W_OK)


def _validate(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise StagingError(f"invalid site name for nginx staging: {name!r}")
    return name


def _settle(stem: Path, timeout: float, *, poll: float = 0.5) -> None:
    """Wait for the watcher verdict; absence of the staged file is not success."""
    applied, failed, rejected = (Path(str(stem) + suffix)
                                 for suffix in (".applied", ".failed", ".rejected"))
    deadline = time.monotonic() + timeout
    while True:
        if applied.exists():
            applied.unlink(missing_ok=True)
            return
        for bad, reason in ((rejected, "rejected by the root apply guard"),
                            (failed, "nginx validation failed; previous config restored")):
            if bad.exists():
                bad.unlink(missing_ok=True)
                raise StagingError(f"nginx staging {reason}: {stem.name}")
        if time.monotonic() >= deadline:
            raise StagingTimeout(
                f"nginx staging outcome unknown after {timeout:.0f}s: {stem.name}; "
                "the watcher may still apply it, so reconcile before retrying"
            )
        time.sleep(poll)


def apply_config(name: str, content: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Stage swarm-site-<name>.conf and block until the root watcher confirms it."""
    _validate(name)
    if not isinstance(content, str) or not content.strip():
        raise StagingError("refusing to stage empty nginx config")
    staged = STAGING_DIR / f"{CONF_PREFIX}{name}.conf"
    # Write to a temporary name first: the watcher globs *.conf and must never
    # observe a partially written file.
    tmp = STAGING_DIR / f".partial-{CONF_PREFIX}{name}.conf"
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, staged)
    _settle(staged, timeout)


def remove_config(name: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Ask the watcher to delete a config this mechanism previously applied.

    Removing a config that was never applied here is rejected by the guard;
    that surfaces as StagingError rather than silent success.
    """
    _validate(name)
    marker = STAGING_DIR / f"{CONF_PREFIX}{name}.conf.remove"
    marker.write_text("", encoding="utf-8")
    deadline = time.monotonic() + timeout
    rejected, failed = (Path(str(marker) + s) for s in (".rejected", ".failed"))
    while True:
        if not marker.exists() and not rejected.exists() and not failed.exists():
            return  # Guard deletes the marker only after a successful reload.
        for bad, reason in ((rejected, "not owned by this mechanism"),
                            (failed, "nginx validation failed after removal")):
            if bad.exists():
                bad.unlink(missing_ok=True)
                raise StagingError(f"nginx removal {reason}: {CONF_PREFIX}{name}.conf")
        if time.monotonic() >= deadline:
            raise StagingTimeout(
                f"nginx removal outcome unknown after {timeout:.0f}s: {CONF_PREFIX}{name}.conf"
            )
        time.sleep(0.5)
