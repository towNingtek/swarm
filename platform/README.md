# platform/

The platform site: where customers are invited, sign in, talk to the copilot and support, and are handed into their own site. Operators manage customers and sites from `/admin`.

Three processes share one SQLite database:

| Service | Entry point | Binds | Job |
|---|---|---|---|
| Platform | `platform_service.py` | `127.0.0.1:8210` | Invitations, login, copilot, support rooms, single-use entry into a site |
| Admin console | `admin_service.py` | `127.0.0.1:8211` | Operator login, customers, invites, site builds, password reset, deletion |
| Site model relay | `site_model_relay.py` | Docker bridge `:8212` | The only model endpoint sites can reach. Each site has its own key; the platform key never leaves the host |

Put a reverse proxy with TLS in front of the first two (same origin; admin under `/admin`). The relay is reached only from site containers.

Site work (create, delete, password, isolation) is done by `../site/dsh_sitectl.py`; `_site_path.py` puts `site/` on the import path. Set `SWARM_SITE_DIR` if it lives elsewhere.

## Settings

Each entry point documents its variables at the top of the file. The essentials:

| Variable | Used by | Meaning |
|---|---|---|
| `PLATFORM_ORIGIN` | platform, admin | `https://` origin customers use |
| `PLATFORM_DB` | all | Absolute path of the SQLite database |
| `ADMIN_ORIGIN`, `ADMIN_PASSWORD` | admin | Admin origin (normally the same as the platform) and a 16+ character password |
| `PLATFORM_PROVISION` | platform | `1` to really build sites; off by default |
| `SWARM_DOMAIN` | all | Parent domain for sites |
| `SWARM_ENTRY_SECRET` | platform, site | Master secret for single-use entry tickets |
| `PLATFORM_MODEL_BASE_URL`, `PLATFORM_MODEL_KEY` | relay | Upstream OpenAI-compatible gateway and its key |

| `PLATFORM_SITE_SCHEDULER` | platform | `1` to run customer schedules inside their sites |
| `PLATFORM_SITE_LINE` | platform | `1` to let reports push results to LINE / HackMD (needs the scheduler; off by default) |

`platform_admin_cli.py` is a small command line for the same database (add a customer, mint an invite, set up a site's starter model).

## LINE delivery (optional)

With `PLATFORM_SITE_LINE=1` (and `PLATFORM_SITE_SCHEDULER=1`) a scheduled run can hand its result to LINE, and BD reports can refresh one HackMD note. Off by default; without the flag nothing below happens. Code: `support_site_line.py`, `site_scripts/`.

**Tokens stay in the site.** The platform never reads a LINE, Discord or HackMD token. After a run it pipes `site_scripts/line_agent.js` (or `bd_hackmd.js`) into the site's own container with `docker exec -i … node -`; the script reads the token file the site names under `workspace/.swarm/keys/` and prints only a status.

A report opts in in the site's `hives/<hive>/project.yaml`:

```yaml
line:
  push:
    - {name: bot-a, token_file: shared/line-bot-a.token, to: U0123…}   # user/group id, 33 chars
    - {name: bot-b, relay: {site: beta, channel: bot-b}}              # another site's bot
notify: {channel_id: "12345678", token_file: shared/discord.token}   # optional: alerts via the site's own Discord bot
reports:
  daily_digest:
    line_push: true      # push the cleaned answer after a successful run
    relay: true          # default; false skips relay targets for this report
  bd_review:
    line_push: true
    hackmd: {repo: bd-agent, token_file: shared/hackmd.token, owner: Acme}
```

- What is pushed: the deliverable that `support_schedule_answer.deliverable()` keeps (the `[model=…]` line is dropped). Fewer than 40 characters after cleaning is not pushed (and alerts); more than 1600 is cut with a notice. 15 s per LINE call, no retry.
- `hackmd:` publishes `<repo>/profile/consultant-profile.md` and `<repo>/cases/*.md` to one owner-only HackMD note (id kept in `<repo>/.hackmd-note-id`) and pushes "this week's changes + link" instead of the long text. If HackMD fails the original answer is pushed. `repo` is relative to the workspace, `token_file` to `.swarm/keys`; both must stay inside.
- **Relay** (send through another site's bot) needs consent on both sides. The destination site lists who may use its bot in `workspace/.swarm/line-channels.yaml`:

  ```yaml
  channels:
    bot-b: {token_file: shared/line-bot-b.token, to: U0456…, accept_from: [{site: acme}]}
  ```

  Without that entry (or when the site does not exist) the relay is refused and logged; the source site's own bots are still pushed. There is no endpoint a site could call to start a relay.
- **Scheduled vs manual.** Scheduled runs deliver. `SiteScheduler.run_now(..., deliver=False)` is a dry run that never pushes (the run log says `[試跑：不推播]`); the customer page's 「立即執行」 calls `run_now` with the default `deliver=True`, so it pushes like a scheduled run. Failed runs never push.
- The run log gets `[LINE：bot-a=ok, bot-b=…]` and `[HackMD：<url>]` notes.
- The database keeps a 30-day delivery log (`support_line_deliveries`) and the monthly LINE quota per bot (`support_line_quota`), read hourly with LINE's own quota API (no message sent). Alerts at 80 % / 95 % by default, once per bot per month; at 100 % further sends that month are skipped; more than 30 sends to one bot in an hour alerts without blocking. Per-bot rules live in `line-quota.yaml` next to the database:

  ```yaml
  bots:
    beta/bot-b: {steps: [50, 70, 80, 95], remaining_below: 30, projected_over: 90}
  ```

- Alerts go to the site's own Discord bot (`notify:`) and fall back to the platform notifier (`PLATFORM_NOTIFY_DISCORD_CHANNEL`); with neither configured they are dropped. 30-minute quiet period per alert.

Not included: LINE inbound conversations (webhook replies).

## Tests

```sh
pip install -r ../requirements.txt
./run_tests.sh
```

The UI tests run small scripts under Node 22. `platform_isolated_fixture.py` starts the whole app with fake runners and no real provisioning; tests use it to walk a customer journey end to end.
