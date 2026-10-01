# Security model

This page lists the trust boundaries in Swarm and what the code does to hold each one. Every claim names the file that enforces it. For reporting a vulnerability, see [SECURITY.md](../SECURITY.md).

## Trust boundaries

| Boundary | On one side | On the other | Enforced by |
|---|---|---|---|
| Internet to platform | Anyone | Customer accounts and sessions | Edge policy, cookies, rate limit (`platform/support_app.py`, `platform/support_http_policy.py`) |
| Customer to operator | Customer session | Admin console | Separate cookie, path and process (`platform/admin_service.py`) |
| Customer to customer | One tenant | Another tenant | Host-bound sessions, tenant derived from the session, per-site keys |
| Site to host | Code the AI runs in a site | Host ports, other containers, private networks, cloud metadata | Container flags, isolated network, firewall (`site/dsh_sitectl.py`, `deploy/bin/swarm-site-firewall`) |
| Site to platform key | Site | Upstream model gateway key | Site model relay (`platform/site_model_relay.py`) |
| Platform to root | `swarm` user | nginx, iptables | Root watcher and root firewall unit; no `sudo` (`site/nginx_staging.py`, `deploy/bin/swarm-nginx-apply`) |

Out of scope by design: anyone who can drive the AI on a site can reach everything on that site, including its own keys.

## Site isolation

A site's AI may run any command in its container (`DSH_PERMISSION_MODE=danger-full-access` is set only on the isolated network). What contains it:

**Container** (`docker_run` in `site/dsh_sitectl.py`):

- `--cap-drop ALL`, `--security-opt no-new-privileges`, `--cpus` and `--memory` limits;
- runs as a non-root uid (`SWARM_CONTAINER_USER`);
- only two host directories are mounted: the site's `dsh-home/` and `workspace/`;
- its port is published on `127.0.0.1` only.

**Network** (`ensure_site_network`, `verify_site_isolation`):

- a dedicated bridge network (default `swarm-sites`, `172.29.10.0/24`, bridge `br-swarm-sites`) with inter-container traffic disabled (`enable_icc=false`). An existing network with other settings is refused;
- public DNS resolvers instead of the host's resolver;
- before starting a site on that network, a throwaway probe container must reach the relay and the internet and must **fail** to reach `172.17.0.1:22`, the site gateway's port 22, `169.254.169.254:80` and any extra targets in `SWARM_ISOLATION_PROBES`. If any check fails, the site is not started.

**Firewall** (`deploy/bin/swarm-site-firewall`, loaded before Docker by `swarm-site-firewall.service`):

- traffic from the site bridge to the host itself: only TCP to the relay `ip:port` (default `172.17.0.1:8212`) and replies are accepted; everything else is dropped;
- forwarded traffic from sites to `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `100.64.0.0/10`, `127.0.0.0/8`, `0.0.0.0/8`, multicast and reserved ranges is dropped;
- no new connection may be opened from outside into the site bridge;
- rules live in their own chains (`SWARM-SITES-IN`, `SWARM-SITES-FWD`) hooked from `INPUT` and `DOCKER-USER`, and `--remove` deletes them.

## The relay is the only host port a site can reach

`platform/site_model_relay.py`:

- binds to the Docker bridge address (default `172.17.0.1:8212`), not a public interface, and nginx does not proxy it;
- rejects peers outside `172.16.0.0/12` and `127.0.0.0/8`;
- answers only `GET /v1/models` and `POST /v1/chat/completions`;
- authenticates each request with the site's own `sms_` key, stored in the database only as a SHA-256 digest (`platform/support_site_model.py`); disabled tenants get 403;
- drops request fields not on an allowlist, so a site cannot redirect the upstream call (for example with `api_base`);
- caps body size (8 MiB), output tokens (16384) and concurrent requests per tenant (4), and enforces the tenant's monthly budget before forwarding;
- does not follow redirects or read proxy settings from the environment, and does not pass upstream error bodies back to the site.

The platform key (`PLATFORM_MODEL_KEY`) is attached by the relay and never written into a site.

## Single-use entry tickets

`platform/support_site_entry.py` and `site/dsh-profile/vendor/dsh-web-auth/src/index.js`:

- a ticket is minted only for the signed-in customer's own tenant, only if the tenant is enabled and this platform recorded the site as provisioned under the tenant's current host;
- it expires in 60 s; the site also rejects any ticket valid for more than 300 s;
- the signature is HMAC-SHA256 with a per-site key `HMAC(SWARM_ENTRY_SECRET, "dsh-site-entry\0" + host)`; the master secret is never written into a site (`write_dsh_env`, `isolate_site` in `site/dsh_sitectl.py`);
- the site compares signatures in constant time and remembers spent nonces until they expire, so a ticket works once;
- the ticket is unrelated to the site password, so the platform cannot be used to check a password.

The spent-nonce list is held in memory by the web-auth process. A restart of the site clears it; the 60 s expiry still applies.

## Login inside each site

Every site runs `dsh-web-auth` (vendored fork, see `site/dsh-profile/vendor/dsh-web-auth/FORK.md`), configured in `site/dsh-profile/cordis.patch.yml`:

- `authMode: always`: every path needs a login except `/auth/*` and the configured public prefix `/share` (guest pages of the sharing plugin, which checks its own guest credential). Public prefixes must be a single path segment and reserved names are refused;
- the site password is stored only as an scrypt hash in `dsh.env` (mode 0600);
- 5 login attempts per 300 s window, sessions of 720 minutes;
- return paths after login are limited to local paths: no `//`, backslash, tab, CR, LF or NUL (`sanitizeReturnPath` in `src/auth.js`);
- after login, the DSH launch token is exchanged for DSH's session cookie by a same-origin request. The in-site relay (`site/templates/relay.mjs`) redacts the token from logs and strips any redirect that contains it.

In front of DSH, the in-site relay (`site/templates/relay-policy.mjs`) accepts connections only from trusted proxy IPs (the site network gateway), requires exactly one `Host` header equal to the site's public host, and removes client-supplied `X-Forwarded-*`, `Forwarded` and `X-Real-IP` headers.

## Origin and CSRF checks

`platform/support_http_policy.py` (`EdgePolicy`) runs before any route on both the platform and the admin console:

- exactly one `Host` header, equal to the configured origin's host; forwarded headers are never used to change it;
- any `Origin` header must equal the configured origin; state-changing methods require it;
- `Sec-Fetch-Site` must be `same-origin` or `none`, except a top-level document navigation (`Sec-Fetch-Mode: navigate`, `Sec-Fetch-Dest: document`) from `cross-site` or `same-site`, for example opening an invite link. A cross-site POST is refused;
- sensitive responses carry `Cache-Control: no-store`, `Referrer-Policy: same-origin`, `X-Content-Type-Options: nosniff` and `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; base-uri 'none'`.

Request bodies (`platform/support_app.py`, `platform/support_chat_app.py`):

- only `application/json`, at most 16 KiB, duplicate keys rejected, exact field sets;
- query strings are rejected on almost every route;
- login and activation are limited to 10 requests per 60 s per client address and host, using the socket address, not forwarded headers.

Customer cookie: `__Host-customer_session`, `Secure; HttpOnly; SameSite=Strict; Path=/`. Requests carrying `swarm_admin_session`, malformed cookies or two customer cookies are rejected. Sessions are stored as digests and bound to the platform host.

## Admin console separation

`platform/admin_service.py`:

- a separate process on its own port; it mounts no `/customer/*` routes, and the platform process mounts no admin routes;
- one operator password from `ADMIN_PASSWORD` (at least 16 characters), compared in constant time;
- cookie `platform_admin_session` with `Path=/admin`, `Secure; HttpOnly; SameSite=Lax`, 8 h lifetime, HMAC-signed with a key generated at startup (a restart signs everyone out);
- every request passes the edge policy, then needs a valid cookie; otherwise it is redirected to `/admin/login`;
- the console can only queue a build. The platform process holds the worker capability that performs it;
- deletion exists only when `ADMIN_ALLOW_DELETE=1` and requires the caller to repeat the tenant's host (`expected_host`).

Admin listings select an explicit list of columns and never return password hashes, tokens or credentials (`summaries` in `platform/support_management.py`).

## No sudo in the platform

With `SWARM_NGINX_VIA_STAGING=1` (the deploy default), the platform writes `swarm-site-<name>.conf` into `/var/lib/swarm/nginx-staging` and waits for the outcome (`site/nginx_staging.py`). A timeout is treated as unknown, not success. The services run with `NoNewPrivileges=yes`, so `sudo` cannot work there anyway.

The root watcher (`deploy/bin/swarm-nginx-apply`):

- accepts only names matching `swarm-site-<label>.conf`;
- accepts only regular files up to 64 KiB, opened with `O_NOFOLLOW`;
- refuses to overwrite a config it did not create, and refuses to remove one it does not own; ownership records live in a root-only directory (`/var/lib/swarm-nginx-owned`) outside the staging area;
- runs `nginx -t` after every change and restores the previous config if the test fails;
- reads settings from `platform.env` without executing it (`deploy/bin/swarm-env-get`).

Without staging, `site/common.py` falls back to `sudo -n` for nginx. That path is for development hosts.

## Secrets

`deploy/bin/swarm-run` starts each service:

- settings come from `/etc/swarm/platform.env`; secrets are separate files in `/etc/swarm/secrets/` (root:swarm, 0640), read at start;
- each service receives only the secrets it uses:

| Service | Secrets |
|---|---|
| platform | `entry-secret`, `model-key`, optional `cloudflare-token`, `discord-token` |
| admin | `entry-secret`, `model-key`, `admin-password`, optional `cloudflare-token` |
| relay | `model-key` |

- secrets never appear in unit files or on the command line; `swarm-run --check` prints variable names only;
- `deploy/install.sh` generates `entry-secret` and `admin-password` with restrictive permissions.

Inside the database, invite tokens, session tokens and site model keys are stored only as SHA-256 digests; customer passwords as scrypt hashes. The platform keeps no copy of a site password set by a customer (`platform/support_site_password.py`).

## Known limits

- **The `docker` group is root.** The `swarm` user needs Docker to run sites, so anyone who controls the platform process controls the host. Use a host dedicated to Swarm.
- **Single host.** All customers share one kernel, one Docker daemon and one SQLite database. Container isolation is not a VM boundary.
- **No in-site sandbox.** DSH's own sandbox does not work in a container (Docker's default seccomp profile blocks Landlock). The container, the network and the firewall are the only containment.
- **Outbound internet is open** from sites, so a site can send its own data anywhere.
- **IPv4 only.** The firewall script manages `iptables`; IPv6 is not covered. Do not enable IPv6 on the site network.
- **Per-process rate limits.** The login limiter lives in memory of one process. Several workers would need a shared limiter.
- **Sites keep their own secrets readable.** A site's AI can read its own entry key and model key. They are scoped to that site.
- **Spent entry tickets** are remembered in memory; a site restart within 60 s of issuing a ticket would accept it again.
- **One operator account.** The admin console has a single shared password; there are no per-operator identities.
- **TLS is external.** Nothing in this repository terminates TLS; the `https://` origins assume you put it in front.
