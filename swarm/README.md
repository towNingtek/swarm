# Swarm orchestrator

Runs an **EPIC** with a swarm of AI agents: every sub-issue labelled `by:swarm` is handed to an agent in its own git worktree, every result is judged by running the sub-issue's acceptance commands, and nothing is closed on the agent's word.

```
python3 swarm/epic_lint.py --file draft.md --labels epic            # check a draft offline
python3 swarm/epic_run.py --repo acme/app --epic 42 --swarm --engine claude --max-bees 2
python3 swarm/epic_autostart.py --repo acme/app --engine claude     # cron: start released EPICs
python3 swarm/epic_summary.py --repo acme/app --epic 42             # closing summary
python3 swarm/epic_report.py --repo acme/app --month 2026-10        # monthly report
```

| File | What it does |
|------|--------------|
| `epic_lint.py` | Template and acceptance-criteria checks (offline with `--file`, or on GitHub) |
| `epic_run.py` | One EPIC: agents in parallel worktrees, the judge, retries, merge into the EPIC branch, the final PR |
| `epic_autostart.py` | Starts EPICs a human released with `swarm:go`; respects the GitHub API budget |
| `epic_watch.py` | Notices EPICs that stall |
| `epic_summary.py`, `epic_report.py` | Closing summary per EPIC; monthly trend report |
| `epiclib/` | GitHub access, criteria parsing, the judge, agent sessions, scheduling |
| `swarm.yaml`, `model-policy.yaml` | Concurrency, retries, time limits; which engines and models may run unattended |
| `templates/`, `skills/epic/` | Issue templates and the `epic` skill that guides people through opening an EPIC |

Engines are the official CLIs (`claude`, `codex`, `opencode`), each signed in by its owner; the orchestrator never handles subscription tokens.

Settings (environment): `EPIC_NODE`, `EPIC_DSH` (paths, default from `PATH`), `EPIC_SUBSCRIPTIONS` (DSH subscriptions plugin directory to link, optional), `EPIC_GIT_EMAIL` (identity of the merge commits).

Contracts: [docs/swarm/epic-contract.md](../docs/swarm/epic-contract.md) · [docs/swarm/orchestrator.md](../docs/swarm/orchestrator.md) · [docs/swarm/epic-summary.md](../docs/swarm/epic-summary.md) · [docs/issue-acceptance.md](../docs/issue-acceptance.md)

Tests: `python3 -m unittest discover -s swarm/tests -p 'test_*.py'`
