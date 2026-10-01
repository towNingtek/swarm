# site/

Creates and runs customer sites. Each site is one DeepSeek Harness (DSH) container with its own login, data, workspace and model relay.

| Path | What it is |
|---|---|
| `dsh_sitectl.py` | Site engine: create, update, change password, isolate, delete |
| `common.py` | Shared plumbing: settings, site state, ports, optional Cloudflare DNS, nginx |
| `names.py` | Site name rules |
| `site_templates.py`, `safe_fs.py` | Put the platform guide and an office template into a new site, without following links the customer planted |
| `nginx_staging.py` | Hand nginx configs to a root-owned watcher instead of using sudo |
| `Dockerfile` | The site image: DSH, web login, branding, conversation sharing, in-site relay |
| `dsh-profile/` | DSH web profile baked into the image; `vendor/dsh-web-auth` is a fork, see its [FORK.md](dsh-profile/vendor/dsh-web-auth/FORK.md) |
| `templates/` | In-site relay, profile seal, platform guide, nginx templates |
| `tests/` | Unit tests; `./run_tests.sh` runs them |

## Build the image

From the repository root:

```sh
docker build -f site/Dockerfile -t swarm-site:latest .
```

## Settings

All deployment values come from the environment. See the docstrings at the top of `common.py` and `dsh_sitectl.py`. The main ones:

| Variable | Meaning |
|---|---|
| `SWARM_DOMAIN` | Parent domain, required. Sites are `<name>.SWARM_DOMAIN` |
| `SWARM_SITES_ROOT` | Site data directory (default `/var/lib/swarm/sites`) |
| `SWARM_SITE_NETWORK` | Isolated Docker network for sites |
| `SWARM_MODEL_RELAY` | `host:port` of the platform model relay, the only host port a site may reach |
| `SWARM_ENTRY_SECRET` | Master secret for single-use entry tickets (each site gets a derived key, never this) |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ZONE_ID` | Optional. Without them, point a wildcard DNS record `*.SWARM_DOMAIN` at the host yourself |
