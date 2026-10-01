"""Site engine: create, update and remove customer DSH sites.

Each site is one DeepSeek Harness container with its own DSH_HOME, workspace,
login and model relay, on an isolated Docker network. Shared plumbing (state,
ports, DNS, reverse proxy) lives in common.py.

Settings (environment):
  SWARM_SITE_IMAGE           image built from site/Dockerfile
  SWARM_SITE_NETWORK         isolated Docker network name (empty: default bridge)
  SWARM_SITE_SUBNET/GATEWAY  that network's subnet and gateway
  SWARM_SITE_BRIDGE          its Linux bridge name (the firewall matches on it)
  SWARM_MODEL_RELAY          host:port of the platform model relay sites may reach
  SWARM_RELAY_TRUSTED_PEERS  proxy IPs trusted by the in-site relay (no site network)
  SWARM_ENTRY_SECRET         master secret for single-use entry tickets
  SWARM_ISOLATION_PROBES     extra host:port pairs a site must not reach, comma-separated
"""

from __future__ import annotations

import argparse
import contextlib
import urllib.request
import fcntl
import json
import stat
import tempfile
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

import common
import safe_fs
from common import (
    CONTAINER_USER,
    SiteError,
    TEMPLATES_DIR,
    allocate_port,
    container_name,
    generate_password,
    load_state,
    proxy_add,
    proxy_remove,
    run,
    site_host,
    site_root,
    state_path,
)

# dsh 站台專用的 image（由 site/Dockerfile build）
DSH_IMAGE = os.environ.get("SWARM_SITE_IMAGE", "swarm-site:latest")

# dsh 站台的內部 relay 對外埠（容器內 0.0.0.0 監聽）
RELAY_PORT = 3080
# dsh web 本身綁定 127.0.0.1 的埠
DSH_TARGET_PORT = 3081
# 客戶的工作資料夾（HOME）：與 DSH_HOME 分開，放在站台自己的目錄下，重建容器不遺失
WORKSPACE_IN_CONTAINER = "/home/dsh/workspace"


def _workspace_dir(name: str) -> Path:
    """Host dir for the customer's workspace; created private to the site uid."""
    path = site_root(name) / "workspace"
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise SiteError("site workspace must be a real directory")
    return path

ENGINE = "dsh"


def save_state(name: str, state: dict) -> None:
    """Publish complete DSH YAML atomically; failed writes preserve old state."""
    path = state_path(name)
    payload = yaml.safe_dump(state, allow_unicode=True, sort_keys=False)
    fd, temporary = tempfile.mkstemp(prefix=".site.yaml-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextlib.contextmanager
def _create_lock():
    """Serialize DSH creates, including rollback, on a persistent private inode.

    The lock lives OUTSIDE SITES_ROOT so site cleanup cannot unlink it. Never
    unlink this file: waiters must all flock the same inode.
    Parent directories must already exist, be trusted, and contain no symlinks.
    """
    root = Path(os.path.abspath(common.SITES_ROOT))
    with contextlib.ExitStack() as stack:
        directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, directory)
        # Walk via dirfds: O_NOFOLLOW on just the final parent misses ancestors.
        for component in root.parent.parts[1:]:
            directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
            stack.callback(os.close, directory)
        if root.is_symlink():
            raise SiteError("DSH sites root must not be a symlink")
        try:
            fd = os.open(f".{root.name}.dsh-create.lock",
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=directory)
        except OSError as exc:
            raise SiteError("Cannot safely open DSH create lock") from exc
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
            raise SiteError("DSH create lock must be a private owned regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def allocate_free_port() -> int:
    """配一個 dsh 站可用 port：掃 site.yaml 紀錄 + 實際可 bind 測試。

    common.allocate_port 只掃 site.yaml 紀錄的 port，可能撞上被其他服務
    （非站台容器／docker-proxy）佔用的 port（實測 18002 被別的東西占用即撞車）。
    這裡多加一層「真的能 bind」檢查，確保選到的 port 確實可用。
    """
    import socket

    allocated = set()
    if common.SITES_ROOT.exists():
        for site_yaml in common.SITES_ROOT.glob("*/site.yaml"):
            data = yaml.safe_load(site_yaml.read_text(encoding="utf-8")) or {}
            if isinstance(data.get("port"), int):
                allocated.add(data["port"])
    for port in range(common.PORT_MIN, common.PORT_MAX + 1):
        if port in allocated:
            continue
        if not _port_free(port):
            continue
        return port
    raise common.SiteError(f"dsh port 範圍 {common.PORT_MIN}-{common.PORT_MAX} 無可用埠")


def _port_free(port: int) -> bool:
    """檢查 TCP port 是否可 bind（未被監聽）。"""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def init_dsh_home(name: str) -> Path:
    """建站台的 DSH_HOME 目錄結構。

    DSH_HOME 布局：
      {SITES_ROOT}/{name}/dsh-home/
        profiles/web/        <- 空（容器啟動時由 relay 從 image 內 /opt/dsh-web-profile 複製）
        dsh.env              <- 站台密碼等環境變數

    重要：這裡「不複製 profile」。乾淨的 web profile（含 web-auth / subscriptions bundle
    與 node_modules）build 進 image 的 /opt/dsh-web-profile，由容器啟動時的 relay.mjs
    ensureProfile 負責灌入 DSH_HOME/profiles/web。避免 host 側複製來源出錯（曾把
    admin 目錄誤灌進 profile）。
    """
    dsh_home = site_root(name) / "dsh-home"
    dsh_home.mkdir(parents=True, exist_ok=True)
    (dsh_home / "profiles" / "web").mkdir(parents=True, exist_ok=True)
    return dsh_home


def _copy_profile(src: Path, dst: Path) -> None:
    """把 profile 骨架（除 node_modules）複製到站台，node_modules 用 symlink 指回 image 內共享，省空間。"""
    # 先複製非 node_modules 檔案
    for item in src.iterdir():
        if item.name in ("node_modules",):
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    # node_modules：站台內建一個，讓 dsh 解析 plugins 用；本機開發直接複製
    src_nm = src / "node_modules"
    dst_nm = dst / "node_modules"
    if src_nm.exists() and not dst_nm.exists():
        shutil.copytree(src_nm, dst_nm)


def web_auth_password_hash(password: str) -> str:
    """dsh-web-auth scrypt format: Base64URL salt and a fixed 64-byte key."""
    import base64
    import hashlib
    import secrets

    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)
    b64url = lambda value: base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
    return f"scrypt$16384$8$1${b64url(salt)}${b64url(digest)}"


def site_entry_secret(master: str, site_host: str) -> str:
    """Same derivation as platform/support_site_entry.site_entry_secret."""
    import hashlib
    import hmac

    if len(master) < 32:
        raise SiteError("SWARM_ENTRY_SECRET must be at least 32 characters")
    return hmac.new(master.encode("utf-8"), b"dsh-site-entry\0" + site_host.encode("ascii"),
                    hashlib.sha256).hexdigest()


def write_dsh_env(name: str, password: str, trusted_host: str | None = None) -> Path:
    """寫站台 dsh.env（WEB_AUTH + relay 設定）。"""
    relay_peers = None
    if trusted_host:
        import ipaddress
        import re
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', trusted_host):
            raise SiteError('Invalid DSH public host')
        # On the isolated site network the only proxy in front of the site is
        # docker-proxy, which connects from that network's gateway.
        relay_peers = SITE_GATEWAY if SITE_NETWORK else os.environ.get('SWARM_RELAY_TRUSTED_PEERS', '')
        try:
            peers = relay_peers.split(',')
            if not relay_peers or any(not item or item != item.strip() for item in peers):
                raise ValueError
            for item in peers:
                ipaddress.ip_address(item)
        except ValueError:
            raise SiteError('Configure SWARM_RELAY_TRUSTED_PEERS with verified immediate proxy IPs before provisioning') from None

    password_hash = web_auth_password_hash(password)

    dsh_home = site_root(name) / "dsh-home"
    path = dsh_home / "dsh.env"
    lines = [
        "NODE_OPTIONS=--no-warnings",
        "DSH_TELEMETRY_DISABLED=1",
        "WEB_AUTH_USERNAME=admin",
        f"WEB_AUTH_PASSWORD_HASH={password_hash}",
        "WEB_AUTH_MODE=always",
    ]
    if trusted_host:
        lines.extend([f"DSH_TRUSTED_HOST={trusted_host}",
                      f"RELAY_PUBLIC_ORIGIN=https://{trusted_host}",
                      f"RELAY_TRUSTED_PEERS={relay_peers}"])
    # Secret the controlling platform uses to sign single-use entry tickets, so
    # a customer never needs to know this site's password. Independent of the
    # password hash: holding it must not reveal or verify the password.
    entry_secret = os.environ.get("SWARM_ENTRY_SECRET", "").strip()
    if entry_secret and trusted_host:
        # Per-site key, never the platform master: the site runs customer code.
        lines.append(f"WEB_AUTH_ENTRY_SECRET={site_entry_secret(entry_secret, trusted_host)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


# ---------------------------------------------------------------------------
# Site network. A site's AI runs arbitrary commands (DSH without a sandbox:
# Docker's default seccomp profile blocks Landlock, so DSH's own sandbox cannot
# work in a container). What contains it is the container itself plus this
# network: outbound internet stays open, but other containers, every host port
# except the model relay, private/VPC ranges and the cloud metadata service are
# unreachable. The host firewall (deploy/systemd/swarm-site-firewall.sh) does
# the blocking; docker_run PROVES it with a probe before it starts any site
# unrestricted, and refuses otherwise.
SITE_NETWORK = os.environ.get("SWARM_SITE_NETWORK", "")
SITE_SUBNET = os.environ.get("SWARM_SITE_SUBNET", "172.29.10.0/24")
SITE_GATEWAY = os.environ.get("SWARM_SITE_GATEWAY", "172.29.10.1")
SITE_BRIDGE = os.environ.get("SWARM_SITE_BRIDGE", "br-swarm-sites")
# The metadata IP is the host's default resolver; sites use public DNS instead.
SITE_DNS = ("1.1.1.1", "8.8.8.8")
MODEL_RELAY = os.environ.get("SWARM_MODEL_RELAY", "172.17.0.1:8212")
# Host / neighbour services a site must NOT reach (sampled, not exhaustive: the
# firewall drops whole ranges; these prove the rules are loaded).
def _extra_probes() -> tuple[tuple[str, int], ...]:
    out = []
    for item in os.environ.get("SWARM_ISOLATION_PROBES", "").split(","):
        host, _, port = item.strip().rpartition(":")
        if host and port.isdigit():
            out.append((host, int(port)))
    return tuple(out)


ISOLATION_MUST_BLOCK = (
    ("172.17.0.1", 22), (SITE_GATEWAY, 22), ("169.254.169.254", 80),
) + _extra_probes()

_PROBE_JS = r"""
const net = require('net'), dns = require('dns').promises;
const [allow, block] = JSON.parse(process.argv[1]);
const tryc = (h, p) => new Promise(r => { const s = net.connect({host: h, port: p});
  const t = setTimeout(() => { s.destroy(); r(false) }, 3000);
  s.on('connect', () => { clearTimeout(t); s.destroy(); r(true) });
  s.on('error', () => { clearTimeout(t); r(false) }) });
(async () => {
  const out = {allow: {}, block: {}};
  for (const [h, p] of allow) out.allow[h + ':' + p] = await tryc(h, p);
  for (const [h, p] of block) out.block[h + ':' + p] = await tryc(h, p);
  try { const a = await dns.lookup('github.com'); out.dns = true;
        out.internet = await tryc(a.address, 443) } catch { out.dns = false; out.internet = false }
  console.log(JSON.stringify(out));
})();
"""


def ensure_site_network() -> None:
    """Create the site network, or verify an existing one is exactly ours."""
    try:
        raw = run(["docker", "network", "inspect", SITE_NETWORK])
    except SiteError:
        run(["docker", "network", "create", "--driver", "bridge",
             "--subnet", SITE_SUBNET, "--gateway", SITE_GATEWAY,
             "-o", "com.docker.network.bridge.enable_icc=false",
             "-o", f"com.docker.network.bridge.name={SITE_BRIDGE}",
             SITE_NETWORK])
        raw = run(["docker", "network", "inspect", SITE_NETWORK])
    info = (json.loads(raw) or [{}])[0]
    config = (info.get("IPAM") or {}).get("Config") or [{}]
    options = info.get("Options") or {}
    if (config[0].get("Subnet") != SITE_SUBNET or config[0].get("Gateway") != SITE_GATEWAY
            or options.get("com.docker.network.bridge.enable_icc") != "false"
            or options.get("com.docker.network.bridge.name") != SITE_BRIDGE):
        raise SiteError(f"docker network {SITE_NETWORK} exists with other settings; refusing")


def verify_site_isolation(image: str) -> dict:
    """Probe from a throwaway container on the site network. Fail closed."""
    ensure_site_network()
    host, _, port = MODEL_RELAY.rpartition(":")
    spec = json.dumps([[[host, int(port)]], [list(item) for item in ISOLATION_MUST_BLOCK]])
    out = run(["docker", "run", "--rm", "--network", SITE_NETWORK,
               *[arg for server in SITE_DNS for arg in ("--dns", server)],
               "--user", CONTAINER_USER, "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--memory", "128m",
               "--entrypoint", "node", image, "-e", _PROBE_JS, spec])
    try:
        result = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise SiteError("site isolation probe produced no result; refusing") from None
    open_ = [target for target, ok in result.get("block", {}).items() if ok]
    if open_:
        raise SiteError("site network is not isolated (reachable: " + ", ".join(open_) +
                        "); run deploy/systemd/swarm-site-firewall.sh as root first")
    if not all(result.get("allow", {}).values()) or not result.get("allow"):
        raise SiteError("site network cannot reach the model relay; check the firewall")
    if not (result.get("dns") and result.get("internet")):
        raise SiteError("site network has no internet access; check the firewall")
    return result


def docker_run(
    name: str, port: int, env_file: Path, cpus: str, memory: str,
    *, cid_file: Path | None = None, image: str | None = None,
) -> None:
    """dsh 容器：mount DSH_HOME（單目錄），relay 埠 3080 對外。

    With SWARM_SITE_NETWORK set the site joins the isolated site network
    and its AI may run any command; that happens only after the isolation
    probe passes. Without it the site stays sandboxed on the default bridge.
    """
    root = site_root(name)
    dsh_home = root / "dsh-home"
    isolation: list[str] = []
    if SITE_NETWORK:
        verify_site_isolation(image or DSH_IMAGE)
        if _env_value(env_file, "RELAY_TRUSTED_PEERS") != SITE_GATEWAY:
            raise SiteError(f"RELAY_TRUSTED_PEERS must be the site network gateway {SITE_GATEWAY}")
        isolation = ["--network", SITE_NETWORK,
                     *[arg for server in SITE_DNS for arg in ("--dns", server)],
                     "-e", "DSH_PERMISSION_MODE=danger-full-access"]
    run(
        [
            "docker", "run", "-d",
            *isolation,
            *(["--cidfile", str(cid_file)] if cid_file is not None else []),
            "--name", container_name(name),
            "--restart", "unless-stopped",
            "--user", CONTAINER_USER,
            "--cpus", cpus,
            "--memory", memory,
            "--env-file", str(env_file),
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "-e", f"DSH_HOME=/home/dsh/dsh-home",
            # The container runs as the host uid, which has no passwd entry, so
            # HOME would default to "/" (read-only): the folder picker then
            # opens at "/" and "new folder" fails with EACCES. Point HOME at a
            # persistent, writable workspace on the site's own volume.
            "-e", f"HOME={WORKSPACE_IN_CONTAINER}",
            "-e", "DSH_BIN=dsh",
            "-e", f"WEB_AUTH_PASSWORD_HASH={_env_value(env_file, 'WEB_AUTH_PASSWORD_HASH')}",
            *(["-e", f"WEB_AUTH_ENTRY_SECRET={_env_value(env_file, 'WEB_AUTH_ENTRY_SECRET')}"]
              if _env_value(env_file, 'WEB_AUTH_ENTRY_SECRET') else []),
            "-v", f"{dsh_home}:/home/dsh/dsh-home",
            "-v", f"{_workspace_dir(name)}:{WORKSPACE_IN_CONTAINER}",
            "-p", f"127.0.0.1:{port}:{RELAY_PORT}",
            image or DSH_IMAGE,
        ]
    )


def _env_value(env_file: Path, key: str) -> str:
    data = _read_private(env_file) if "dsh-home" in Path(env_file).parts else Path(env_file).read_bytes()
    for line in (data or b"").decode("utf-8").splitlines():
        line = line.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return ""


def _docker_rm_id(identifier: str) -> None:
    result = subprocess.run(
        ["docker", "rm", "-f", identifier], capture_output=True, text=True
    )
    # Missing containers are already clean; daemon/permission failures are not.
    if result.returncode and "No such container:" not in result.stderr:
        raise SiteError(f"Docker cleanup failed for {identifier}: {result.stderr.strip()}")


def docker_rm(name: str) -> None:
    _docker_rm_id(container_name(name))


def _rollback_create(
    name: str, root: Path, *, container: bool, record_id: str | None, proxy: bool,
) -> list[str]:
    """Rollback only resources acquired by this create (swarm-next#9).

    Keep the owned directory on cleanup failure for diagnosis/recovery. DNS IDs
    live in memory as well as state: saving state can itself be the failing step.
    A cidfile proves ownership even when docker run creates then fails to start.
    """
    errors: list[str] = []

    def attempt(label, action):
        try:
            action()
        except Exception as exc:
            errors.append(f"{label}: {exc}")

    if proxy:
        attempt("proxy", lambda: proxy_remove(name))
    if record_id:
        attempt("dns", lambda: common.dns_delete(record_id))
    cid_file = root / ".create-container-id"
    if cid_file.exists():
        def remove_owned_container():
            cid = cid_file.read_text(encoding="utf-8").strip()
            if cid:
                _docker_rm_id(cid)
            elif container:
                docker_rm(name)
        attempt("container", remove_owned_container)
    elif container:
        attempt("container", lambda: docker_rm(name))
    if not errors:
        attempt("data", lambda: shutil.rmtree(root))
    return errors


def read_env_file(path: Path) -> dict[str, str]:
    return common.read_env_file(path)


def cmd_create(args: argparse.Namespace) -> None:
    """建立一個 dsh 站台。DSH-only lock spans allocation through rollback."""
    with _create_lock():
        _cmd_create_locked(args)


def _cmd_create_locked(args: argparse.Namespace) -> None:
    from names import validate_name

    name = validate_name(args.name)
    root = site_root(name)
    if root.exists():
        raise SiteError(f"站台目錄已存在：{root}")

    password = args.password or generate_password()
    trusted_host = site_host(name)
    port = allocate_free_port()

    # Exclusive mkdir is the ownership boundary: never remove a pre-existing
    # directory, including one created between the existence check and mkdir.
    root.mkdir(parents=True, exist_ok=False)
    container = proxy = False
    record_id = None
    cid_file = root / ".create-container-id"
    try:
        (root / "keys").mkdir()
        init_dsh_home(name)
        env_file = write_dsh_env(name, password, trusted_host=trusted_host)
        save_state(
            name,
            {
                "name": name,
                "host": site_host(name),
                "port": port,
                "image": DSH_IMAGE,
                "engine": ENGINE,
                "cpus": args.cpus,
                "memory": args.memory,
            },
        )
        docker_run(name, port, env_file, args.cpus, args.memory, cid_file=cid_file)
        container = True
        record_id = common.dns_create(name)
        state = load_state(name)
        state["dns_record_id"] = record_id
        save_state(name, state)
        proxy_add(name, port)
        proxy = True
        # Require authenticated bootstrap evidence, not merely relay readiness.
        _dsh_self_test(port, password, retries=args.self_test_retries if hasattr(args, "self_test_retries") else 12, base_url=f"https://{trusted_host}")
        cid_file.unlink(missing_ok=True)
    except Exception as exc:
        errors = _rollback_create(
            name, root, container=container, record_id=record_id, proxy=proxy
        )
        if errors:
            raise SiteError(
                f"Create failed: {exc}; rollback incomplete ({'; '.join(errors)}); "
                f"retained data: {root}; DNS record: {record_id or 'none'}"
            ) from exc
        raise

    print(f"站台建立完成：https://{site_host(name)}")
    print(f"  密碼:          {'（自訂）' if password else password}")
    print(f"  DSH_HOME:      {root / 'dsh-home'}")


# Identify this probe honestly. Without it urllib sends "Python-urllib/<ver>",
# which edge protection reasonably treats as an unattributed generic script
# (observed: Cloudflare 1010 browser_signature_banned on an otherwise healthy
# site). This is a truthful signature for attribution and rate-limit policy, NOT
# a browser impersonation string: it must never claim to be Mozilla/Chrome, and
# operators must remain able to block or allow it deliberately.
PROBE_USER_AGENT = "dsh-provisioning-probe/1.0 (+swarm-hub sitectl self-test)"


class _DshHTTPTransport:
    """One isolated cookie jar; no proxies or implicit redirects.

    request is the mock seam. Responses are (status, email-style headers, bytes).
    A short socket timeout, body cap and probe deadline bound normal failures.
    """

    def __init__(self):
        import http.cookiejar
        import urllib.request

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
        # build_opener keeps urllib's default UA in addheaders; replace it so no
        # request can fall back to the generic signature.
        self.opener.addheaders = [("User-Agent", PROBE_USER_AGENT)]

    def upgrade_status(self, url, *, timeout=10):
        """Attempt a WebSocket upgrade with this jar's cookies; return the status.

        Uses a raw socket because urllib cannot express an upgrade. Only the
        status line is read: no framing, no data exchange.
        """
        import base64
        import os
        import socket
        import ssl
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        secure = parsed.scheme == "https"
        host = parsed.hostname
        port = parsed.port or (443 if secure else 80)
        # Let the cookie jar decide what may be sent to this URL.
        probe = urllib.request.Request(url)
        for processor in self.opener.handlers:
            jar = getattr(processor, "cookiejar", None)
            if jar is not None:
                jar.add_cookie_header(probe)
                break
        cookie = probe.get_header("Cookie", "")
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        lines = [f"GET {parsed.path or '/'} HTTP/1.1", f"Host: {parsed.netloc}",
                 "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
                 f"Origin: {parsed.scheme}://{parsed.netloc}",
                 f"User-Agent: {PROBE_USER_AGENT}"]
        if cookie:
            lines.append(f"Cookie: {cookie}")
        raw = ("\r\n".join(lines) + "\r\n\r\n").encode("latin1")
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            if secure:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            sock.sendall(raw)
            head = sock.recv(256).decode("latin1", "replace")
        finally:
            sock.close()
        try:
            return int(head.split(" ", 2)[1])
        except (IndexError, ValueError):
            raise SiteError("dsh probe: malformed upgrade response") from None

    def request(self, method, url, *, headers, data=None, timeout=5, json_body=False):
        # json_body is accepted (and ignored) so callers can pass it uniformly;
        # the content type is already decided by the caller's headers.
        import urllib.error
        import urllib.request

        # Explicit per-request header too: Request headers win over addheaders,
        # so setting only one of the two would silently leave a gap.
        headers = {"User-Agent": PROBE_USER_AGENT, **headers}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            response = self.opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            import time

            deadline = time.monotonic() + timeout
            chunks = []
            size = 0
            # read1 returns after one underlying read, allowing the deadline
            # to catch a slow trickle instead of waiting to fill the body cap.
            reader = getattr(response, "read1", response.read)
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("response deadline")
                chunk = reader(min(65536, 1024 * 1024 + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > 1024 * 1024:
                    raise ValueError("response limit")
            return response.code, response.headers, b"".join(chunks)


class _DiagnosableProbeFailure(Exception):
    """A probe failure whose message is safe to show: built from a status code
    alone, never from a response body, URL or credential."""


def _entry_ticket(entry_key: str) -> str:
    """A 60 s single-use ticket, the same format support_site_entry issues."""
    import base64
    import hashlib
    import hmac
    import secrets
    import time

    expiry = int(time.time() * 1000) + 60_000
    nonce = secrets.token_urlsafe(24)
    mac = hmac.new(entry_key.encode("ascii"), f"{expiry}.{nonce}".encode("ascii"),
                   hashlib.sha256).digest()
    return f"{expiry}.{nonce}.{base64.urlsafe_b64encode(mac).decode('ascii').rstrip('=')}"


def _dsh_self_test(port: int, password: str | None, retries: int = 12, wait: float = 5.0,
                   *, transport_factory=None, base_url: str | None = None,
                   entry_key: str | None = None) -> None:
    """Fail closed unless login AND native cookie handoff reach DSH HTML.

    Readiness is separate from authenticated evidence. Only readiness retries:
    repeating the negative login check could exhaust the server's attempt limit.
    Production callers supply a public HTTPS origin: urllib verifies TLS and
    CookieJar retains real Secure-cookie semantics, without loopback tunneling.
    Omitted base_url preserves the standalone loopback API only.
    This HTTP evidence does not replace real-browser/WS acceptance testing.
    """
    import math
    import secrets
    import time
    from urllib.parse import urlencode, urljoin, urlsplit

    if not isinstance(retries, int) or not 1 <= retries <= 60:
        raise SiteError("dsh probe: retries must be 1..60")
    if not math.isfinite(wait) or not 0 <= wait <= 30:
        raise SiteError("dsh probe: wait must be 0..30")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise SiteError("dsh probe: invalid port")
    if password is None and not (isinstance(entry_key, str) and len(entry_key) >= 32):
        raise SiteError("dsh probe: a password or an entry key is required")
    factory = transport_factory or _DshHTTPTransport
    base = f"http://127.0.0.1:{port}"
    if base_url is not None:
        # Validate before constructing a transport or sending credentials. Keep
        # errors generic: even a malformed input URL might contain credentials.
        import ipaddress
        import re

        try:
            if not isinstance(base_url, str) or not base_url.isascii():
                raise ValueError("origin")
            if any(ord(c) < 33 or ord(c) == 127 for c in base_url) or "\\" in base_url:
                raise ValueError("origin")
            parsed = urlsplit(base_url)
            if (not base_url.startswith("https://") or parsed.scheme != "https"
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path not in ("", "/") or "?" in base_url or "#" in base_url):
                raise ValueError("origin")
            host = parsed.hostname or ""
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", host):
                raise ValueError("origin")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError("origin")
            if parsed.netloc.endswith(":") or (parsed.port is not None and not 1 <= parsed.port <= 65535):
                raise ValueError("origin")
            base = "https://" + parsed.netloc.lower()
        except (ValueError, TypeError):
            raise SiteError("dsh probe: base_url must be a public HTTPS DNS origin") from None
    origin = urlsplit(base)
    deadline = time.monotonic() + min(300, retries * (5 + wait) + 60)

    def require(condition):
        if not condition:
            raise ValueError("missing evidence")

    def target(current, location):
        require(isinstance(location, str) and bool(location))
        require(not any(ord(c) < 33 or ord(c) == 127 for c in location))
        require("\\" not in location)
        url = urljoin(current, location)
        parsed = urlsplit(url)
        require(parsed.scheme == origin.scheme and parsed.netloc == origin.netloc)
        require(parsed.username is None and parsed.password is None and not parsed.fragment)
        return url

    def request(client, method, url, *, html=False, data=None, json_body=False):
        remaining = deadline - time.monotonic()
        require(remaining > 0)
        headers = {"Accept": "text/html" if html else "application/json"}
        if data is not None:
            headers.update({
                "Content-Type": "application/json" if json_body
                else "application/x-www-form-urlencoded",
                "Origin": base,
                # The exchange requires same-origin fetch metadata, exactly as a
                # browser would send from the handoff page.
                "Sec-Fetch-Site": "same-origin",
            })
        return client.request(method, url, headers=headers, data=data, timeout=min(5, remaining))

    def status(client, authenticated):
        code, headers, body = request(client, "GET", base + "/auth/status")
        require(code == (200 if authenticated else 401))
        require(headers.get("Content-Type", "").split(";", 1)[0] == "application/json")
        result = json.loads(body)
        require(result.get("required") is True and result.get("authenticated") is authenticated)
        if authenticated:
            require(result.get("username") == "admin")

    def bootstrap(response):
        code, headers, body = response
        require(code == 200 and headers.get("Content-Type", "").split(";", 1)[0] == "text/html")
        text = body.decode("utf-8").lower()
        require("<html" in text and "__dsh_boot__" in text)
        require("/auth/bootstrap.js" in text)
        require("<form" not in text and 'type="password"' not in text)

    # A reachable auth status route is readiness, never authentication success.
    ready = False
    for attempt in range(retries):
        try:
            anonymous = factory()
            status(anonymous, False)
            ready = True
            break
        except Exception:
            if attempt + 1 < retries:
                time.sleep(min(wait, max(0, deadline - time.monotonic())))
    if not ready:
        raise SiteError("dsh probe: readiness failed; authenticated evidence absent") from None

    stage = "anonymous protection"
    try:
        code, headers, _ = request(anonymous, "GET", base + "/", html=True)
        require(code in (302, 303))
        login = target(base + "/", headers.get("Location"))
        require(urlsplit(login).path == "/auth/login")
        code, _, body = request(anonymous, "GET", base + "/api/sessions")
        require(code == 401 and json.loads(body).get("error") == "authentication_required")

        stage = "wrong-password rejection"
        wrong = factory()
        invalid = (password or "") + ".invalid-" + secrets.token_hex(16)
        data = urlencode({"username": "admin", "password": invalid, "next": "/"}).encode()
        code, _, _ = request(wrong, "POST", base + "/auth/login", data=data)
        require(code == 401)
        status(wrong, False)

        client = factory()
        if password is not None:
            stage = "password login"
            data = urlencode({"username": "admin", "password": password, "next": "/"}).encode()
            code, headers, _ = request(client, "POST", base + "/auth/login", data=data)
            login_url = base + "/auth/login"
        else:
            # No password known (the platform only keeps its hash): sign in the
            # way the customer does, with a one-time entry ticket for this site.
            stage = "entry ticket login"
            login_url = base + "/auth/enter?ticket=" + _entry_ticket(entry_key)
            code, headers, _ = request(client, "GET", login_url, html=True)
        require(code == 303)
        # CookieJar, rather than hand-built Cookie headers, handles path/domain,
        # expiry and multiple Set-Cookie headers (including the native cookie).
        require(any(value.startswith("dsh_web_auth=") for value in headers.get_all("Set-Cookie", [])))
        url = target(login_url, headers.get("Location"))
        stage = "native handoff"
        # The composition must never hand a launch token to the browser: login
        # redirects to a fixed tokenless page which performs a same-origin POST
        # exchange. Reject any Location that still carries a token.
        require("token=" not in url)
        for _ in range(6):
            require(urlsplit(url).path != "/auth/login")
            response = request(client, "GET", url, html=True)
            if response[0] not in (301, 302, 303, 307, 308):
                break
            url = target(url, response[1].get("Location"))
            require("token=" not in url)
        else:
            raise ValueError("redirect limit")

        # Whatever we landed on must be a real page: a 401/403 here means the
        # session did not survive the redirect, and must not be mistaken for
        # the handoff page or for DSH's index.
        require(response[0] == 200)
        if urlsplit(url).path == "/auth/handoff":
            stage = "tokenless native exchange"
            # The page must carry no inline script: under the served CSP an
            # inline block never executes and the customer waits forever.
            page = response[2].decode("utf-8", "replace").lower()
            require("<script>" not in page)
            require('src="/auth/handoff.js"' in page)
            # The page's own CSP must permit the exchange it performs. A missing
            # connect-src blocks the fetch in a browser while every server-side
            # check still passes, leaving the customer stuck on "please wait".
            csp = response[1].get("Content-Security-Policy") or ""
            require("connect-src 'self'" in csp)
            require("script-src 'self'" in csp)
            code, _, _ = request(client, "GET", base + "/auth/handoff.js")
            require(code == 200)
            # Drive the exchange the page would perform, then continue to the
            # clean index. A token must never appear in this request.
            code, _, _ = request(client, "POST", base + "/auth/native-session",
                                 data=b"{}", json_body=True)
            require(code == 204)
            response = request(client, "GET", base + "/", html=True)
            stage = "native handoff"
        bootstrap(response)
        status(client, True)
        # Clean URL reload proves cookies survive without the launch token.
        bootstrap(request(client, "GET", base + "/", html=True))

        stage = "websocket authorization"
        # HTML alone does not prove a usable site: the app opens a WebSocket
        # immediately, and an unauthenticated upgrade leaves the customer
        # watching "reconnecting" forever. Require the upgrade to get PAST
        # authentication; 404 (no such route) is acceptable, 401/403 is not.
        try:
            upgrade = client.upgrade_status(base + "/api/ws")
        except SiteError:
            raise
        except Exception as error:
            # A defect in the probe itself must be named as such, not blamed on
            # the site: an ImportError here once masqueraded as an
            # unauthenticated socket and failed provisioning for no reason.
            raise _DiagnosableProbeFailure(
                "dsh probe: websocket check could not run "
                f"({type(error).__name__}); this is a probe defect, not a site failure"
            ) from None
        if upgrade in (401, 403):
            # Name the status: a bare "failed" gave no way to tell an
            # unauthenticated socket from an edge rejecting the upgrade.
            raise _DiagnosableProbeFailure(
                f"dsh probe: websocket upgrade was refused with {upgrade}; the site "
                "would load but never connect. Check the session cookie's SameSite "
                "attribute and that the edge forwards Upgrade headers.")
    except _DiagnosableProbeFailure as failure:
        # Deliberately constructed here from a status code only; it contains no
        # response body, URL or credential, so it is safe to surface verbatim.
        raise SiteError(str(failure)) from None
    except Exception:
        # Never expose response bodies, exception strings, URLs, passwords or
        # cookies: native handoff URLs contain credentials too.
        raise SiteError(f"dsh probe: ready, but authenticated evidence failed ({stage}); browser acceptance required") from None


def cmd_delete(args: argparse.Namespace) -> None:
    from names import validate_name

    name = validate_name(args.name)
    state = load_state(name)
    if state.get("engine") != ENGINE:
        raise SiteError(f"站台 {name!r} 不是 dsh，拒絕刪除")
    errors: list[str] = []
    actions = [("container", lambda: docker_rm(name)),
               ("proxy", lambda: proxy_remove(name))]
    if state.get("dns_record_id"):
        actions.append(("dns", lambda: common.dns_delete(state["dns_record_id"])))
    for label, action in actions:
        try:
            action()
        except Exception as exc:
            errors.append(f"{label}: {exc}")
    if errors:
        raise SiteError(f"Delete incomplete ({'; '.join(errors)}); retained data: {site_root(name)}")
    if getattr(args, "purge_data", False):
        shutil.rmtree(site_root(name))
        print(f"站台 {name} 已刪除（含資料）")
    else:
        state["deleted"] = True
        state.pop("dns_record_id", None)
        save_state(name, state)
        print(f"站台 {name} 已刪除（資料保留於 {site_root(name)}，加 --purge-data 可清除）")


def cmd_update_resources(args: argparse.Namespace) -> None:
    """dsh 站「編修」：只更新資源上限並重建容器。

    dsh 站的模型／訂閱由站台內的 settings.yaml 與 subscriptions 插件管理，
    只動 cpus/memory。
    """
    _reject_unsafe_replacement(args.name)


def _active_dsh_name(name: str) -> str:
    from names import validate_name

    name = validate_name(name)
    state = load_state(name)
    if state.get("engine") != ENGINE or state.get("deleted"):
        raise SiteError(f"站台 {name!r} 不是可操作的 dsh 站台")
    return name


def _reject_unsafe_replacement(name: str) -> None:
    """Fail closed until replacement has a verified ownership/recovery contract.

    Renaming the old container alone cannot restore mutations to the shared
    writable DSH_HOME. A safe implementation also needs data isolation, staged
    env/state publication, the recorded image, authenticated readiness checks,
    and recoverable handling of ambiguous Docker failures. Do not destroy the
    working site or publish new credentials/resources before that exists.
    """
    name = _active_dsh_name(name)
    raise SiteError(
        f"DSH replacement disabled for {name!r}: safe replacement transaction "
        "is not implemented; container, env, state and image are unchanged"
    )


def cmd_stop(args: argparse.Namespace) -> None:
    name = _active_dsh_name(args.name)
    run(["docker", "stop", container_name(name)])


def cmd_start(args: argparse.Namespace) -> None:
    name = _active_dsh_name(args.name)
    run(["docker", "start", container_name(name)])


def cmd_reset_password(args: argparse.Namespace) -> None:
    _reject_unsafe_replacement(args.name)


SITE_PASSWORD_MIN = 12
SITE_PASSWORD_MAX = 1024
HASH_KEY = "WEB_AUTH_PASSWORD_HASH"


def _validate_site_password(password: str) -> str:
    if not isinstance(password, str):
        raise SiteError("site password must be text")
    if not SITE_PASSWORD_MIN <= len(password) <= SITE_PASSWORD_MAX:
        raise SiteError(f"site password must be {SITE_PASSWORD_MIN}..{SITE_PASSWORD_MAX} characters")
    # The hash lands on one env-file line; control characters have no business
    # in a password and would corrupt that file.
    if any(ord(c) < 32 or ord(c) == 127 for c in password):
        raise SiteError("site password must not contain control characters")
    return password


def _replace_hash_line(text: str, new_hash: str) -> str:
    lines = text.split("\n")
    hits = [i for i, line in enumerate(lines) if line.startswith(HASH_KEY + "=")]
    if len(hits) != 1:
        raise SiteError("dsh.env must contain exactly one password hash line")
    lines[hits[0]] = f"{HASH_KEY}={new_hash}"
    return "\n".join(lines)


def _in_home(path: Path) -> tuple[Path, Path]:
    """(dsh-home, relative path) for a file inside a site's DSH_HOME.

    DSH_HOME is writable by code running in the site container, so the host
    never resolves a path there by name: safe_fs walks it with O_NOFOLLOW."""
    path = Path(path)
    for parent in path.parents:
        if parent.name == "dsh-home":
            return parent, path.relative_to(parent)
    raise SiteError("path is not inside a site DSH_HOME")


def _write_private(path: Path, data: bytes) -> None:
    home, rel = _in_home(path)
    try:
        safe_fs.write_atomic(home, rel, data, 0o600)
    except safe_fs.UnsafePath:
        raise SiteError(f"refusing to write {rel}: not a regular file inside DSH_HOME") from None


def _read_private(path: Path) -> bytes | None:
    """Bytes of a DSH_HOME file, None when absent; a symlink is an error."""
    home, rel = _in_home(path)
    try:
        return safe_fs.read_bytes(home, rel)
    except FileNotFoundError:
        return None
    except safe_fs.UnsafePath:
        raise SiteError(f"refusing to read {rel}: not a regular file inside DSH_HOME") from None


def set_site_password(name: str, password: str, *, self_test=None) -> None:
    """Replace ONLY the site's login password, reversibly.

    Narrower than the general replacement that _reject_unsafe_replacement still
    refuses: the image (pinned to the running container's image id), resources,
    port and DSH_HOME data are untouched; only the hash line in dsh.env changes.
    The old container is stopped and kept, not deleted, until the new one
    passes an authenticated self-test with the new password; any failure puts
    back the old env, the old sessions and the old container. Existing login
    sessions are dropped so the old password's sessions cannot outlive it.
    The password is never stored or logged.
    """
    password = _validate_site_password(password)
    _recreate_site(
        name,
        lambda text: _replace_hash_line(text, web_auth_password_hash(password)),
        drop_sessions=True,
        probe=lambda port, host, env: (self_test or _dsh_self_test)(
            port, password, base_url=f"https://{host}"),
        what="password change", restored="site restored to the old password")


def isolate_site(name: str, *, self_test=None) -> None:
    """Move an existing site onto the isolated site network, reversibly.

    The site's AI may then run any command (DSH_PERMISSION_MODE is set by
    docker_run, which first proves the firewall is in place). The env gets
    the network gateway as its only trusted relay peer and its own per-site
    entry key (replacing a shared platform secret, which code in the site
    could otherwise read and use against every other site). Password, data,
    image, port and resources are unchanged; login sessions are kept.
    """
    if not SITE_NETWORK:
        raise SiteError("SWARM_SITE_NETWORK is not configured")
    master = os.environ.get("SWARM_ENTRY_SECRET", "").strip()
    if not master:
        raise SiteError("SWARM_ENTRY_SECRET is required to verify the moved site")

    def transform(text):
        host = _env_line(text, "DSH_TRUSTED_HOST")
        if not host:
            raise SiteError("dsh.env has no DSH_TRUSTED_HOST")
        text = _set_env_line(text, "RELAY_TRUSTED_PEERS", SITE_GATEWAY)
        return _set_env_line(text, "WEB_AUTH_ENTRY_SECRET", site_entry_secret(master, host))

    def probe(port, host, env):
        key = _env_line(env, "WEB_AUTH_ENTRY_SECRET")
        (self_test or _dsh_self_test)(port, None, base_url=f"https://{host}", entry_key=key)

    _recreate_site(name, transform, drop_sessions=False, probe=probe,
                   what="isolation move", restored="site restored on its old network")


def _env_line(text: str, key: str) -> str:
    hits = [line.split("=", 1)[1] for line in text.split("\n") if line.startswith(key + "=")]
    if len(hits) > 1:
        raise SiteError(f"dsh.env has more than one {key} line")
    return hits[0].strip() if hits else ""


def _set_env_line(text: str, key: str, value: str) -> str:
    if any(c in value for c in "\r\n\0"):
        raise SiteError("env value must be a single line")
    lines = text.split("\n")
    hits = [i for i, line in enumerate(lines) if line.startswith(key + "=")]
    if len(hits) > 1:
        raise SiteError(f"dsh.env has more than one {key} line")
    if hits:
        lines[hits[0]] = f"{key}={value}"
    else:
        at = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
        lines.insert(at, f"{key}={value}")
    return "\n".join(lines)


def _recreate_site(name, transform_env, *, drop_sessions, probe, what, restored) -> None:
    """Recreate a site's container with a changed env, rolling back on failure.

    The image is pinned to the running container's image id; resources, port
    and data are untouched. The old container is stopped and kept, not
    deleted, until the new one passes an authenticated self-test; any failure
    puts back the old env, the old sessions and the old container.
    """
    import secrets

    with _create_lock():
        name = _active_dsh_name(name)
        state = load_state(name)
        port, cpus, memory = state.get("port"), state.get("cpus"), state.get("memory")
        host = state.get("host")
        if not isinstance(port, int) or not cpus or not memory or not host:
            raise SiteError("site state incomplete; refusing to recreate")
        current = container_name(name)
        image = run(["docker", "inspect", "--format", "{{.Image}}", current])
        if not image.startswith("sha256:"):
            raise SiteError("cannot pin the running site image")
        # Move to the current image only when it seals the exact same web
        # profile (e.g. a boot-check fix); anything else is an upgrade, which
        # needs an explicit migration and is refused here.
        target = run(["docker", "image", "inspect", "--format", "{{.Id}}", DSH_IMAGE])
        if target != image:
            if not target.startswith("sha256:") or _profile_seal(target) != _profile_seal(image):
                raise SiteError("site image differs from the current image's sealed profile; "
                                "explicit migration required")
            image = target
        home = site_root(name) / "dsh-home"
        env_file = home / "dsh.env"
        old_env = _read_private(env_file)
        if old_env is None:
            raise SiteError("dsh.env missing; refusing to recreate")
        new_text = transform_env(old_env.decode("utf-8"))
        new_env = new_text.encode("utf-8")
        sessions = home / "plugins" / "web-auth" / "sessions.json"
        old_sessions = _read_private(sessions)
        previous = f"{current}-prev-{secrets.token_hex(4)}"

        run(["docker", "stop", current])
        try:
            run(["docker", "rename", current, previous])
        except Exception:
            run(["docker", "start", current])
            raise
        try:
            _write_private(env_file, new_env)
            if drop_sessions and old_sessions is not None:
                safe_fs.unlink(*_in_home(sessions))
            docker_run(name, port, env_file, cpus, memory, image=image)
            probe(port, host, new_text)
        except Exception as exc:
            errors = []
            for step in (
                lambda: _docker_rm_id(current),
                lambda: _write_private(env_file, old_env),
                lambda: old_sessions is not None and _write_private(sessions, old_sessions),
                lambda: run(["docker", "rename", previous, current]),
                lambda: run(["docker", "start", current]),
            ):
                try:
                    step()
                except Exception as step_exc:  # keep going: restore as much as possible
                    errors.append(type(step_exc).__name__)
            if errors:
                raise SiteError(f"{what} failed and restore incomplete "
                                f"({', '.join(errors)}); old container kept as {previous}") from exc
            raise SiteError(f"{what} failed; {restored}") from exc
        _docker_rm_id(previous)
        if state.get("image") != image:
            state["image"] = image
            save_state(name, state)


def _profile_seal(image: str) -> str:
    """Digest of the sealed web-profile manifest inside an image."""
    out = run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "sha256sum",
               image, "/opt/dsh-web-profile/.dsh-profile-manifest.json"])
    digest = out.split()[0] if out.split() else ""
    if len(digest) != 64:
        raise SiteError("cannot read the image's sealed profile")
    return digest



# ---------------------------------------------------------------------------
# Starter model: the platform lends a site a working model until the customer
# connects their own. The site gets ONLY its own relay token (never the
# platform gateway key), stored in DSH's own credential store; the profile
# patch declares a route to the relay and makes it the default for new agents.
# DSH reloads both files live, so no restart is needed.

STARTER_ROUTE = "platform-starter"
STARTER_KEY_REF = "SITE_STARTER_MODEL_KEY"
STARTER_MARKER = "# >>> swarm starter model (managed by the platform; do not edit)"
STARTER_END = "# <<< swarm starter model"


def _dsh_file_lock(path: Path):
    """DSH's cross-process writer lock: `<file>.lock` created exclusively."""
    import time

    home, rel = _in_home(path)
    lock = rel.with_name(rel.name + ".lock")

    @contextlib.contextmanager
    def held():
        deadline = time.monotonic() + 30
        while True:
            try:
                safe_fs.create_new(home, lock, f"{os.getpid()}\n", 0o600)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise SiteError(f"timed out waiting for {lock.name}")
                time.sleep(0.1)
            except safe_fs.UnsafePath:
                raise SiteError("DSH_HOME path crosses a symlink") from None
        try:
            yield
        finally:
            try:
                safe_fs.unlink(home, lock)
            except OSError:
                pass

    return held()


def _starter_models_ok(base_url: str, models: list[str]) -> None:
    import re

    if not re.fullmatch(r"https?://[A-Za-z0-9.:\-]+(/[A-Za-z0-9._\-/]*)?", base_url or ""):
        raise SiteError("invalid starter model base url")
    if not models or any(not re.fullmatch(r"[A-Za-z0-9._\-]{1,64}", m) for m in models):
        raise SiteError("invalid starter model id")


def starter_patch_block(base_url: str, models: list[str]) -> str:
    """The platform-managed route. Only this block is ever rewritten."""
    _starter_models_ok(base_url, models)
    lines = [
        STARTER_MARKER,
        "- id: llm-pi-ai",
        "  name: '@deepseek-ai/dsh-llm-pi-ai'",
        "  config:",
        "    providers:",
        f"      {STARTER_ROUTE}:",
        "        displayName: 平台起步模型",
        f"        apiKeyEnv: {STARTER_KEY_REF}",
        "        api: openai-completions",
        f"        baseURL: {base_url}",
        "        models:",
    ]
    for model in models:
        lines += [f"          - id: {model}", f"            name: {model}",
                  "            contextWindow: 128000", "            maxTokens: 16384"]
    lines.append(STARTER_END)
    return "\n".join(lines) + "\n"


def starter_default_entry(model: str) -> str:
    """Initial default model. Owned by the customer afterwards: DSH rewrites
    this entry when they pick another model, and the platform never resets it."""
    return ("- id: agent-default-model\n"
            "  name: '@deepseek-ai/dsh-agent-default-model'\n"
            "  config:\n"
            f"    provider: {STARTER_ROUTE}\n"
            f"    model: {model}\n")


def _has_default_entry(text: str) -> bool:
    import re

    return re.search(r"^- id: ['\"]?agent-default-model['\"]?\s*$", text, re.M) is not None


def _store_credential_ref(path: Path, ref: str, value: str) -> None:
    import yaml

    with _dsh_file_lock(path):
        raw = _read_private(path)
        document = yaml.safe_load(raw.decode("utf-8")) if raw is not None else None
        document = document or {}
        if not isinstance(document, dict) or document.get("version", 1) != 1:
            raise SiteError("unsupported DSH credential store layout")
        document["version"] = 1
        refs = document.get("refs") or {}
        if not isinstance(refs, dict):
            raise SiteError("unsupported DSH credential store layout")
        refs[ref] = value
        document["refs"] = refs
        _write_private(path, yaml.safe_dump(document, sort_keys=False).encode("utf-8"))


def enable_starter_model(name: str, token: str, *, base_url: str, models: list[str]) -> None:
    """Point the site at the platform starter model. Idempotent; rotates the token."""
    import re

    name = _active_dsh_name(name)
    if not isinstance(token, str) or not re.fullmatch(r"sms_[A-Za-z0-9_\-]{43}", token):
        raise SiteError("invalid starter model token")
    block = starter_patch_block(base_url, models)
    home = site_root(name) / "dsh-home"
    patch = home / "profiles" / "web" / "cordis.patch.yml"
    if safe_fs.kind(home, "profiles/web/cordis.patch.yml") != "file":
        raise SiteError("site profile patch missing; is the site initialized?")
    # Key first: a route without its key would fail every request, while a key
    # without a route is inert.
    _store_credential_ref(home / ".credentials.yaml", STARTER_KEY_REF, token)
    with _dsh_file_lock(patch):
        text = (_read_private(patch) or b"").decode("utf-8")
        start, end = text.find(STARTER_MARKER), text.find(STARTER_END)
        if start >= 0 and end > start:
            inside = text[start:end]
            # Earlier layout kept the default inside the block; DSH may have
            # rewritten it to the customer's choice, so carry it out verbatim.
            carried = ""
            at = inside.find("- id: agent-default-model")
            if at >= 0:
                carried = inside[at:].rstrip("\n") + "\n"
            # Replace in place so everything after the block keeps its position.
            text = (text[:start] + block + carried
                    + text[end + len(STARTER_END):].lstrip("\n"))
        elif start >= 0 or end >= 0:
            raise SiteError("starter model block in the profile patch is damaged")
        else:
            text = text.rstrip("\n") + "\n\n" + block
        if not _has_default_entry(text):
            text += starter_default_entry(models[0])
        safe_fs.write_atomic(home, "profiles/web/cordis.patch.yml", text, 0o644, keep_mode=True)


def prepare_workspace(name: str, template_id: str, *, starter_model: str | None = None,
                      language: str = "繁體中文") -> dict:
    """Write the platform guide into $DSH_HOME and place the tenant's workspace
    template (or none). Safe to re-run: the guide is refreshed, template files
    are only ever created once."""
    import site_templates

    name = _active_dsh_name(name)
    values = site_templates.guide_values(language=language, starter_model=starter_model)
    site_templates.write_platform_guide(site_root(name) / "dsh-home", values)
    return site_templates.apply_template(_workspace_dir(name), template_id, values)
