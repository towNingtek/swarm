# Design: several people in one DSH session

- Status: **proposal, not implemented** in this repository.
- Related: the site image already installs the `dsh-share-room` plugin, which lets a site owner share one conversation with guests under `/share` (see `site/dsh-profile/package.json` and `publicPrefixes` in `site/dsh-profile/cordis.patch.yml`). Speaker labels, discussion mode and member identity described below are not part of this repository.

## Problem

Before a specification exists, a team often needs a shared place to explore the problem:

- a few people and the AI are in the same session;
- people can talk to each other while the AI only watches;
- anyone can call the AI at any time, and it has the full context: the discussion, the commands it ran, the files it changed;
- a non-engineer can drive, with someone else watching and helping.

A chat tool does not give this. The AI there sees only the messages that mention it, and none of the session's working state.

## What DSH already does

| Question | Finding |
|---|---|
| Do two browsers on the same session stay in sync? | Yes. Messages from one appear in the other within a second, and new sessions appear in both sidebars |
| Does a prompt carry a speaker? | No. A session prompt has a request ID, session ID, mode and content only |
| Does every message sent from the web UI wake the AI? | Yes, in both queue and steer mode |
| Can the core store a message without waking the AI? | Possibly: the agent API has a "next turn, no wakeup" send, and sessions allow plugin-defined ignorable events. Not tested |
| Can an existing multi-tenant plugin be reused? | Not recommended. Filtering events per user needs DSH source changes, leaks data silently when the patch is missing, and needs root. It does not add speakers or a discussion mode either |

So "see each other live" already works. What is missing is: **who is speaking**, **who may join**, and **whether a message wakes the AI**.

## Options for where the shared session lives

### A. One site per project (tried, withdrawn)

A project gets its own site; members enter it with a ticket that also carries their identity. Isolation comes from the container: everything in a project site belongs to all members, so no permission table is needed.

This was built and then removed. The problem: a project site starts empty, so a conversation already in progress in someone's own site cannot be brought in.

### B. Share one session from an existing site (preferred)

The owner, in their own site, clicks "share" on a conversation. A plugin forks a share copy of that session and creates an invite link bound to that one session, with an expiry and a revoke button.

- The AI runs only in the owner's site, with the owner's model key. All participants talk to the same model; cost goes to the owner.
- Guests need nothing installed. The link opens a small conversation page served by the owner's site: only this session, plus the discuss / call-the-AI switch. They see none of the owner's other conversations, files or settings.
- The owner can end sharing at any time. After that, guests can no longer post or call the AI. Guests may keep a read-only copy.
- It is a pure DSH plugin and does not need the platform. Identity comes from the share link, not from a platform account.

## Plugin features

### Identity

- Each participant's identity comes from a signed credential (share link, or a platform ticket in option A), verified with a shared secret. When a secret is configured, never fall back to a default local admin.
- Plugin routes check `Host` and `Origin` themselves.

### Speaker labels

- Intercept `session/prompt` with an exact route and append `<swarm_speaker>{"id","name"}</swarm_speaker>` at the **end** of the content before it reaches the core. (At the start, it would become part of DSH's automatic session title. The plugin sets the title from the speaker's own words instead.)
- Escape `<`, `>` and `&` in names so nobody can forge a label.
- Other RPCs that write into a session (`subagent/prompt`, `goal/*`) need the same treatment. Any write RPC the plugin does not handle is refused, and a test pins the core's RPC list so a new one is noticed.
- The UI shows labels as name badges, never as raw tags.
- Off by default. A site enables it explicitly with a flag and a secret of at least 32 characters; single-user sites are unaffected.

### Discuss or call the AI

| Mode | Effect |
|---|---|
| Discuss (default) | Everyone sees the message live. The AI does not run |
| Call the AI | The AI runs one turn and sees all discussion since its last turn |

Two ways to build it:

- **Core-native**: use "next turn, no wakeup" or a custom ignorable event. The discussion becomes part of the session and survives fork and export. To verify: whether a plugin can reach the agent from the server side, whether the web UI shows these messages, and whether context compaction drops them.
- **Plugin-stored (fallback)**: the plugin keeps the discussion in its own log (one JSONL file per session under `$DSH_HOME`), pushes it to participants over its own WebSocket or SSE, and when someone calls the AI, prepends a `<swarm_discussion>` block with the new discussion to that prompt. Always possible; the discussion is not native session content.

The plugin-stored version was chosen for the first iteration.

### Later

- A watch mode where the AI periodically reads the discussion and points out contradictions. Expensive; not planned.
- Typing indicators and a presence list.
- Simultaneous calls to the AI use DSH's own queue; the UI shows the position in the queue.

## Keys (option A, for reference)

The platform master secret never enters a site. Each site gets keys derived from it and the site host, with a different label per purpose:

```
WEB_AUTH_ENTRY_SECRET = HMAC(master, "dsh-site-entry\0"    + host)   # implemented
SWARM_ROOM_SECRET     = HMAC(master, "swarm-room-member\0" + host)   # proposal
```

A site that leaks its own keys still cannot sign tickets for another site.

## Share gateway (option B) findings

Checked in a throwaway container with a probe plugin:

| Check | Result |
|---|---|
| Server-side plugin follows one session's event stream | Works: a snapshot of the history first, then live events |
| Only that session's events arrive | Yes |
| Cancelling the stream | Ends cleanly |
| Server-side `session/create`, `session/prompt`, `session/fork` | Work. Fork returns a new session with the full history |
| A button in the session header | A client plugin can add one; clicking it forks on the server |

Things to watch when building it:

- The snapshot contains internal events (system messages, request context) that may include the system prompt and workspace content. The gateway must forward only an allowlist of human-facing events: user messages, assistant answers, tool summaries.
- The parameter name of `session/list` differs from the other methods.
- `dsh-web-auth` blocks all plugin routes for users without a login. Guest pages need one public prefix (for example `/share`) where the plugin verifies its own credential. The vendored fork supports this through `publicPrefixes`.

## Trust and risks

- Everyone who can call the AI can use the site's shell, files and keys. For option B, guests can reach the owner's files through the AI. This is handled by "always visible" and "revoke any time", not prevented. What a guest has already seen, and what the AI has already done, cannot be undone.
- Participants see everything the others type. That is the point, but the invite screen must say so.
- In a shared site, any member can ask the AI to read the room secret and then post under another member's name within that project. The member list is the trust boundary.
- DSH route priority and RPC shapes may change between releases. Use public APIs where possible and run integration tests on every upgrade.
- If one write path is missed, its messages have no speaker. Refuse unhandled paths by default.

## Open questions

1. Who may share: only site owners, or also guests re-sharing? (Proposal: owners only.)
2. Should participants have levels (read only, can post)? (Earlier decision for option A: no, everyone can post.)
3. Should guests be able to call the AI at all, or only discuss?
