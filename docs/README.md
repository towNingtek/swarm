# Documentation

| Document | What it covers |
|---|---|
| [architecture.md](architecture.md) | Components, request flow, invite and login, single-use site entry, site builds, the model relay, data locations |
| [security.md](security.md) | Trust boundaries, what the code enforces, known limits |
| [api.md](api.md) | HTTP routes of the platform, the admin console and the site model relay |
| [issue-acceptance.md](issue-acceptance.md) | How to write acceptance criteria for an issue |
| [design/shared-session.md](design/shared-session.md) | Proposal: several people and the AI in one DSH session (not implemented) |
| [adr/001-one-agent-per-company.md](adr/001-one-agent-per-company.md) | Decision: one agent per company, behaviour from contracts |
| [contracts/SPEC.md](contracts/SPEC.md) | Format of a contract file |

Elsewhere in the repository:

- [`platform/README.md`](../platform/README.md): the platform services and their settings
- [`site/README.md`](../site/README.md): the site engine and the site image
- [`deploy/README.md`](../deploy/README.md): installing on one host with systemd, Docker and nginx
- [`SECURITY.md`](../SECURITY.md): reporting a vulnerability
