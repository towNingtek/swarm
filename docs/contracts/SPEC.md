# Contract format

Status: specification ([ADR-001](../adr/001-one-agent-per-company.md)). The site scheduler in this repository does not load contracts yet.

A **contract** is one Markdown file in `hives/<company>/contracts/`. It is a rule set for one task, not a personality. When an event wakes the agent, the runner pastes the whole contract into the prompt: what to do this time, how, and what not to do.

## Fields

| Field | Where | Meaning |
|---|---|---|
| `hat` | First line under the H1 | Tag such as `[review]` or `[weekly-meeting]`. Every message the run produces starts with it |
| `model` | Second line under the H1 | Model alias for this task. Overrides the company default |
| `Scope` | H2 section | Which event, repository or channel this applies to |
| `Source Priority` | H2 section, optional | Which evidence wins when sources disagree |
| `Required Behavior` | H2 section | Rules the run must follow, including limits ("never merge", "back up first") |
| `External Writes` | H2 section | Default is no writes outside the workspace; list each allowed write (comment on the pull request, post to a channel, ...) |
| `Response Format` | H2 section | Output format, starting with the hat |

## Loading

1. The event (webhook, schedule, chat mention) names a contract file.
2. The runner reads the file.
3. It parses `hat` and `model` from the two lines under the H1.
4. `model` sets the session model. `hat` goes at the top of the prompt.
5. The whole contract is pasted into the prompt.
6. The agent runs; its output starts with `[hat]`.

The runner does the loading. The agent is never asked to find and read the contract itself.

## Routing

| Event | Key | Contract |
|---|---|---|
| Pull request opened or updated | fixed | `hives/<company>/contracts/review.md` |
| Pull request merged | fixed | `hives/<company>/contracts/deploy.md` |
| Issue comment with an `agent:<x>` label | label value | `hives/<company>/contracts/<x>.md` |
| Message in a chat channel | the company's channel-to-contract map | the mapped file |
| Schedule | job name | a contract or a skill |

## Working notes

Working notes are kept per task, so runs that happen at the same time do not overwrite each other.

| Run | Notes file |
|---|---|
| Contract run | `hives/<company>/tasks/<contract name>/WORKING.md` |
| Other runs | `hives/<company>/agents/<role>/WORKING.md` |

## Example

```markdown
# Review contract

hat: [review]
model: standard

## Scope

Pull request opened or updated in this company's repositories.

## Required Behavior

- Read the diff and give specific, actionable comments.
- Never merge, never push.

## External Writes

- Allowed: review comments on the pull request.
- Not allowed: merge, push, close, change labels.

## Response Format

Start with `[review]`, then the findings ordered by risk.
```
