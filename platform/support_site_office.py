"""Read-only view of a customer's site office for the onboarding page.

What it may read, and nothing else:

* ``workspace/.dsh-template.json``      which workspace template was placed
* ``workspace/.swarm/registry.yaml``    hives, roles, per-role model and cron
* ``workspace/hives/{id}/project.yaml`` display name, SCM type, whether
                                        notification channels are configured,
                                        and report names
* ``dsh-home/profiles/web/cordis.patch.yml`` only the provider/model of the
                                        ``agent-default-model`` entry

Never ``.keys/``, ``.credentials.yaml``, tokens, or any other file. Every path
component is checked for symlinks (a customer controls the workspace and could
point a link at a secret), reads are size capped, and only a small whitelisted
summary leaves this module: no free-form values beyond short display strings.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

import _site_path  # noqa: E402,F401  (site/ on sys.path)

import safe_fs  # noqa: E402

MAX_BYTES = 64 * 1024
MAX_HIVES = 30
MAX_ROLES = 12
MAX_SCHEDULES = 12
MAX_CONTEXT = 2000
STARTER_PROVIDER = 'platform-starter'
_ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,62}$')
_CRON = re.compile(r'^[0-9a-z*/,\- ]{1,64}$')
_SCHEDULE_META = frozenset({'model', 'enabled', 'heartbeat'})


def _short(value, limit=80):
    if not isinstance(value, str):
        return None
    value = ' '.join(value.split())
    return value[:limit] or None


def _read(root: Path, *parts: str):
    """Text of root/parts, or None. Never follows a symlink at any component
    (openat walk, so a directory swapped for a link mid-read is refused)."""
    try:
        data = safe_fs.read_bytes(root, Path(*parts), MAX_BYTES)
    except (OSError, ValueError):
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return None


def _yaml(text):
    if text is None:
        return None
    import yaml

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def _is_dir(root: Path, *parts: str) -> bool:
    try:
        return safe_fs.kind(root, Path(*parts)) == 'dir'
    except (OSError, ValueError):
        return False


def _roles(agents):
    """Classic ``{role: {model, <report>: cron}}`` or the older ``[role, ...]``."""
    roles = []
    if isinstance(agents, list):
        agents = {name: {} for name in agents if isinstance(name, str)}
    if not isinstance(agents, dict):
        return roles
    for name, body in list(agents.items())[:MAX_ROLES]:
        if not isinstance(name, str) or not _ID.match(name):
            continue
        body = body if isinstance(body, dict) else {}
        schedules = []
        for key, value in body.items():
            if key in _SCHEDULE_META or not isinstance(key, str) or not _ID.match(key):
                continue
            if isinstance(value, str) and _CRON.match(value.strip()):
                schedules.append({'name': key, 'cron': value.strip()})
            if len(schedules) >= MAX_SCHEDULES:
                break
        roles.append({'id': name,
                      'model': _short(body.get('model'), 60),
                      'enabled': body.get('enabled', True) is not False,
                      'schedules': schedules})
    return roles


def _project(workspace: Path, hive_id: str):
    data = _yaml(_read(workspace, 'hives', hive_id, 'project.yaml'))
    if not isinstance(data, dict):
        return {}
    project = data.get('project') if isinstance(data.get('project'), dict) else {}
    scm = data.get('scm')
    scm_type = scm.get('type') if isinstance(scm, dict) else scm
    discord = data.get('discord')
    line = data.get('line')
    notify = data.get('notify')
    channels = []
    if isinstance(discord, dict) and (discord.get('server_id') or discord.get('channels')):
        channels.append('discord')
    if isinstance(notify, dict) and notify.get('channel_id') and 'discord' not in channels:
        channels.append('discord')
    if isinstance(line, dict) and line:
        channels.append('line')
    reports = {}
    raw = data.get('reports')
    for key, body in (raw.items() if isinstance(raw, dict) else []):
        if not (isinstance(key, str) and _ID.match(key)) or len(reports) >= MAX_SCHEDULES * 2:
            continue
        body = body if isinstance(body, dict) else {}
        skill = body.get('skill')
        reports[key] = {'skill': skill if isinstance(skill, str) and _ID.match(skill) else None,
                        'context': body.get('context')[:MAX_CONTEXT]
                        if isinstance(body.get('context'), str) else ''}
    return {
        'name': _short(project.get('name') or data.get('name')),
        'description': _short(project.get('description') or data.get('description'), 140),
        'scm': scm_type if scm_type in ('github', 'gitlab') else 'none',
        'channels': channels,
        'reports': reports,
    }


def _schedules(roles, reports, hive_enabled):
    """Pair registry cron with project.yaml reports exactly like the classic
    scheduler does: a report runs only when both sides exist."""
    items = []
    for role in roles:
        for job in role['schedules']:
            report = reports.get(job['name'])
            state = ('disabled' if not (hive_enabled and role['enabled']) else 'ready') \
                if report else 'missing_definition'
            items.append({'role': role['id'], 'name': job['name'], 'cron': job['cron'],
                          'skill': report['skill'] if report else None,
                          'context': report['context'] if report else '', 'state': state})
    timed = {item['name'] for item in items}
    for name, report in reports.items():
        if name not in timed:
            items.append({'role': None, 'name': name, 'cron': None, 'skill': report['skill'],
                          'context': report['context'], 'state': 'missing_time'})
    return items


def _skills(workspace: Path):
    names = []
    if not _is_dir(workspace, '.dsh', 'skills'):
        return names
    try:
        entries = sorted(safe_fs.list_dir(workspace, Path('.dsh', 'skills')))
    except OSError:
        return names
    for entry in entries[:60]:
        if _ID.match(entry) and _read(workspace, '.dsh', 'skills', entry, 'SKILL.md') is not None:
            names.append(entry)
    return names


def default_model(dsh_home: Path):
    """provider/model of the customer's default model entry, nothing else."""
    text = _read(dsh_home, 'profiles', 'web', 'cordis.patch.yml')
    if text is None:
        return None
    match = re.search(r"^- id: ['\"]?agent-default-model['\"]?\s*$", text, re.M)
    if not match:
        return None
    block = []
    for line in text[match.end():].splitlines()[1:]:
        if line.startswith('- ') or (line and not line.startswith(' ')):
            break
        block.append(line)
    found = {}
    for key in ('provider', 'model'):
        hit = re.search(rf"^\s+{key}:\s*['\"]?([A-Za-z0-9._:/@-]{{1,80}})['\"]?\s*$",
                        '\n'.join(block), re.M)
        found[key] = hit.group(1) if hit else None
    if not found['provider'] and not found['model']:
        return None
    return {**found, 'starter': found['provider'] == STARTER_PROVIDER}


def read_office(site_root: Path) -> dict:
    """Summary of one site's office. Missing pieces are reported, not guessed."""
    site_root = Path(site_root)
    workspace = site_root / 'workspace'
    record = _read(workspace, '.dsh-template.json')
    template = None
    if record is not None:
        try:
            value = json.loads(record).get('template')
            template = value if isinstance(value, str) and _ID.match(value) else None
        except (ValueError, AttributeError):
            template = None
    registry_text = _read(workspace, '.swarm', 'registry.yaml')
    registry = _yaml(registry_text)
    hives, registry_state = [], 'missing'
    if registry_text is not None:
        registry_state = 'ok'
        items = registry if isinstance(registry, list) else (
            registry.get('hives') if isinstance(registry, dict) else None)
        if registry is not None and not isinstance(items, (list, type(None))):
            registry_state = 'unreadable'
        elif registry is None and registry_text.strip() and not all(
                line.strip().startswith('#') or not line.strip()
                for line in registry_text.splitlines()):
            registry_state = 'unreadable'
        for item in (items or [])[:MAX_HIVES]:
            if not isinstance(item, dict):
                continue
            hive_id = item.get('id')
            if not isinstance(hive_id, str) or not _ID.match(hive_id):
                continue
            project = _project(workspace, hive_id)
            roles = _roles(item.get('agents'))
            enabled = item.get('enabled') is True
            hives.append({
                'id': hive_id,
                'name': project.get('name') or _short(item.get('name')) or hive_id,
                'description': project.get('description'),
                'enabled': enabled,
                'exists': _is_dir(workspace, 'hives', hive_id),
                'roles': roles,
                'scm': project.get('scm', 'none'),
                'channels': project.get('channels', []),
                'schedules': _schedules(roles, project.get('reports', {}), enabled),
            })
    return {'template': template, 'registry': registry_state, 'hives': hives,
            'skills': _skills(workspace),
            'revision': _revision(registry_text, hives, workspace),
            'default_model': default_model(site_root / 'dsh-home')}


def _revision(registry_text, hives, workspace):
    """Fingerprint of every file the schedule editor may write."""
    digest = hashlib.sha256((registry_text or '').encode('utf-8'))
    for hive in hives:
        digest.update(b'\0' + hive['id'].encode() + b'\0')
        digest.update((_read(workspace, 'hives', hive['id'], 'project.yaml') or '').encode('utf-8'))
    return digest.hexdigest()[:24]


class SiteOffice:
    """Maps a tenant's site host to its office summary under ``sites_root``."""

    def __init__(self, sites_root, domain):
        self.sites_root = Path(sites_root)
        self.domain = domain

    def for_host(self, site_host):
        suffix = '.' + self.domain
        if not isinstance(site_host, str) or not site_host.endswith(suffix):
            return None
        from names import validate_name

        try:
            name = validate_name(site_host[:-len(suffix)])
        except ValueError:
            return None
        if not _is_dir(self.sites_root, name):
            return None
        return read_office(self.sites_root / name)

    def editor_for_host(self, site_host):
        suffix = '.' + self.domain
        if not isinstance(site_host, str) or not site_host.endswith(suffix):
            raise OfficeError('站台尚未就緒')
        from names import validate_name

        try:
            name = validate_name(site_host[:-len(suffix)])
        except ValueError:
            raise OfficeError('站台尚未就緒') from None
        if not _is_dir(self.sites_root, name) or not _is_dir(self.sites_root, name, 'workspace'):
            raise OfficeError('站台尚未就緒')
        return OfficeEditor(self.sites_root / name)


# -- schedule editor ---------------------------------------------------------
#
# Writes the classic swarm layout and nothing else: the cron goes on the role in
# .swarm/registry.yaml, the task definition goes in hives/{id}/project.yaml
# under reports:. Only those two files are ever written, only for a hive that
# is already registered, and only when the caller saw the current revision.
# Every write keeps a backup copy under .swarm/.backups/.

_DOW = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
_FIELD_LIMITS = ((0, 59), (0, 23), (1, 31), (1, 12), None)
_SECRET = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,})|(gh[pousr]_[A-Za-z0-9]{20,})|(xox[abpr]-[A-Za-z0-9\-]{10,})"
    r"|(AKIA[0-9A-Z]{16})|(-----BEGIN [A-Z ]*PRIVATE KEY-----)|(AIza[0-9A-Za-z_\-]{30,})"
    r"|(glpat-[A-Za-z0-9_\-]{16,})")
MAX_BACKUPS = 10


class OfficeError(ValueError):
    """Refused edit; the message is safe to show the customer."""


class OfficeConflict(RuntimeError):
    """The files changed since the customer loaded them."""


def _cron_value(token, low, high):
    if not token.isdigit() or not low <= int(token) <= high:
        raise OfficeError('時間格式不正確')


def validate_cron(expr):
    """Five fields. Day-of-week must use names (mon..sun): numbers mean
    different days to cron and to the classic APScheduler-based scheduler."""
    if not isinstance(expr, str):
        raise OfficeError('時間格式不正確')
    parts = expr.split()
    if len(parts) != 5 or len(expr) > 64:
        raise OfficeError('時間格式不正確')
    for index, part in enumerate(parts):
        for piece in part.split(','):
            base, _, step = piece.partition('/')
            if step:
                _cron_value(step, 1, 59)
            if base == '*':
                continue
            if index == 4:
                names = base.split('-')
                if len(names) > 2 or any(n not in _DOW for n in names):
                    raise OfficeError('星期請用 mon…sun')
                continue
            low, high = _FIELD_LIMITS[index]
            ends = base.split('-')
            if len(ends) > 2:
                raise OfficeError('時間格式不正確')
            for end in ends:
                _cron_value(end, low, high)
    return ' '.join(parts)


def _write_file(root: Path, parts, text):
    """Atomic replace of a regular file; refuses any symlink on the way."""
    try:
        safe_fs.write_atomic(root, Path(*parts), text)
    except safe_fs.UnsafePath:
        raise OfficeError('站台檔案結構異常，未寫入') from None


def _backup(workspace: Path, label, text, stamp):
    if text is None:
        return
    try:
        safe_fs.make_dirs(workspace, Path('.swarm', '.backups'))
    except (safe_fs.UnsafePath, NotADirectoryError):
        raise OfficeError('備份資料夾異常，未寫入') from None
    name = f'{stamp}-{label}'
    _write_file(workspace, ('.swarm', '.backups', name), text)
    kept = sorted(p for p in safe_fs.list_dir(workspace, Path('.swarm', '.backups'))
                  if p.endswith('-' + label))
    for old in kept[:-MAX_BACKUPS]:
        try:
            safe_fs.unlink(workspace, Path('.swarm', '.backups', old))
        except OSError:
            pass


def _header(text):
    """Leading comment block, kept across rewrites (the format guide lives there)."""
    lines = []
    for line in (text or '').splitlines():
        if line.startswith('#') or not line.strip():
            lines.append(line)
        else:
            break
    while lines and not lines[-1].strip():
        lines.pop()
    return '\n'.join(lines) + '\n' if lines else ''


def _dump(data):
    import yaml

    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=1000)


class OfficeEditor:
    """Edits one site's schedules. Stateless apart from a process-wide lock."""

    import threading as _threading
    _lock = _threading.Lock()

    def __init__(self, site_root: Path, clock=None):
        import time

        self.site_root = Path(site_root)
        self.workspace = self.site_root / 'workspace'
        self.clock = clock or time.time

    def _load(self, revision, hive_id):
        office = read_office(self.site_root)
        if office['registry'] != 'ok':
            raise OfficeError('讀不到辦公室的公司清單，請先在站台裡請 AI 用 hive-list 檢查')
        if not isinstance(revision, str) or revision != office['revision']:
            raise OfficeConflict()
        if not isinstance(hive_id, str) or not any(h['id'] == hive_id for h in office['hives']):
            raise OfficeError('找不到這間公司')
        registry_text = _read(self.workspace, '.swarm', 'registry.yaml')
        registry = _yaml(registry_text)
        items = registry if isinstance(registry, list) else registry.get('hives')
        entry = next(i for i in items if isinstance(i, dict) and i.get('id') == hive_id)
        agents = entry.get('agents')
        if isinstance(agents, list):
            entry['agents'] = agents = {name: {'model': ''} for name in agents if isinstance(name, str)}
        elif not isinstance(agents, dict):
            entry['agents'] = agents = {}
        project_text = _read(self.workspace, 'hives', hive_id, 'project.yaml')
        if project_text is None and _is_dir(self.workspace, 'hives', hive_id) and \
                safe_fs.kind(self.workspace, Path('hives', hive_id, 'project.yaml')) is not None:
            raise OfficeError('project.yaml 無法讀取，未寫入')
        project = _yaml(project_text) if project_text is not None else {}
        if project is None and project_text and project_text.strip():
            raise OfficeError('project.yaml 格式有誤，未寫入')
        if not isinstance(project, dict):
            project = {}
        return registry_text, registry, entry, project_text, project

    def _save(self, hive_id, registry_text, registry, project_text, project, touch_project):
        if not _is_dir(self.workspace, 'hives', hive_id):
            raise OfficeError('站台裡找不到這間公司的資料夾')
        stamp = time_stamp(self.clock())
        _backup(self.workspace, 'registry.yaml', registry_text, stamp)
        if touch_project:
            _backup(self.workspace, f'{hive_id}.project.yaml', project_text, stamp)
            _write_file(self.workspace, ('hives', hive_id, 'project.yaml'),
                        _header(project_text) + _dump(project))
        _write_file(self.workspace, ('.swarm', 'registry.yaml'),
                    _header(registry_text) + _dump(registry))
        return read_office(self.site_root)

    def save_schedule(self, revision, hive_id, *, original, name, role, cron, skill, context):
        if not isinstance(name, str) or not _ID.match(name) or name in _SCHEDULE_META:
            raise OfficeError('任務代號請用英文小寫、數字、底線或連字號')
        if original and (not isinstance(original, str) or not _ID.match(original)):
            raise OfficeError('原任務代號不正確')
        cron = validate_cron(cron)
        if skill and (not isinstance(skill, str) or not _ID.match(skill)):
            raise OfficeError('skill 名稱不正確')
        if not isinstance(context, str) or len(context) > MAX_CONTEXT:
            raise OfficeError(f'任務說明最多 {MAX_CONTEXT} 字')
        context = context.strip()
        if not context and not skill:
            raise OfficeError('請選一個 skill 或寫下要做什麼')
        if _SECRET.search(context):
            raise OfficeError('任務說明看起來含有金鑰，請移除；金鑰只放在 hives/{id}/.keys/')
        with self._lock:
            registry_text, registry, entry, project_text, project = self._load(revision, hive_id)
            agents = entry['agents']
            if role not in agents:
                raise OfficeError('這間公司沒有這個角色')
            reports = project.get('reports')
            if not isinstance(reports, dict):
                reports = {}
            old = original or None
            if old is None and (name in reports or any(
                    isinstance(b, dict) and name in b for b in agents.values())):
                raise OfficeError('已經有同名的排程')
            if old and old != name and (name in reports or any(
                    isinstance(b, dict) and name in b for b in agents.values())):
                raise OfficeError('已經有同名的排程')
            definition = dict(reports.get(old or name) or {}) if isinstance(
                reports.get(old or name), (dict, type(None))) else {}
            for key in {old, name} - {None}:
                reports.pop(key, None)
                for body in agents.values():
                    if isinstance(body, dict):
                        body.pop(key, None)
            if skill:
                definition['skill'] = skill
            else:
                definition.pop('skill', None)
            definition['context'] = context or f'這是「{name}」排程觸發。請執行 {skill} skill。'
            definition.setdefault('context_type', 'project_management')
            reports[name] = definition
            project['reports'] = reports
            body = agents.get(role)
            if not isinstance(body, dict):
                agents[role] = body = {}
            body[name] = cron
            return self._save(hive_id, registry_text, registry, project_text, project, True)

    def delete_schedule(self, revision, hive_id, name):
        if not isinstance(name, str) or not _ID.match(name) or name in _SCHEDULE_META:
            raise OfficeError('任務代號不正確')
        with self._lock:
            registry_text, registry, entry, project_text, project = self._load(revision, hive_id)
            found = False
            for body in entry['agents'].values():
                if isinstance(body, dict) and name in body:
                    body.pop(name)
                    found = True
            reports = project.get('reports')
            if isinstance(reports, dict) and name in reports:
                reports.pop(name)
                found = True
            if not found:
                raise OfficeError('找不到這個排程')
            return self._save(hive_id, registry_text, registry, project_text, project, True)

    def set_hive_enabled(self, revision, hive_id, enabled):
        if type(enabled) is not bool:
            raise OfficeError('開關值不正確')
        with self._lock:
            registry_text, registry, entry, project_text, project = self._load(revision, hive_id)
            entry['enabled'] = enabled
            return self._save(hive_id, registry_text, registry, project_text, project, False)


def time_stamp(now):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
