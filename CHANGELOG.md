# Changelog

## 0.1.0 — 2026-10-02

First public release.

- P5: README in English, Traditional Chinese and Simplified Chinese with an architecture diagram, a captioned demo video, a GIF and screenshots (`docs/media/`). `demo/` holds the scripted model, the recording script and the compose script behind them.
- Fix, found while recording: a customer's first visit to their site showed "could not create the default workspace", because DSH looks up the Documents directory with `xdg-user-dir`, which the site image does not have. The site profile now sets the Documents directory, and the default workspace in it points at the office workspace (`/home/dsh/workspace`). The clean install checks both.
- P4, an adversarial security review (six areas, each finding reproduced before it was fixed), found and fixed:
  - **high**: moving a site onto the isolated network (`isolate-site`) took the site's host from its own `dsh.env`, which code in the site can rewrite; a site could be handed another site's entry key. The host now comes from the platform's `site.yaml`, and the identity lines are rewritten from it;
  - the platform's login limiter saw every visitor as nginx (one bucket for everybody), and the site's own login limiter saw everybody as the in-site relay. Both now key on the visitor address set by nginx (`X-Swarm-Client` on the platform, `X-Real-IP` passed on by the relay in sites); the admin login is now rate-limited too;
  - the support assistant spent the platform model key without checking the tenant's quota; it now reserves from and settles into the same monthly pool as the site relay;
  - a site that hung up mid-stream left its quota hold behind, and four of them locked the tenant out; holds are now always settled, and stale holds expire;
  - the relay passed gateway-side tools (`mcp`, `web_search`, ...) and the `user`/`store` fields through; only function tools are accepted now. Image parts are counted in the admission estimate;
  - one site's broken files (an invalid date, deep nesting) stopped scheduled runs for every tenant, a whitespace-filled file made a regex take seconds, and long task names could hang task delivery; each site is now isolated and parsing is linear;
  - deleting a tenant failed once its support room had messages, after the site was already gone;
  - cookies planted by a sibling site on the parent domain locked users out of the platform; foreign cookies are now ignored;
  - invite activation could probe which usernames exist; an invite is revoked after 5 taken-name attempts;
  - invite tokens and entry tickets reached the nginx access log; the shipped configs log without query strings;
  - the template's `git init` could follow a `.git` symlink a site swapped in; the marker is written with the same no-follow file code as everything else;
  - a tenant host could equal the platform's own host;
  - the root nginx watcher would copy a hard-linked staged file (where `fs.protected_hardlinks` is off); files with more than one link are refused.
- P3, a clean install on a fresh host by following `deploy/README.md`, found and fixed:
  - the platform crashed on hosts without system time zone data (`tzdata` is now a dependency);
  - the first site build failed: the shared nginx map that site configs use was never installed (`install.sh` installs it now);
  - the admin console and the relay could start before the platform had created the database, and kept restarting (they now wait for it);
  - invite tokens were written to the journal by the HTTP access log (access logs are off).
- `docs/`: architecture, security model, HTTP API, ADR-001 (one agent per company), contract format, shared-session proposal, issue acceptance guide.
- Fix: scheduled office runs used a hard-coded container prefix and missed sites built with `SWARM_CONTAINER_PREFIX`; they now use the site engine's container name.
- `deploy/`: install on one host with systemd. Settings in `/etc/swarm/platform.env`, one file per secret in `/etc/swarm/secrets/`, a dedicated `swarm` user, the site firewall and the nginx watcher. The watcher now keeps ownership records outside the staging directory, refuses links and oversized files; tests run both against real nginx and iptables in throwaway containers.
- `platform/`: the platform site (customer invites and login, copilot, support rooms, single-use entry into sites), the `/admin` console and the site model relay, moved from the private deployment. Deployment names and domains are gone from code and tests; the old opencode hub is not included.
- `site/`: the DSH site engine, moved from the private deployment. The deployment's own values (domain, paths, network, container names) became settings; Cloudflare DNS is now optional; the old opencode engine is gone.
- `site/Dockerfile` builds from the repository root; dsh-share-room now comes from npm (0.1.1).
- `plugins/swarm-brand`: Swarm branding and Traditional Chinese for the DSH web client (was `@swarm/dsh-brand`).
- `site/dsh-profile/vendor/dsh-web-auth`: a documented fork of `@summersec/dsh-web-auth` 0.2.0 (see FORK.md).
- `office-template/swarm`: the office template every new site starts from.
- Repository skeleton: README, license, security policy, CI with a leak check (gitleaks plus a hashed deny-list of deployment-specific words).
