#!/usr/bin/env python3
"""Block deployment-specific values from entering this public repository.

Two kinds of rule:

* Generic patterns (absolute home paths, private keys, ...) are written out in
  this file because they reveal nothing.
* Deployment-specific words (our domains, people, IDs) must not appear in the
  repository even as a rule. They are stored as salted SHA-256 hashes in
  scripts/denylist.sha256: every word-like token in every tracked file is
  hashed and compared. Matching is case-insensitive.

Usage:
  scripts/check-denylist.py                 scan tracked files (git ls-files)
  scripts/check-denylist.py FILE...         scan the given files
  scripts/check-denylist.py --hash WORD...  print the hash line for new words

Exit status 1 when anything matches. Matches print the file and line, never
the hashed word.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HASH_FILE = ROOT / "scripts" / "denylist.sha256"
SALT = "towningtek-swarm-denylist-v1:"

# Word-like tokens: domains, e-mail local parts, numeric IDs, names.
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]|[A-Za-z0-9]")

GENERIC = [
    ("absolute home path", re.compile(r"/home/[a-z_][a-z0-9_-]*/")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Discord webhook", re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[\w-]{20,}")),
    ("17-20 digit ID", re.compile(r"(?<![\w.])\d{17,20}(?![\w.])")),
]

# Exact public strings (e.g. URLs of already-public repos) that may contain a
# denylisted word. One per line in scripts/denylist-allow.txt. Keep it short.
ALLOW_FILE = ROOT / "scripts" / "denylist-allow.txt"


def load_allow() -> list[str]:
    if not ALLOW_FILE.exists():
        return []
    return [l.strip() for l in ALLOW_FILE.read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#")]

# Files that are allowed to mention generic patterns (this checker, its tests).
GENERIC_ALLOW = {"scripts/check-denylist.py", "scripts/test_check_denylist.py"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".mp4", ".woff", ".woff2"}


def digest(word: str) -> str:
    return hashlib.sha256((SALT + word.lower()).encode()).hexdigest()


def load_hashes() -> set[str]:
    if not HASH_FILE.exists():
        return set()
    out = set()
    for line in HASH_FILE.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def candidates(token: str):
    """The token, plus every dot/dash/underscore-separated piece and suffix."""
    yield token
    parts = re.split(r"[._-]", token)
    for part in parts:
        if part:
            yield part
    # domain suffixes: a.b.example.com -> b.example.com, example.com
    dotted = token.split(".")
    for i in range(1, len(dotted) - 1):
        yield ".".join(dotted[i:])


def scan_text(rel: str, text: str, hashes: set[str], allow: list[str] | None = None) -> list[str]:
    found = []
    if rel == "scripts/denylist-allow.txt":
        return found
    for lineno, line in enumerate(text.splitlines(), 1):
        if rel not in GENERIC_ALLOW:
            for label, pattern in GENERIC:
                if pattern.search(line):
                    found.append(f"{rel}:{lineno}: {label}")
        if hashes:
            checked = line
            for literal in allow or ():
                checked = checked.replace(literal, " ")
            for token in TOKEN.findall(checked):
                if any(digest(c) in hashes for c in candidates(token)):
                    found.append(f"{rel}:{lineno}: deployment-specific word (denylist)")
                    break
    return found


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    return [p for p in out.stdout.decode().split("\0") if p]


def main(argv: list[str]) -> int:
    if argv[:1] == ["--hash"]:
        for word in argv[1:]:
            print(digest(word))
        return 0
    files = argv or tracked_files()
    hashes = load_hashes()
    allow = load_allow()
    problems = []
    for rel in files:
        path = ROOT / rel if not Path(rel).is_absolute() else Path(rel)
        if path.suffix.lower() in SKIP_SUFFIX or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        problems += scan_text(str(Path(rel)), text, hashes, allow)
    for p in problems:
        print(p)
    if problems:
        print(f"denylist: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"denylist: {len(files)} file(s) clean, {len(hashes)} hashed word(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
