"""SCM tokens for customer sites: per-site relay keys, repo scope, mint audit.

A site must never hold the GitHub App private key. That key can mint a token
for *every* installation the app has, so one compromised site would expose every
customer. Instead the platform keeps the key and a site holds only a relay key;
``scm_token_relay.py`` exchanges that key for a short-lived, repo-scoped
installation token, exactly the way ``site_model_relay.py`` keeps the platform
model key out of sites.

This module is the relay's bookkeeping:

* ``support_site_scm_tokens`` — one relay key per tenant. Only the SHA-256
  digest is stored; issuing again rotates it.
* ``support_site_scm_scopes`` — the repositories that tenant may receive a token
  for. An empty scope is *not* a wildcard: the relay refuses instead of guessing.
* ``support_site_scm_mints`` — one audit row per exchange, granted or refused.
  Issuing credentials is high-authority; it has to be answerable afterwards.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets

from support_core import InvalidInput, SupportError

TOKEN_PREFIX = 'scs_'
_TOKEN_RE = re.compile(r'scs_[A-Za-z0-9_-]{43}')
# GitHub owners: letters, digits and single inner dashes (max 39). Repo names:
# letters, digits, dot, dash, underscore, but never "." or "..". Both end up in
# GitHub API URL paths, so nothing that could walk a path is accepted.
_OWNER = r'[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}'
_NAME = r'(?!\.{1,2}(?:$|/))[A-Za-z0-9_.-]{1,100}'
_REPO_RE = re.compile(_OWNER + '/' + _NAME)
# ``owner/*``: every repository the app installation on that owner can reach.
# The relay then mints without a ``repositories`` list, so GitHub itself bounds
# the token to the installation's repository selection.
_ORG_RE = re.compile(_OWNER + r'/\*')
MAX_REPOS = 100
_APP_RE = re.compile(r'[a-z0-9][a-z0-9-]{0,62}')  # local name of a configured GitHub App

# Permission ceiling per tenant. Every token minted for a site is narrowed to the
# tenant's policy via the ``permissions`` body parameter, even if the site's App
# was registered (or later edited) with more.
# Default is read-only: a site gets write access only when explicitly granted.
READ_ONLY = {'contents': 'read', 'issues': 'read', 'pull_requests': 'read', 'metadata': 'read'}
# Opt-in only (never in the default): organization Projects V2, for the site's board
# (plugins/swarm-board reads a project and moves cards between Status columns).
_PERMISSION_KEYS = frozenset(READ_ONLY) | {'organization_projects'}
_LEVELS = frozenset({'read', 'write'})


def normalize_permissions(permissions):
    if permissions is None:
        return dict(READ_ONLY)
    if not isinstance(permissions, dict) or not permissions:
        raise InvalidInput('permissions must be a non-empty object')
    out = {}
    for key, level in permissions.items():
        if key not in _PERMISSION_KEYS:
            raise InvalidInput(f'permission {key!r} is not allowed for sites')
        if level not in _LEVELS:
            raise InvalidInput(f'permission level {level!r} must be read or write')
        out[key] = level
    out['metadata'] = 'read'  # GitHub requires it; never more.
    return out


class SiteScmDenied(SupportError):
    """Exchange refused. ``status`` is the HTTP status the relay returns."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def _digest(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def normalize_repos(repos):
    """Validate a repo list and return it deduplicated, order preserved.

    Comparison is case-insensitive because GitHub treats owner/repo that way,
    but the given spelling is kept: that is what the operator will recognise.
    """
    if not isinstance(repos, (list, tuple)) or not repos:
        raise InvalidInput('repos must be a non-empty list')
    if len(repos) > MAX_REPOS:
        raise InvalidInput(f'repos must hold at most {MAX_REPOS} entries')
    seen, out = set(), []
    for repo in repos:
        if not isinstance(repo, str) or not (_REPO_RE.fullmatch(repo) or _ORG_RE.fullmatch(repo)):
            raise InvalidInput(f'invalid repository {repo!r}; expected owner/name or owner/*')
        key = repo.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(repo)
    return out


class SiteScmAccess:
    def __init__(self, core):
        self.core = core
        with core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('CREATE TABLE IF NOT EXISTS support_site_scm_tokens ('
                         'tenant_id TEXT PRIMARY KEY REFERENCES tenants(id), '
                         'digest TEXT NOT NULL UNIQUE, created_at REAL NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS support_site_scm_scopes ('
                         'tenant_id TEXT PRIMARY KEY REFERENCES tenants(id), '
                         'repos TEXT NOT NULL, permissions TEXT NOT NULL, '
                         'updated_at REAL NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS support_site_scm_mints ('
                         'tenant_id TEXT NOT NULL REFERENCES tenants(id), '
                         'request_id TEXT NOT NULL, repos TEXT NOT NULL, '
                         'installation_id INTEGER, expires_at REAL, '
                         "outcome TEXT NOT NULL CHECK(outcome IN ('granted','refused','error')), "
                         'detail TEXT, created_at REAL NOT NULL, '
                         'PRIMARY KEY (tenant_id, request_id))')
            conn.execute('CREATE INDEX IF NOT EXISTS support_site_scm_mints_tenant '
                         'ON support_site_scm_mints(tenant_id, created_at)')
            # Which GitHub App speaks for this site. Every site has its own App
            # (its own name on GitHub); a site without one gets no token at all,
            # never a fallback to some other site's or the operator's identity.
            columns = {row[1] for row in conn.execute('PRAGMA table_info(support_site_scm_scopes)')}
            if 'app' not in columns:
                conn.execute('ALTER TABLE support_site_scm_scopes ADD COLUMN app TEXT')

    # -- relay keys -------------------------------------------------------------
    # Trusted in-process callers only (provisioner, operator CLI): no HTTP route.

    def issue(self, tenant_id):
        """Create (or rotate) the tenant's relay key and return it once."""
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM tenants WHERE id=?', (tenant_id,)).fetchone():
                raise InvalidInput('unknown tenant')
            conn.execute('INSERT INTO support_site_scm_tokens VALUES (?,?,?) ON CONFLICT(tenant_id) '
                         'DO UPDATE SET digest=excluded.digest, created_at=excluded.created_at',
                         (tenant_id, _digest(token), self.core.clock()))
        return token

    def revoke(self, tenant_id):
        with self.core._connect() as conn:
            conn.execute('DELETE FROM support_site_scm_tokens WHERE tenant_id=?', (tenant_id,))

    # -- scope ------------------------------------------------------------------

    def set_repos(self, tenant_id, repos, permissions=None, *, app=None):
        """Replace the tenant's repositories and permission ceiling.

        ``permissions`` defaults to READ_ONLY. ``app`` names the GitHub App
        (a key of the relay's SCM_APPS_FILE) that speaks for this site; when
        omitted the current binding is kept. Returns ``(repos, permissions)``.
        """
        clean = normalize_repos(repos)
        perms = normalize_permissions(permissions)
        if app is not None and (not isinstance(app, str) or not _APP_RE.fullmatch(app)):
            raise InvalidInput('invalid app name')
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM tenants WHERE id=?', (tenant_id,)).fetchone():
                raise InvalidInput('unknown tenant')
            conn.execute('INSERT INTO support_site_scm_scopes (tenant_id, repos, permissions, updated_at, app) '
                         'VALUES (?,?,?,?,?) ON CONFLICT(tenant_id) '
                         'DO UPDATE SET repos=excluded.repos, permissions=excluded.permissions, '
                         'updated_at=excluded.updated_at, app=COALESCE(excluded.app, app)',
                         (tenant_id, json.dumps(clean), json.dumps(perms), self.core.clock(), app))
        return clean, perms

    def bind_app(self, tenant_id, app):
        """Bind (or with ``None`` unbind) the GitHub App that speaks for this site."""
        if app is not None and (not isinstance(app, str) or not _APP_RE.fullmatch(app)):
            raise InvalidInput('invalid app name')
        with self.core._connect() as conn:
            done = conn.execute('UPDATE support_site_scm_scopes SET app=?, updated_at=? WHERE tenant_id=?',
                                (app, self.core.clock(), tenant_id))
            if done.rowcount == 0:
                raise InvalidInput('set the repositories first')

    def app_for(self, tenant_id):
        with self.core._connect() as conn:
            row = conn.execute('SELECT app FROM support_site_scm_scopes WHERE tenant_id=?',
                               (tenant_id,)).fetchone()
        app = row['app'] if row else None
        return app if isinstance(app, str) and _APP_RE.fullmatch(app) else None

    def clear_repos(self, tenant_id):
        with self.core._connect() as conn:
            conn.execute('DELETE FROM support_site_scm_scopes WHERE tenant_id=?', (tenant_id,))

    def scope_for(self, tenant_id):
        """Return ``(repos, permissions)``; ``([], READ_ONLY)`` when unset.

        A malformed stored permission set falls back to READ_ONLY, never wider.
        """
        with self.core._connect() as conn:
            row = conn.execute('SELECT repos, permissions FROM support_site_scm_scopes '
                               'WHERE tenant_id=?', (tenant_id,)).fetchone()
        if row is None:
            return [], dict(READ_ONLY)
        try:
            repos = json.loads(row['repos'])
        except (TypeError, ValueError):
            repos = []
        try:
            perms = normalize_permissions(json.loads(row['permissions']))
        except (TypeError, ValueError, InvalidInput):
            perms = dict(READ_ONLY)
        return (repos if isinstance(repos, list) else []), perms

    def repos_for(self, tenant_id):
        return self.scope_for(tenant_id)[0]

    # -- admission --------------------------------------------------------------

    def authenticate(self, token):
        """Return the tenant id for a valid relay key of an enabled tenant."""
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            raise SiteScmDenied(401, 'invalid_token', 'invalid SCM relay token')
        with self.core._connect() as conn:
            row = conn.execute('SELECT t.id, t.enabled FROM support_site_scm_tokens k '
                               'JOIN tenants t ON t.id=k.tenant_id WHERE k.digest=?',
                               (_digest(token),)).fetchone()
        if row is None:
            raise SiteScmDenied(401, 'invalid_token', 'invalid SCM relay token')
        if not row['enabled']:
            raise SiteScmDenied(403, 'site_disabled', 'this site is disabled')
        return row['id']

    def authorize(self, token, repo=None):
        """Authenticate and return ``(tenant_id, app, repos_to_grant, permissions)``.

        ``repo`` narrows the grant to one repository and must already be inside
        the tenant's scope: a site can ask for less, never for more. The
        permissions are always the tenant's ceiling; a site cannot widen them.
        ``app`` is the site's own GitHub App; without one nothing is granted.
        """
        tenant = self.authenticate(token)
        scope, perms = self.scope_for(tenant)
        if not scope:
            raise SiteScmDenied(403, 'scm_not_configured',
                                '這個站台還沒設定可存取的 repository。')
        app = self.app_for(tenant)
        if app is None:
            raise SiteScmDenied(403, 'scm_no_app',
                                '這個站台還沒有自己的 GitHub 身份（GitHub App）。')
        if repo is None:
            return tenant, app, list(scope), perms
        wanted = repo.strip() if isinstance(repo, str) else ''
        if not _REPO_RE.fullmatch(wanted):
            raise SiteScmDenied(400, 'invalid_repo', 'invalid repository parameter')
        owner = wanted.split('/', 1)[0].lower()
        for allowed in scope:
            if allowed.lower() == wanted.lower():
                return tenant, app, [allowed], perms
            if _ORG_RE.fullmatch(allowed) and allowed[:-2].lower() == owner:
                return tenant, app, [wanted], perms
        raise SiteScmDenied(403, 'repo_not_allowed', '這個 repository 不在站台允許的範圍內。')

    # -- audit ------------------------------------------------------------------

    def record(self, tenant_id, request_id, repos, installation_id, expires_at, outcome, detail=None):
        with self.core._connect() as conn:
            conn.execute('INSERT OR REPLACE INTO support_site_scm_mints '
                         'VALUES (?,?,?,?,?,?,?,?)',
                         (tenant_id, request_id, json.dumps(list(repos or [])),
                          installation_id, expires_at, outcome,
                          (detail or '')[:500] or None, self.core.clock()))

    def recent_mints(self, tenant_id, limit=20):
        with self.core._connect() as conn:
            rows = conn.execute('SELECT * FROM support_site_scm_mints WHERE tenant_id=? '
                                'ORDER BY created_at DESC LIMIT ?', (tenant_id, int(limit))).fetchall()
        return [dict(row) for row in rows]
