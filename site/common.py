"""Shared site plumbing: settings, site state, ports, DNS and the reverse proxy.

Everything that depends on the deployment comes from the environment:

  SWARM_SITES_ROOT      where site data lives (default /var/lib/swarm/sites)
  SWARM_DOMAIN          parent domain for sites, required (e.g. example.com)
  SWARM_PORT_MIN/MAX    host port range for site containers (default 18000-18999)
  SWARM_CONTAINER_USER  uid:gid the site containers run as (default: this process)
  SWARM_CONTAINER_PREFIX
                        container name prefix (default "site-")
  SWARM_NGINX_CONF_DIR  nginx conf.d directory (default /etc/nginx/conf.d)
  SWARM_NGINX_VIA_STAGING=1
                        hand configs to a root-owned watcher (nginx_staging)
                        instead of calling sudo directly

DNS is optional. Without CLOUDFLARE_API_TOKEN the platform assumes a wildcard
record (*.SWARM_DOMAIN) already points at this host and creates nothing. With
it, each site gets its own record:

  CLOUDFLARE_API_TOKEN, CLOUDFLARE_ZONE_ID
  SWARM_TUNNEL_ID       CNAME to a Cloudflare tunnel, or
  SWARM_MACHINE_IP      an A record to this host
"""
from __future__ import annotations

import os
import secrets
import string
import subprocess
from pathlib import Path
from string import Template

import yaml

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
CF_API = "https://api.cloudflare.com/client/v4"
TIMEOUT = 30.0


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


SITES_ROOT = Path(_env("SWARM_SITES_ROOT", "/var/lib/swarm/sites"))
DOMAIN = _env("SWARM_DOMAIN")
PORT_MIN = int(_env("SWARM_PORT_MIN", "18000"))
PORT_MAX = int(_env("SWARM_PORT_MAX", "18999"))
CONTAINER_USER = _env("SWARM_CONTAINER_USER", f"{os.getuid()}:{os.getgid()}")
NGINX_CONF_DIR = Path(_env("SWARM_NGINX_CONF_DIR", "/etc/nginx/conf.d"))
TUNNEL_ID = _env("SWARM_TUNNEL_ID")
MACHINE_IP = _env("SWARM_MACHINE_IP")
CONTAINER_PREFIX = _env("SWARM_CONTAINER_PREFIX", "site-")


class SiteError(RuntimeError):
    pass


def domain() -> str:
    """The parent domain; refuses to guess one."""
    if not DOMAIN:
        raise SiteError("SWARM_DOMAIN is not set (for example: SWARM_DOMAIN=example.com)")
    return DOMAIN


def site_host(name: str) -> str:
    return f"{name}.{domain()}"


# ---------------------------------------------------------------- site state

def site_root(name: str) -> Path:
    return SITES_ROOT / name


def state_path(name: str) -> Path:
    return site_root(name) / "site.yaml"


def load_state(name: str) -> dict:
    path = state_path(name)
    if not path.exists():
        raise SiteError(f"site {name!r} does not exist ({path} not found)")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def container_name(name: str) -> str:
    return f"{CONTAINER_PREFIX}{name}"


def allocate_port() -> int:
    """First port in range not recorded by any site.yaml."""
    used: set[int] = set()
    if SITES_ROOT.exists():
        for site_yaml in SITES_ROOT.glob("*/site.yaml"):
            data = yaml.safe_load(site_yaml.read_text(encoding="utf-8")) or {}
            if isinstance(data.get("port"), int):
                used.add(data["port"])
    for port in range(PORT_MIN, PORT_MAX + 1):
        if port not in used:
            return port
    raise SiteError(f"no free port in {PORT_MIN}-{PORT_MAX}")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SiteError(f"command failed: {' '.join(command[:3])} ...\n{result.stderr.strip()}")
    return result.stdout.strip()


# ---------------------------------------------------------------- DNS (optional)

def dns_enabled() -> bool:
    return bool(_env("CLOUDFLARE_API_TOKEN"))


def _cloudflare() -> tuple[dict[str, str], str]:
    token = _env("CLOUDFLARE_API_TOKEN")
    zone = _env("CLOUDFLARE_ZONE_ID")
    if not token or not zone:
        raise SiteError("Cloudflare DNS needs both CLOUDFLARE_API_TOKEN and CLOUDFLARE_ZONE_ID")
    return {"Authorization": f"Bearer {token}"}, zone


def dns_payload(name: str) -> dict:
    """CNAME to a tunnel when one is configured, otherwise an A record."""
    if TUNNEL_ID:
        return {"type": "CNAME", "name": site_host(name),
                "content": f"{TUNNEL_ID}.cfargotunnel.com", "proxied": True}
    if not MACHINE_IP:
        raise SiteError("Cloudflare DNS needs SWARM_TUNNEL_ID or SWARM_MACHINE_IP")
    return {"type": "A", "name": site_host(name), "content": MACHINE_IP, "proxied": True}


def dns_create(name: str) -> str | None:
    """Create the site's DNS record. Returns its id, or None when DNS is off."""
    if not dns_enabled():
        return None
    import httpx

    headers, zone = _cloudflare()
    response = httpx.post(f"{CF_API}/zones/{zone}/dns_records", headers=headers,
                          json=dns_payload(name), timeout=TIMEOUT)
    body = response.json()
    if not body.get("success"):
        raise SiteError(f"Cloudflare DNS create failed: {body.get('errors')}")
    return body["result"]["id"]


def dns_delete(record_id: str | None) -> None:
    if not record_id or not dns_enabled():
        return
    import httpx

    headers, zone = _cloudflare()
    httpx.delete(f"{CF_API}/zones/{zone}/dns_records/{record_id}", headers=headers,
                 timeout=TIMEOUT).raise_for_status()


# ---------------------------------------------------------------- reverse proxy (nginx)

def _staging_enabled() -> bool:
    return _env("SWARM_NGINX_VIA_STAGING") == "1"


def nginx_conf_path(name: str) -> Path:
    import nginx_staging

    return NGINX_CONF_DIR / f"{nginx_staging.CONF_PREFIX}{name}.conf"


def render_site_conf(name: str, port: int) -> str:
    template = Template((TEMPLATES_DIR / "nginx-site.conf.tmpl").read_text(encoding="utf-8"))
    return template.substitute(host=site_host(name), port=port)


def _sudo_write(path: Path, content: str) -> None:
    result = subprocess.run(["sudo", "-n", "tee", str(path)], input=content,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise SiteError(f"cannot write nginx config {path}\n{result.stderr.strip()}")


def _nginx_reload() -> None:
    check = subprocess.run(["sudo", "-n", "nginx", "-t"], capture_output=True, text=True)
    if check.returncode != 0:
        raise SiteError(f"nginx config test failed:\n{check.stderr.strip()}")
    run(["sudo", "-n", "systemctl", "reload", "nginx"])


def proxy_add(name: str, port: int) -> None:
    content = render_site_conf(name, port)
    if _staging_enabled():
        import nginx_staging

        try:
            nginx_staging.apply_config(name, content)
        except nginx_staging.StagingError as exc:
            raise SiteError(str(exc)) from exc
        return
    common = NGINX_CONF_DIR / "00-swarm-common.conf"
    if not common.exists():
        _sudo_write(common, (TEMPLATES_DIR / "nginx-common.conf").read_text(encoding="utf-8"))
    path = nginx_conf_path(name)
    _sudo_write(path, content)
    try:
        _nginx_reload()
    except SiteError:
        subprocess.run(["sudo", "-n", "rm", "-f", str(path)], capture_output=True, text=True)
        subprocess.run(["sudo", "-n", "systemctl", "reload", "nginx"], capture_output=True, text=True)
        raise


def proxy_remove(name: str) -> None:
    if _staging_enabled():
        import nginx_staging

        try:
            nginx_staging.remove_config(name)
        except nginx_staging.StagingError as exc:
            raise SiteError(str(exc)) from exc
        return
    path = nginx_conf_path(name)
    if not path.exists():
        return
    run(["sudo", "-n", "rm", "-f", str(path)])
    _nginx_reload()
