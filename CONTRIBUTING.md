# Contributing

Thanks for your interest in Swarm.

## Before you start

- **Security issues**: do not open a public issue. Follow [SECURITY.md](SECURITY.md).
- **Bugs and feature requests**: open an issue with the matching template.
- **Larger changes**: open an issue first so the design can be agreed before you write code.

## How work is described here

Swarm runs its own development the way it runs a customer's: as an **EPIC** with **sub-issues**, each carrying executable acceptance criteria.

- Use the issue templates in [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/) (they are the same as [`swarm/templates/`](swarm/templates/)).
- Every sub-issue lists the files it may change and two tables of commands: what must pass when it is done, and what fails today. Run both before opening the issue. See [docs/issue-acceptance.md](docs/issue-acceptance.md).
- Check a draft offline: `python3 swarm/epic_lint.py --file draft.md --labels epic` (or `--labels by:human` for a sub-issue).

## Development

Requirements: Linux, Python 3.10+, Node 22, Docker (for the image and install tests).

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHON=.venv/bin/python bash scripts/test.sh     # every suite
python3 scripts/check-denylist.py                # no deployment-specific values
```

| Area | Code | Tests |
|------|------|-------|
| Platform, admin console, model relay | `platform/` | `platform/tests/` |
| Site engine and image | `site/` | `site/tests/` |
| Board plugin (Wolffia) and `epic` skill | `plugins/swarm-board/` | `plugins/swarm-board/wolffia/site-plugin/test/`, `plugins/swarm-board/wolffia/tests/` |
| Swarm orchestrator | `swarm/` | `swarm/tests/` |

## Pull requests

- One topic per pull request; describe what changed and how you verified it.
- Add or update tests with the change. CI runs the leak check and every suite.
- Never commit secrets, real hostnames, IP addresses, personal paths or IDs. Use `example.com` and placeholders; the deny-list check will stop the build otherwise.
- By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
