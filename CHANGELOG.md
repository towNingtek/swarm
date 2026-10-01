# Changelog

## Unreleased

- `site/`: the DSH site engine, moved from the private deployment. The deployment's own values (domain, paths, network, container names) became settings; Cloudflare DNS is now optional; the old opencode engine is gone.
- `site/Dockerfile` builds from the repository root; dsh-share-room now comes from npm (0.1.1).
- `plugins/swarm-brand`: Swarm branding and Traditional Chinese for the DSH web client (was `@swarm/dsh-brand`).
- `site/dsh-profile/vendor/dsh-web-auth`: a documented fork of `@summersec/dsh-web-auth` 0.2.0 (see FORK.md).
- `office-template/swarm`: the office template every new site starts from.
- Repository skeleton: README, license, security policy, CI with a leak check (gitleaks plus a hashed deny-list of deployment-specific words).
