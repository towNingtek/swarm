# Changelog

## Unreleased

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
