"""SCM token relay: customer sites -> short-lived GitHub App installation tokens.

The GitHub App private key can mint a token for every installation the app
has, so it never enters a site. A site holds only its relay key (``scs_...``,
see ``support_site_scm.py``) and asks here for a token. The relay:

* authenticates the relay key and looks up the tenant's scope;
* signs an app JWT (RS256 via the system ``openssl``; no extra dependency);
* finds the installation for the repository owner;
* mints an installation token narrowed to the tenant's repositories AND
  permission ceiling (``repositories`` / ``permissions`` body parameters). The
  App may hold more; the token never does.

Tokens are cached until five minutes before they expire, so a burst of git
operations costs one GitHub call. Every real mint and every refusal is written
to ``support_site_scm_mints``; cache hits are not (they grant nothing new).

Only one route: ``POST /scm/v1/github/token`` with ``Authorization: Bearer
scs_...`` and an optional JSON body ``{"repo": "owner/name"}``. It is mounted
into ``site_model_relay.py`` so it shares that relay's bind address, peer check
and firewall opening (172.17.0.1:8212) instead of needing a new host port.

Every site speaks with its OWN GitHub App (its own ``name[bot]`` on GitHub):
an installation token carries the App's identity, so a shared App would make
one customer's actions indistinguishable from another's (or the operator's).
A site is bound to an App by name (``SiteScmAccess.bind_app``); a site without
a binding gets nothing. There is deliberately no default App.

Configuration (read by ``site_model_relay.build``):
  SCM_APPS_FILE   JSON registry, 0600, outside every site; unset = relay disabled
                  {"acme": {"client_id": "Iv1...", "pem_file": "/abs/key.pem"}, ...}
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import stat
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

API = 'https://api.github.com'
JWT_LIFETIME = 540          # GitHub allows at most 600 s
JWT_REUSE = 420             # re-sign a minute or two before expiry
TOKEN_MARGIN = 300          # hand out a cached token only if it has 5 min left
INSTALLATION_TTL = 600
MAX_CACHED_TOKENS = 512
_CLIENT_ID_RE = re.compile(r'(Iv[0-9A-Za-z.]{8,40}|[0-9]{1,12})')
_OWNER_RE = re.compile(r'[A-Za-z0-9_.-]{1,100}')


class MintError(Exception):
    """Mint failed. ``status``/``code`` are what the site sees; ``detail`` is
    for the audit log only (it may describe GitHub's answer)."""

    def __init__(self, status, code, message, detail=None):
        super().__init__(message)
        self.status, self.code, self.detail = status, code, detail or message


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _expires(value):
    """GitHub's ``expires_at`` (ISO 8601, ``Z``) -> epoch seconds, or None."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def check_private_key(path):
    """Refuse a key file that others can read, or that is not a plain file."""
    path = Path(path)
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError(f'{path} is not a regular file')
    if st.st_mode & 0o077:
        raise RuntimeError(f'{path} must not be readable by group/others (chmod 600)')
    if st.st_size > 16384 or b'PRIVATE KEY' not in path.read_bytes():
        raise RuntimeError(f'{path} does not look like a PEM private key')


class AppSigner:
    """Signs (and briefly reuses) the GitHub App JWT."""

    def __init__(self, client_id, pem_path, *, clock=time.time, sign=None):
        if not isinstance(client_id, str) or not _CLIENT_ID_RE.fullmatch(client_id):
            raise ValueError('invalid GitHub App client id')
        self.client_id, self.pem_path, self.clock = client_id, str(pem_path), clock
        self._sign = sign or self._openssl_sign
        self._cached = (None, 0.0)
        self._lock = threading.Lock()

    def _openssl_sign(self, message):
        done = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', self.pem_path],
                              input=message, capture_output=True, timeout=10)
        if done.returncode != 0 or not done.stdout:
            raise MintError(502, 'signing_failed', 'token service is misconfigured',
                            'openssl could not sign the app JWT')
        return done.stdout

    def jwt(self):
        with self._lock:
            token, issued = self._cached
            now = self.clock()
            if token and now - issued < JWT_REUSE:
                return token
            header = _b64(b'{"alg":"RS256","typ":"JWT"}')
            payload = _b64(json.dumps({'iat': int(now) - 60, 'exp': int(now) + JWT_LIFETIME,
                                       'iss': self.client_id}, separators=(',', ':')).encode())
            signing_input = f'{header}.{payload}'.encode('ascii')
            token = f'{header}.{payload}.{_b64(self._sign(signing_input))}'
            self._cached = (token, now)
            return token


class GitHubAppMinter:
    """Installation lookup + narrowed token minting, with caches."""

    def __init__(self, signer, *, transport=None, clock=time.time, api=API):
        import httpx

        self.signer, self.clock, self.api = signer, clock, api.rstrip('/')
        self.client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                        timeout=httpx.Timeout(connect=10, read=20, write=10, pool=10))
        self._installations = {}   # owner(lower) -> (id, looked_up_at)
        self._tokens = {}          # cache key -> (token, expires_epoch, payload)

    async def aclose(self):
        await self.client.aclose()

    async def _app_headers(self):
        from starlette.concurrency import run_in_threadpool

        jwt = await run_in_threadpool(self.signer.jwt)
        return {'Authorization': f'Bearer {jwt}', 'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'swarm-scm-relay'}

    async def installation_id(self, owner):
        key = owner.lower()
        hit = self._installations.get(key)
        if hit and self.clock() - hit[1] < INSTALLATION_TTL:
            return hit[0]
        headers = await self._app_headers()
        for kind in ('orgs', 'users'):
            try:
                response = await self.client.get(f'{self.api}/{kind}/{owner}/installation', headers=headers)
            except Exception as exc:
                raise MintError(502, 'github_unreachable', 'GitHub is unreachable', repr(exc)[:200])
            if response.status_code == 200:
                value = response.json().get('id')
                if type(value) is not int:
                    break
                self._installations[key] = (value, self.clock())
                return value
            if response.status_code == 401:
                raise MintError(502, 'app_auth_failed', 'token service is misconfigured',
                                'GitHub rejected the app JWT (client id / private key mismatch?)')
            if response.status_code != 404:
                raise MintError(502, 'github_error', 'GitHub refused the request',
                                f'installation lookup HTTP {response.status_code}')
        raise MintError(403, 'installation_missing',
                        f'GitHub App 沒有安裝在 {owner}。',
                        f'no installation for {owner}')

    def _prune(self):
        now = self.clock()
        for key in [k for k, (_, exp, _) in self._tokens.items() if exp - now <= TOKEN_MARGIN]:
            del self._tokens[key]
        while len(self._tokens) > MAX_CACHED_TOKENS:
            self._tokens.pop(next(iter(self._tokens)))

    async def mint(self, tenant, owner, names, permissions):
        """Return ``(payload, installation_id, minted)``.

        ``names`` is a list of repository names under ``owner``, or None for
        every repository the installation can reach.
        """
        installation = await self.installation_id(owner)
        key = (tenant, installation, None if names is None else tuple(sorted(n.lower() for n in names)),
               json.dumps(permissions, sort_keys=True))
        self._prune()
        cached = self._tokens.get(key)
        if cached and cached[1] - self.clock() > TOKEN_MARGIN:
            return cached[2], installation, False
        body = {'permissions': permissions}
        if names is not None:
            body['repositories'] = list(names)
        headers = await self._app_headers()
        try:
            response = await self.client.post(f'{self.api}/app/installations/{installation}/access_tokens',
                                              headers=headers, json=body)
        except Exception as exc:
            raise MintError(502, 'github_unreachable', 'GitHub is unreachable', repr(exc)[:200])
        if response.status_code in (404, 422):
            # Unknown repository in the list, or a permission the app lacks.
            self._installations.pop(owner.lower(), None)
            raise MintError(403, 'not_grantable', '要求的 repository 或權限不在 GitHub App 的安裝範圍內。',
                            f'mint HTTP {response.status_code}')
        if response.status_code == 401:
            raise MintError(502, 'app_auth_failed', 'token service is misconfigured',
                            'GitHub rejected the app JWT while minting')
        if response.status_code != 201:
            raise MintError(502, 'github_error', 'GitHub refused the request',
                            f'mint HTTP {response.status_code}')
        data = response.json()
        token, expires_at = data.get('token'), data.get('expires_at')
        expires = _expires(expires_at)
        if not isinstance(token, str) or not token.startswith('ghs_') or expires is None:
            raise MintError(502, 'github_error', 'GitHub returned an unexpected answer',
                            'mint response missing token/expires_at')
        payload = {'token': token, 'expires_at': expires_at,
                   'permissions': data.get('permissions') or permissions,
                   'repositories': 'all' if names is None else [f'{owner}/{n}' for n in names]}
        self._tokens[key] = (token, expires, payload)
        return payload, installation, True


def plan(grants):
    """Turn granted entries into ``(owner, names_or_None)``.

    Raises ValueError when they span more than one owner: an installation token
    belongs to one installation, so the site must name the repository.
    """
    owners = {g.split('/', 1)[0].lower() for g in grants}
    if len(owners) != 1:
        raise ValueError('repo required')
    owner = grants[0].split('/', 1)[0]
    if not _OWNER_RE.fullmatch(owner):
        raise ValueError('invalid owner')
    if any(g.endswith('/*') for g in grants):
        return owner, None
    return owner, [g.split('/', 1)[1] for g in grants]


def create_routes(access, minters, *, peer_ok, error):
    """Routes to mount into the site relay app. ``minters`` maps an App name to
    its GitHubAppMinter. ``peer_ok``/``error`` are the relay's own peer check
    and error responder."""
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from support_site_scm import SiteScmDenied

    def bearer(request):
        header = request.headers.get('authorization', '')
        return header[7:].strip() if header[:7].lower() == 'bearer ' else ''

    async def github_token(request):
        if not peer_ok(request):
            return error(403, 'forbidden', 'forbidden')
        raw = await request.body()
        if len(raw) > 4096:
            return error(413, 'request_too_large', 'request too large')
        repo = None
        if raw.strip():
            try:
                body = json.loads(raw)
            except Exception:
                return error(400, 'invalid_request', 'invalid JSON')
            if not isinstance(body, dict) or set(body) - {'repo'}:
                return error(400, 'invalid_request', 'only "repo" may be given')
            repo = body.get('repo')
            if repo is not None and not isinstance(repo, str):
                return error(400, 'invalid_repo', 'invalid repository parameter')
        try:
            tenant, app, grants, permissions = await run_in_threadpool(access.authorize, bearer(request), repo)
        except SiteScmDenied as denied:
            return error(denied.status, denied.code, str(denied))
        request_id = secrets.token_hex(12)
        minter = minters.get(app)
        if minter is None:
            await run_in_threadpool(access.record, tenant, request_id, grants, None, None,
                                    'error', f'site is bound to app {app!r}, which is not configured')
            return error(503, 'scm_app_unavailable', '這個站台的 GitHub 身份目前無法使用，請聯絡平台管理員。')
        try:
            owner, names = plan(grants)
        except ValueError:
            await run_in_threadpool(access.record, tenant, request_id, grants, None, None,
                                    'refused', 'scope spans several owners; repo not given')
            return error(400, 'repo_required', '這個站台可存取多個帳號的 repository，請指定 repo。')
        try:
            payload, installation, minted = await minter.mint(tenant, owner, names, permissions)
        except MintError as failed:
            outcome = 'refused' if failed.status < 500 else 'error'
            await run_in_threadpool(access.record, tenant, request_id, grants, None, None,
                                    outcome, failed.detail)
            return error(failed.status, failed.code, str(failed))
        if minted:
            await run_in_threadpool(access.record, tenant, request_id, grants, installation,
                                    _expires(payload['expires_at']), 'granted', None)
        return JSONResponse(payload, headers={'Cache-Control': 'no-store'})

    return [Route('/scm/v1/github/token', github_token, methods=['POST'])]


_APP_NAME_RE = re.compile(r'[a-z0-9][a-z0-9-]{0,62}')


def load_apps(path):
    """Parse the App registry. Refuses a readable-by-others file, bad names,
    relative key paths and keys that are themselves unsafe."""
    path = Path(path)
    check_private_key_like(path)
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not data:
        raise RuntimeError(f'{path}: expected a non-empty object of apps')
    apps = {}
    for name, entry in data.items():
        if not _APP_NAME_RE.fullmatch(name) or not isinstance(entry, dict):
            raise RuntimeError(f'{path}: invalid app entry {name!r}')
        client_id, pem = entry.get('client_id'), entry.get('pem_file')
        if not isinstance(pem, str) or not Path(pem).is_absolute():
            raise RuntimeError(f'{path}: {name}.pem_file must be an absolute path')
        check_private_key(pem)
        apps[name] = (client_id, pem)
    return apps


def check_private_key_like(path):
    """The registry names private keys: same rule as a key, minus the PEM check."""
    st = Path(path).lstat()
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError(f'{path} is not a regular file')
    if st.st_mode & 0o077:
        raise RuntimeError(f'{path} must not be readable by group/others (chmod 600)')


def build_minters():
    """``{app name: minter}`` from the environment, or None when not configured."""
    registry = os.environ.get('SCM_APPS_FILE', '').strip()
    if not registry:
        return None
    if not Path(registry).is_absolute():
        raise RuntimeError('SCM_APPS_FILE must be an absolute path')
    return {name: GitHubAppMinter(AppSigner(client_id, pem))
            for name, (client_id, pem) in load_apps(registry).items()}
