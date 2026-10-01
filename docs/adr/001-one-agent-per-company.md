# ADR-001: One agent per company, behaviour from contracts

- Status: accepted (2026-08-10). Not yet applied to `office-template/swarm`, which still ships seven roles; see "Consequences".
- Supersedes: the "one agent per role" design (pm, rd, reviewer, devops, tech-writer, marketing, bd).

## Context

The first Swarm gave every company (a *hive*) seven agents, one per role. Running it for months showed:

1. **The roles were not really separate.** Every role ran the same CLI, in the same directory, with the same tools and the same credentials. What differed was one line of identity in the prompt, a model name and a personality file.
2. **Most roles were idle.** Across all companies only one scheduled job was left. In a month, each company's activity came from one role at most; several roles had produced nothing for months.
3. **The pattern that worked was a contract.** One task-specific rule file (sources to trust, output format, what it may write where) pasted in full into the prompt. It was cheaper and more predictable than asking an agent to read five layers of personality files.
4. **Config drift caused incidents.** Seven sets of bot credentials per company led to notifications going to the wrong channel, and settings nobody read stayed in config files.
5. **Cost.** Reading identity files on every wake-up spent tokens for no benefit.

## Decision

```
Identity: one agent per company (hive)
Behaviour: contracts/ - one file per task (review, deploy, weekly meeting, Q&A, ...)
Routing:  the triggering event picks a contract, not an agent
  - pull request opened  -> review contract
  - pull request merged  -> deploy contract
  - chat channel message -> that channel's contract
  - schedule             -> a scheduled contract or skill
  - issue label agent:x  -> contract x
```

Rules for the change:

- **One identity is not one thread.** Session, working notes and locks are per task, so a review and a scheduled report can run at the same time.
- **Limits are written in the contract.** "A review run never merges", "back up before deploying". Before, a role name only implied them.
- **The wall between companies stays.** Per-company credentials are the real boundary. There is no single agent across companies.
- **The contract picks the model.** Heavy tasks get a large model, chores a small one.
- **Output starts with a hat tag** (`[review]`, `[deploy]`) so people can still tell what produced a message.
- Real permission separation, if ever needed, comes from per-trigger credentials or tool allowlists, not from more personas. Not needed yet.

## Consequences

- The contract format is specified in [contracts/SPEC.md](../contracts/SPEC.md).
- The runner must load the contract itself and paste it into the prompt; the agent is not trusted to go and read it.
- `office-template/swarm` still describes seven roles, and the site scheduler (`platform/support_site_scheduler.py`) still schedules by role. Moving the template and scheduler to contracts is open work.
