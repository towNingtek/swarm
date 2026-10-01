# Architecture

Swarm runs on one Linux host. A central **platform** site invites customers, signs them in and sends them into their own **site**. Each site is one DeepSeek Harness (DSH) container with its own login, data and workspace. Operators manage everything from an **admin console** under `/admin` on the platform's origin.

This document describes what the code in this repository does. Settings are described at the top of each entry point and in [`deploy/README.md`](../deploy/README.md).

## Components

| Component | Code | Runs as | Listens on | Job |
|---|---|---|---|---|
| Platform | `platform/platform_service.py` | `swarm` user, systemd `swarm-platform` | `127.0.0.1:8210` | Invitations, customer login, onboarding page, support rooms, single-use entry into a site. Also runs the site build loop and (optionally) the site scheduler |
| Admin console | `platform/admin_service.py` | `swarm`, `swarm-platform-admin` | `127.0.0.1:8211` | Operator login, customers, invites, quotas, task library, site build requests, deletion |
| Site model relay | `platform/site_model_relay.py` | `swarm`, `swarm-site-relay` | Docker bridge, default `172.17.0.1:8212` | The only model endpoint a site can reach. Checks the site's own key and budget, then forwards with the platform key |
| Site engine | `site/dsh_sitectl.py`, `site/common.py` | Called in-process by the platform | none | Create, isolate, change password, delete sites |
| Site container | `site/Dockerfile` | uid:gid of the platform (`SWARM_CONTAINER_USER`) | `127.0.0.1:<port>` on the host (range 18000-18999) | DSH web app, `dsh-web-auth` login (vendored fork), branding, conversation sharing, in-site relay (`site/templates/relay.mjs`) |
| nginx | `deploy/nginx/swarm-platform.conf.example`, `site/templates/nginx-site.conf.tmpl` | root | 80 | Routes `platform.example.com` to 8210 (and `/admin` to 8211), and each `<name>.example.com` to its site's port |
| nginx watcher | `deploy/bin/swarm-nginx-apply`, `swarm-nginx-apply.path` | root | none | Applies site configs the platform drops in a staging directory, so the platform never needs `sudo` |
| Site firewall | `deploy/bin/swarm-site-firewall`, `swarm-site-firewall.service` | root, before Docker starts | none | iptables rules for the isolated site network |

All three Python services share one SQLite database (`PLATFORM_DB`, default `/var/lib/swarm/platform/platform.sqlite`).

TLS is not terminated by anything in this repository. The nginx examples listen on port 80; put TLS in front (a tunnel, a load balancer, or your own nginx `listen 443`). The platform and admin refuse to start unless their configured origin is `https://`.

## Diagram

```
                       browser
                          |  HTTPS (TLS in front of nginx)
                          v
   +---------------------------------------------------+
   | nginx :80                                         |
   |  platform.example.com   /       -> 127.0.0.1:8210 |
   |  platform.example.com   /admin  -> 127.0.0.1:8211 |
   |  acme.example.com       /       -> 127.0.0.1:180xx|
   +---------------------------------------------------+
        |                  |                   |
        v                  v                   v
   +----------+      +-----------+     +--------------------------+
   | platform |      |   admin   |     | site container (one per  |
   |  :8210   |      |   :8211   |     | customer), network       |
   +----------+      +-----------+     | swarm-sites 172.29.10.0/24|
        |  \             |             |  in-site relay :3080     |
        |   \  SQLite    |             |   -> DSH web + web-auth  |
        |    +-----------+             +--------------------------+
        |    | platform.sqlite                  |
        |    +-----------+                      | Bearer sms_... (site key)
        |                |                      v
        |          +-------------------------------+
        |          | site model relay 172.17.0.1:8212 |
        |          +-------------------------------+
        |                      | Bearer platform key
        |  docker / staging    v
        v                 OpenAI-compatible gateway
   docker, nginx-staging  (PLATFORM_MODEL_BASE_URL)
```

The firewall allows a site container to open connections to two places only: the internet, and the relay's `ip:port` on the host.

## How a request flows

Platform request (`https://platform.example.com/customer/...`):

1. nginx forwards to `127.0.0.1:8210` with the original `Host`.
2. The customer boundary (`platform/support_app.py`) runs the edge policy (`platform/support_http_policy.py`): exactly one `Host` equal to the configured host, `Origin` checks, `Sec-Fetch-*` checks. A failure is a 403 before any route runs.
3. It reads the `__Host-customer_session` cookie. Login and activation are rate limited per client address.
4. The route resolves the session through `SupportCore.authenticate_session`, which also checks that the session belongs to this host and that the tenant is enabled.
5. Responses get `Cache-Control: no-store`, `Referrer-Policy: same-origin`, `X-Content-Type-Options: nosniff` and a restrictive CSP.

Admin request (`https://platform.example.com/admin/...`): same edge policy against `ADMIN_ORIGIN`, then the signed operator cookie (`platform_admin_session`, `Path=/admin`). Anything without it is redirected to `/admin/login`.

Site request (`https://acme.example.com/...`): nginx forwards to the site's loopback port. Inside the container the in-site relay (`relay.mjs`, policy in `relay-policy.mjs`) checks that the peer is a trusted proxy address and that there is exactly one `Host` equal to the site's public host, drops client-supplied forwarding headers, and passes the request to DSH on `127.0.0.1:3081`. `dsh-web-auth` requires a login for every path except `/auth/*` and the `/share` prefix (guest pages of the conversation-sharing plugin).

## Customer journey

### 1. Invite

An operator creates a tenant in the admin console (`POST /admin/tenants` with a `host`, for example `acme.example.com`) and issues an invite (`POST /admin/tenants/{id}/invites`, TTL 60 s to 24 h). The command line `platform/platform_admin_cli.py add-tenant` / `invite` does the same against the database.

- The invite token is 32 random bytes, URL-safe. Only its SHA-256 digest is stored. It is shown once.
- The link is `https://platform.example.com/?token=<invite>`. It is bound to the platform host, not the site host, because the site does not exist yet.
- Issuing is not idempotent: retrying creates a second valid invite.

### 2. Activate and log in

The customer opens the link and chooses a username and password (`POST /customer/activate` with `invite`, `username`, `password`). The core re-checks the invite inside a write transaction (not expired, not redeemed, not revoked, tenant enabled), creates the account, marks the invite redeemed and returns a session.

- Passwords: 8 to 1024 bytes, hashed with scrypt (N=16384, r=8, p=1).
- Sessions: 32 random bytes, stored as a digest, bound to the platform host, 24 h by default, sent only as a `Secure; HttpOnly; SameSite=Strict` host-only cookie.
- Login (`POST /customer/login`) does the same scrypt work for unknown users, so a missing account does not answer faster.

### 3. Build the site

See [How a site is built](#how-a-site-is-built). The customer's onboarding page (`/customer/onboarding`) shows the build status from `GET /customer/onboarding/status`.

### 4. Enter the site

The customer never needs the site password. When they click "enter":

1. The browser sends `POST /customer/site-entry` with an empty JSON body.
2. `platform/support_site_entry.py` checks that the session is valid, the tenant is enabled, and this platform's own job record says it provisioned the site (`succeeded_provisioned`) under the tenant's current host. Otherwise 409.
3. It mints a ticket `<expiry_ms>.<nonce>.<signature>`:
   - `expiry_ms` is now + 60 s;
   - `nonce` is 24 random bytes, URL-safe;
   - `signature` is base64url HMAC-SHA256 over `"<expiry_ms>.<nonce>"`, keyed with the **per-site key**.
4. It returns `https://acme.example.com/auth/enter?ticket=...` and the browser navigates there.
5. `dsh-web-auth` (`site/dsh-profile/vendor/dsh-web-auth/src/index.js`, `verifyEntryTicket`) checks the format, that the ticket has not expired and does not expire more than 300 s in the future, compares the signature in constant time, and refuses a nonce it has already seen. A valid ticket creates the same web-auth session a password login would (`HttpOnly; SameSite=Lax`, `Secure` over HTTPS).
6. It redirects (303) to `/auth/handoff`, which turns the web-auth session into DSH's own session cookie through a same-origin request. The DSH launch token never appears in a URL; the in-site relay also removes it from logs and from any `Location` header.

The per-site key is derived the same way in two places, `site_entry_secret` in `site/dsh_sitectl.py` and in `platform/support_site_entry.py`:

```
per_site_key = hex( HMAC-SHA256( SWARM_ENTRY_SECRET,
                                 "dsh-site-entry\0" + site_host ) )
```

The master `SWARM_ENTRY_SECRET` (at least 32 characters) stays on the host. Each site's `dsh.env` gets only its own derived key as `WEB_AUTH_ENTRY_SECRET`. Code running inside a site can read that key, but it cannot sign tickets for any other site.

The customer may also set a site password from the platform (`POST /customer/site-password`, when `PLATFORM_SITE_PASSWORD=1`). That recreates the container with a new password hash; the platform keeps no copy.

## How a site is built

Building a site is a durable job, one per tenant (`platform/support_site_jobs.py`).

```
queued -> running -> succeeded_provisioned
             |
             +-- failure or lease expiry --> reconciliation_required
                                                  |
                       trusted reconcile -------> queued | failed
```

`succeeded_simulated` also exists for the test fixture; it never opens a site.

1. **Queue.** An operator calls `POST /admin/tenants/{id}/site-jobs` with `{"client_request_id": "..."}`, or runs `platform_admin_cli.py provision`. This writes intent only. Replays with the same request ID return the existing job; a tenant has at most one job and one site identity.
2. **Claim.** If `PLATFORM_PROVISION=1`, the platform service runs a loop every 15 s that takes one queued job (`SiteProvisioner.run_once(limit=1)` in `platform/support_site_executor.py`). The executor holds an in-process capability object; no HTTP route can claim, complete or reconcile a job. The lease is 900 s and is fenced by an epoch and a random lease token.
3. **Create.** The site name is derived from the tenant's host and must be `<label>.SWARM_DOMAIN` (one DNS label, not a reserved name; see `site/names.py`). `dsh_sitectl.cmd_create` then:
   - takes a global create lock and creates `/var/lib/swarm/sites/<name>/` exclusively (an existing directory is never reused);
   - initialises `dsh-home/`, writes `dsh.env` (mode 0600: password hash, public host, trusted relay peer, per-site entry key) and `site.yaml`;
   - when `SWARM_SITE_NETWORK` is set, proves network isolation with a probe container, then starts the site on that network (see [security.md](security.md));
   - creates a DNS record if Cloudflare settings are present (otherwise a wildcard record is assumed);
   - stages the nginx config and waits for the watcher to apply it;
   - runs an authenticated self-test through the public HTTPS address: login and native handoff must reach DSH's HTML;
   - on any failure, removes what it created (proxy, DNS, the container it started, the directory) and reports what it could not remove.
4. **Finish.** Best-effort extras: the starter model key, the office template in the workspace (`office-template/swarm` via `site/site_templates.py`), and default tasks. A failure here is logged; the site still works.
5. **Record.** The job becomes `succeeded_provisioned` with site name, port and DNS record ID. Any failure moves it to `reconciliation_required`. Nothing is retried automatically, because external resources may be half-created.

Deleting a tenant (`POST /admin/tenants/{id}/delete` with `expected_host`, only when `ADMIN_ALLOW_DELETE=1`; `platform/support_site_teardown.py`) disables the tenant first, removes the site only under the name recorded in its job, and deletes database rows only after the container, proxy and DNS record are gone.

## How models reach sites

A new site needs a model before the customer connects their own. The platform lends a **starter model** through the relay.

1. During the build the platform issues a site key: `sms_` plus 32 random bytes (`SiteModelAccess.issue`, `platform/support_site_model.py`). The database stores only its SHA-256 digest. Issuing again rotates it.
2. `dsh_sitectl.enable_starter_model` writes the key into the site's DSH credential store (`dsh-home/.credentials.yaml`) and points the site's profile patch at `SITE_MODEL_RELAY_URL` (for example `http://172.17.0.1:8212/v1`).
3. The site calls the relay like any OpenAI-compatible API. The relay (`platform/site_model_relay.py`):
   - accepts only peers in `172.16.0.0/12` or `127.0.0.0/8`;
   - serves only `GET /v1/models` and `POST /v1/chat/completions`, everything else is 404;
   - authenticates the site key, rejects disabled tenants, and applies the tenant's policy (disabled, unlimited, or a monthly token cap) with at most 4 requests in flight per tenant;
   - keeps only an allowlist of request fields (so routing fields like `api_base` or `api_key` are dropped), forces `n=1`, caps output at 16384 tokens (default 4096), and asks for usage on streams;
   - reserves an estimate before forwarding and settles to the reported usage, or keeps the estimate if usage is unknown;
   - forwards to `PLATFORM_MODEL_BASE_URL` with `PLATFORM_MODEL_KEY` and returns a generic message instead of upstream error bodies.
4. The customer can add their own model inside the site (Settings → Models). That key lives only in the site.

The platform key is loaded by the relay (and by the platform/admin processes when the support assistant uses the gateway). It is never written into a site.

## Scheduled work inside sites

With `PLATFORM_SITE_SCHEDULER=1` the platform reads each site's office files (registry and project files in the site workspace) and, when a schedule's cron matches, runs it with `docker exec` in that site's container using DSH's headless profile and the site's own default model (`platform/support_site_scheduler.py`). No platform credential is passed in. One run per site at a time, at most two globally. The platform also writes `workspace/.swarm/platform-status.md` into each site (`platform/support_site_status.py`) so the site's AI can see its model, allowance and schedules; it contains no keys.

## Data locations

| Path | Owner | Contents |
|---|---|---|
| `/opt/swarm` | root | This repository and its virtualenv |
| `/etc/swarm/platform.env` | root:swarm 0640 | Settings (no secrets) |
| `/etc/swarm/secrets/` | root:swarm 0750, files 0640 | `entry-secret`, `admin-password`, `model-key`, optional `cloudflare-token`, `discord-token` |
| `/var/lib/swarm/platform/platform.sqlite` | swarm | Tenants, accounts (scrypt hashes), sessions and invites (digests), rooms and messages, jobs, quota ledger, site key digests |
| `/var/lib/swarm/sites/<name>/site.yaml` | swarm | Site state: host, port, image, resources, DNS record ID |
| `/var/lib/swarm/sites/<name>/dsh-home/` | swarm | DSH home: `dsh.env` (0600), profile, credentials, sessions |
| `/var/lib/swarm/sites/<name>/workspace/` | swarm | The customer's files; mounted as `HOME` in the container |
| `/var/lib/swarm/nginx-staging/` | root:swarm 0770 | Configs handed from the platform to the watcher |
| `/var/lib/swarm-nginx-owned/` | root 0700 | Which nginx configs the watcher created |
| `/etc/nginx/conf.d/swarm-site-<name>.conf` | root | Per-site nginx config |

Deleting a site keeps its data directory unless the purge option is used.
