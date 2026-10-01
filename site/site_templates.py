"""Workspace templates and the platform guide for customer DSH sites.

Two layers, deliberately separate:

* Platform guide -> ``$DSH_HOME/AGENTS.md``. DSH loads it for every workspace.
  It carries what every site needs (reply language, starter model and how to
  connect one's own model). The platform owns it and may rewrite it.
* Workspace template -> the customer's workspace, chosen per tenant (or none).
  Placed once; files the customer has since changed or removed are never
  touched again. The applied id/version is recorded in the workspace.

Nothing secret is ever placed: every template file is scanned before any byte is
written, and a template holding key-like paths or token-like content is refused
as a whole.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

import yaml

import safe_fs

TEMPLATES_ROOT = Path(__file__).resolve().parent / "templates"
# Office templates live at the repo root (office-template/<id>/); override with
# SWARM_OFFICE_TEMPLATES when the site code is installed elsewhere.
WORKSPACE_TEMPLATES = Path(os.environ.get(
    "SWARM_OFFICE_TEMPLATES", Path(__file__).resolve().parent.parent / "office-template"))
PLATFORM_GUIDE = TEMPLATES_ROOT / "platform-guide" / "AGENTS.md"
RECORD = ".dsh-template.json"
NONE = "none"

MAX_FILE = 256 * 1024
MAX_TOTAL = 2 * 1024 * 1024

# Path parts that must never ship in a template.
_SECRET_PARTS = {".keys", "keys", ".credentials.yaml", ".git", "node_modules"}
_SECRET_NAME = re.compile(r"(^\.env(\..*)?$)|(\.(pem|key|p12|pfx)$)|(^id_(rsa|ed25519|ecdsa))", re.I)
# Content that looks like a live credential.
_SECRET_TEXT = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,})|(sms_[A-Za-z0-9_\-]{20,})|(gh[pousr]_[A-Za-z0-9]{20,})"
    r"|(xox[abpr]-[A-Za-z0-9\-]{10,})|(AKIA[0-9A-Z]{16})|(-----BEGIN [A-Z ]*PRIVATE KEY-----)"
    r"|(AIza[0-9A-Za-z_\-]{30,})")
_ID = re.compile(r"[a-z][a-z0-9\-]{0,31}")
_VAR = re.compile(r"\{\{([a-z_]+)\}\}")
_SECTION = re.compile(r"\{\{([#^])([a-z_]+)\}\}\n?(.*?)\{\{/\2\}\}\n?", re.S)


class TemplateError(RuntimeError):
    pass


def render(text: str, values: dict) -> str:
    """Tiny, closed renderer: {{var}}, {{#flag}}..{{/flag}}, {{^flag}}..{{/flag}}.

    Unknown variables are an error, so a typo cannot ship as literal braces.
    """
    def section(match):
        kind, key, body = match.groups()
        if key not in values:
            raise TemplateError(f"unknown template flag {key!r}")
        return body if bool(values[key]) == (kind == "#") else ""

    text = _SECTION.sub(section, text)

    def var(match):
        key = match.group(1)
        if key not in values or isinstance(values[key], bool):
            raise TemplateError(f"unknown template variable {key!r}")
        return str(values[key])

    text = _VAR.sub(var, text)
    if "{{" in text:
        raise TemplateError("unbalanced template markup")
    return text


def guide_values(*, language: str = "繁體中文", starter_model: str | None = None,
                 starter_display: str = "平台起步模型") -> dict:
    for value in (language, starter_display, starter_model or ""):
        if not isinstance(value, str) or len(value) > 64 or re.search(r"[\n\r{}]", value):
            raise TemplateError("invalid guide value")
    return {"language": language, "starter": bool(starter_model),
            "starter_model": starter_model or "", "starter_display": starter_display}


def list_templates(root: Path = WORKSPACE_TEMPLATES) -> list[dict]:
    """Metadata of every available workspace template (plus the 'none' choice)."""
    found = [{"id": NONE, "name": "不放樣板", "version": 0,
              "description": "只有平台指引，工作區保持空白。"}]
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and not entry.is_symlink() and (entry / "template.yaml").is_file():
                meta = _meta(entry)
                found.append({k: meta[k] for k in ("id", "name", "version", "description")})
    return found


def _meta(directory: Path) -> dict:
    meta = yaml.safe_load((directory / "template.yaml").read_text(encoding="utf-8")) or {}
    if not isinstance(meta, dict) or meta.get("id") != directory.name or not _ID.fullmatch(directory.name):
        raise TemplateError(f"template {directory.name!r}: id must match its directory")
    if type(meta.get("version")) is not int or meta["version"] < 1:
        raise TemplateError(f"template {directory.name!r}: version must be a positive integer")
    for key in ("name", "description"):
        if not isinstance(meta.get(key), str) or not meta[key]:
            raise TemplateError(f"template {directory.name!r}: {key} required")
    dirs = meta.get("dirs", [])
    if not isinstance(dirs, list) or any(not isinstance(d, str) or not _safe_rel(d) for d in dirs):
        raise TemplateError(f"template {directory.name!r}: invalid dirs")
    meta.setdefault("git_init", False)
    if type(meta["git_init"]) is not bool:
        raise TemplateError(f"template {directory.name!r}: git_init must be boolean")
    return meta


def _safe_rel(rel: str) -> bool:
    path = Path(rel)
    return (not path.is_absolute() and ".." not in path.parts and rel == path.as_posix()
            and not any(part in _SECRET_PARTS or _SECRET_NAME.search(part) for part in path.parts))


def load_template(template_id: str, values: dict, root: Path = WORKSPACE_TEMPLATES) -> tuple[dict, dict]:
    """Return (meta, {relative path: rendered text}); refuse anything secret-like."""
    if not isinstance(template_id, str) or not _ID.fullmatch(template_id) or template_id == NONE:
        raise TemplateError("invalid template id")
    directory = root / template_id
    if not directory.is_dir() or directory.is_symlink():
        raise TemplateError(f"unknown template {template_id!r}")
    meta = _meta(directory)
    files, total = {}, 0
    for path in sorted(directory.rglob("*")):
        rel = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise TemplateError(f"template {template_id!r}: symlink {rel} refused")
        if path.is_dir():
            if not _safe_rel(rel):
                raise TemplateError(f"template {template_id!r}: path {rel} refused")
            continue
        if rel == "template.yaml":
            continue
        if not _safe_rel(rel) or rel == RECORD:
            raise TemplateError(f"template {template_id!r}: path {rel} refused")
        raw = path.read_bytes()
        total += len(raw)
        if len(raw) > MAX_FILE or total > MAX_TOTAL:
            raise TemplateError(f"template {template_id!r}: too large")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise TemplateError(f"template {template_id!r}: {rel} is not UTF-8 text") from None
        text = render(text, values)
        if _SECRET_TEXT.search(text):
            raise TemplateError(f"template {template_id!r}: {rel} looks like it holds a credential")
        files[rel] = text
    return meta, files


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _unchanged(workspace: Path, rel: str, digest: str) -> bool:
    try:
        return _digest(safe_fs.read_text(workspace, rel)) == digest
    except (OSError, UnicodeDecodeError):
        return False


def apply_template(workspace: Path, template_id: str, values: dict,
                   root: Path = WORKSPACE_TEMPLATES) -> dict:
    """Place a template into a workspace once. Returns a summary.

    * First time: every file is created (never overwriting a pre-existing one).
    * Same template again: files never placed before are created if absent. A
      file placed before is upgraded only when it is still exactly what was
      placed (digest match); a customer's edit or deletion always sticks.
    * A different template than the recorded one is refused.
    """
    workspace = Path(workspace)
    if workspace.is_symlink() or not workspace.is_dir():
        raise TemplateError("workspace must be a real directory")
    # The workspace is customer-writable: every access below walks it with
    # O_NOFOLLOW (safe_fs), so a planted symlink is refused, never followed.
    record = None
    record_kind = safe_fs.kind(workspace, RECORD)
    if record_kind is not None:
        if record_kind != "file":
            raise TemplateError("template record must be a regular file")
        record = json.loads(safe_fs.read_text(workspace, RECORD))
    if template_id == NONE:
        return {"template": NONE, "created": [], "kept": [], "record": record}
    meta, files = load_template(template_id, values, root)
    if record is not None and record.get("template") != template_id:
        raise TemplateError(
            f"workspace already has template {record.get('template')!r}; refusing {template_id!r}")
    placed = dict(record.get("files", {})) if record else {}
    created, kept, upgraded = [], [], []
    try:
        for rel, text in files.items():
            if rel in placed:
                # Upgrade only a file that is byte-for-byte what we placed: the
                # customer never touched it. Edited or deleted files stay theirs.
                new_digest = _digest(text)
                if placed[rel] != new_digest and _unchanged(workspace, rel, placed[rel]):
                    safe_fs.write_atomic(workspace, rel, text)
                    placed[rel] = new_digest
                    upgraded.append(rel)
                else:
                    kept.append(rel)
                continue
            try:
                safe_fs.create_new(workspace, rel, text)
            except FileExistsError:
                kept.append(rel)
                continue
            placed[rel] = _digest(text)
            created.append(rel)
        for rel in meta.get("dirs", []):
            safe_fs.make_dirs(workspace, rel)
    except (safe_fs.UnsafePath, NotADirectoryError) as exc:
        raise TemplateError(f"workspace path crosses a symlink or file: {exc}") from None
    if meta["git_init"] and safe_fs.kind(workspace, ".git") is None:
        # A project-root marker lets DSH load AGENTS.md and .dsh/skills from any
        # subfolder the customer opens. No commit is made and no identity is set.
        subprocess.run(["git", "init", "-q", str(workspace)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    new_record = {"template": template_id, "version": meta["version"],
                  "first_version": (record or {}).get("first_version", meta["version"]),
                  "files": placed}
    safe_fs.write_atomic(workspace, RECORD,
                         json.dumps(new_record, ensure_ascii=False, indent=2, sort_keys=True))
    return {"template": template_id, "version": meta["version"], "created": created,
            "kept": kept, "upgraded": upgraded}


def write_platform_guide(dsh_home: Path, values: dict, source: Path = PLATFORM_GUIDE) -> None:
    """(Re)write ``$DSH_HOME/AGENTS.md``. Platform-owned: always replaced."""
    dsh_home = Path(dsh_home)
    if dsh_home.is_symlink() or not dsh_home.is_dir():
        raise TemplateError("DSH home must be a real directory")
    text = render(source.read_text(encoding="utf-8"), values)
    if _SECRET_TEXT.search(text):
        raise TemplateError("platform guide looks like it holds a credential")
    try:
        safe_fs.write_atomic(dsh_home, "AGENTS.md", text, 0o644)
    except safe_fs.UnsafePath:
        raise TemplateError("platform guide target must not be a symlink") from None
