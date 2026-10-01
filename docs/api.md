# HTTP API

The HTTP surface of the platform, the admin console and the site model relay, taken from the route definitions in `platform/*.py`. All three disable FastAPI's `/docs`, `/redoc` and `/openapi.json`.

These are internal APIs for the bundled web pages. They are not versioned and may change.

## Common rules

Apply to the platform and the admin console:

- Every request passes the edge policy (`platform/support_http_policy.py`): one `Host` equal to the configured origin, matching `Origin` on writes, no cross-site fetches. Failure: 403.
- Bodies are `application/json`, at most 16 KiB, with exactly the listed fields and no duplicate keys. Anything else: 400 (413 if too large).
- Query strings are rejected (400) except where noted.
- Errors are `{"error": "request rejected"}` with a status code; details are not returned.

Typical status codes: 400 bad input, 401 no or invalid session, 403 refused by edge policy or not authorised, 409 conflict (stale revision, account exists, site not provisioned), 413 body too large, 429 rate limited or busy.

## Platform (`platform_service.py`, `127.0.0.1:8210`)

Session: the `__Host-customer_session` cookie, set by activate or login.

### Pages

| Method | Path | Session | Notes |
|---|---|---|---|
| GET | `/` | no | Landing page. Accepts `?token=<invite>` only, in canonical form |
| GET | `/customer/welcome` | no | Activation and login form |
| GET | `/customer/onboarding` | yes | Onboarding page; redirects to `/customer/welcome` without a session |
| GET | `/customer/chat` | yes | Support chat page |
| GET | `/customer/ui/*` | varies | Static assets (`landing.css`, `welcome.js`, `chat.js`, `style.css`, `onboarding.js`, `onboarding.css`) |

### Account

| Method | Path | Session | Body | Notes |
|---|---|---|---|---|
| POST | `/customer/activate` | no | `invite`, `username`, `password` | Redeems an invite, creates the account and sets the session cookie. Rate limited |
| POST | `/customer/login` | no | `username`, `password` | Sets a new session cookie. Rate limited |
| POST | `/customer/logout` | yes | `{}` | Revokes the session and clears the cookie |
| GET | `/customer/me` | yes | | The signed-in principal (id, tenant, username, role) |

Usernames: 1-64 of `A-Z a-z 0-9 _ . -`. Passwords: 8-1024 bytes. Invite: 43 URL-safe characters.

### Onboarding

| Method | Path | Session | Body | Notes |
|---|---|---|---|---|
| GET | `/customer/onboarding/status` | yes | | Site job status, model, office, schedules, tasks |
| POST | `/customer/onboarding/acknowledge` | yes | `rules_version` | Acknowledge the current platform rules |
| GET | `/customer/onboarding/copilot` | yes | | Next suggested step, with a signed proposal |
| POST | `/customer/onboarding/copilot/perform` | yes | `kind`, `arguments`, `token` | Perform a proposal the customer confirmed. Only `acknowledge_rules` is allowed |
| GET | `/customer/model-settings` | yes | | Model selection status |
| POST | `/customer/model-settings` | yes | `selection_id`, `expected_revision` | Pick a model from the allowlist (`PLATFORM_MODELS`) |
| POST | `/customer/model-settings/revoke` | yes | `expected_revision` | Clear the selection |

### Office (schedules and tasks in the customer's site)

All POST, session required, no query string.

| Path | Body |
|---|---|
| `/customer/office/schedule` | `revision`, `hive`, `original`, `name`, `role`, `cron`, `skill`, `context` |
| `/customer/office/schedule-delete` | `revision`, `hive`, `name` |
| `/customer/office/hive-enabled` | `revision`, `hive`, `enabled` (`"true"` / `"false"`) |
| `/customer/office/task-accept` | `delivery`, `hive` |
| `/customer/office/task-dismiss` | `delivery` |
| `/customer/office/schedule-run` | `hive`, `name` (needs `PLATFORM_SITE_SCHEDULER=1`) |

`revision` guards against the site's files changing in between; a mismatch is 409 and the page reloads.

### Site

| Method | Path | Session | Body | Mounted when | Notes |
|---|---|---|---|---|---|
| POST | `/customer/site-entry` | yes | `{}` | `SWARM_ENTRY_SECRET` is set | Returns `{"url": "https://<site>/auth/enter?ticket=..."}`. 409 if the site is not provisioned or the tenant is disabled |
| GET | `/customer/site-password` | yes | | `PLATFORM_SITE_PASSWORD=1` | `{"available": true}` |
| POST | `/customer/site-password` | yes | exactly one of `password`, `platform_password` | `PLATFORM_SITE_PASSWORD=1` | Sets the site login password (12+ characters, or reuse the platform password after it verifies). Recreates the container. 429 while another change runs or during a 30 s cooldown |

### Support rooms

| Method | Path | Session | Body | Notes |
|---|---|---|---|---|
| GET | `/customer/rooms` | yes | | The customer's rooms |
| GET | `/customer/rooms/{room_id}` | yes | | Messages. Accepts `?after=<seq>` only |
| POST | `/customer/rooms/{room_id}/messages` | yes | `client_message_id`, `body` | Post a message; idempotent per `client_message_id` |
| POST | `/customer/rooms/{room_id}/respond` | yes | `requested` (bool) | Ask the assistant to answer. 503 if no model is configured |

## Admin console (`admin_service.py`, `127.0.0.1:8211`, under `/admin`)

Session: the `platform_admin_session` cookie (`Path=/admin`). Without it, every path except the login page redirects to `/admin/login`.

`/admin/*` is served by a support app mounted inside the admin service (`platform/support_swarm_admin.py`). The paths below are the public ones.

### Login and pages

| Method | Path | Session | Notes |
|---|---|---|---|
| GET | `/admin/login` | no | Login form |
| POST | `/admin/login` | no | Form field `password`. Sets the cookie and redirects to `/admin/customers` |
| GET | `/admin/customers` | yes | Customer management page |
| GET | `/admin/customers/assets/*` | yes | `management.js`, `tasks.js`, `chat.js`, `style.css` |
| GET | `/admin/support` | yes | Redirects to `customers#chat-section` |

### Tenants

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/admin/tenants` | | All tenants: host, template, enabled, site job status, quota policy. No secrets |
| POST | `/admin/tenants` | `client_request_id`, `host`, optional `room_mode` (`ai`, `coassist`, `human`), optional `policy` | Create a tenant and its support room. Replaying the same request returns the original result; a different payload with the same ID is 409 |
| GET | `/admin/tenants/{id}` | | One tenant |
| POST | `/admin/tenants/{id}/invites` | `ttl_seconds` (60-86400) | Returns the invite token once (201). Not idempotent |
| POST | `/admin/tenants/{id}/quota` | `policy`: `{"mode":"disabled"}`, `{"mode":"unlimited"}` or `{"mode":"capped","monthly_limit":N}` | Monthly model budget for the tenant (relay and assistant share it) |
| GET | `/admin/site-templates` | | Available workspace templates and the default |
| POST | `/admin/tenants/{id}/template` | `template` (an ID or `none`) | Template for the next site build; never rewrites an existing workspace |
| POST | `/admin/tenants/{id}/site-jobs` | `client_request_id` | Queue the site build. Records intent; the platform service builds it |
| POST | `/admin/tenants/{id}/delete` | `expected_host` | Delete the tenant and its site. Only when `ADMIN_ALLOW_DELETE=1`. 409 `teardown_incomplete` if external resources remain |

### Task library

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/admin/tasks` | | Task templates |
| POST | `/admin/tasks` | `original`, `name`, `title`, `role`, `cron`, `skill`, `context`, `is_default` | Save a template |
| POST | `/admin/tasks/delete` | `name` | Delete a template |
| GET | `/admin/tenants/{id}/tasks` | | What was handed to this tenant |
| POST | `/admin/tenants/{id}/tasks` | `names` (list), `mode` (`direct` or `offer`) | Hand templates to a tenant. `direct` writes them into the site; `offer` lets the customer accept or dismiss |

### Support rooms

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/admin/rooms` | | All rooms |
| GET | `/admin/room-overview` | | Rooms with status, for the console |
| GET | `/admin/rooms/{room_id}` | | Messages. Accepts `?after=<seq>` only |
| POST | `/admin/rooms/{room_id}/messages` | `client_message_id`, `body` | |
| POST | `/admin/rooms/{room_id}/reply` | `client_message_id`, `body` | Reply as a human |
| POST | `/admin/rooms/{room_id}/control` | `mode`, `expected_epoch` | Switch the room between `ai`, `coassist` and `human` |
| POST | `/admin/rooms/{room_id}/assist` | `instruction` | Ask the assistant to answer with an operator instruction. 503 without a model (`PLATFORM_COPILOT=gateway`) |

## Site model relay (`site_model_relay.py`, default `172.17.0.1:8212`)

Reached only from site containers. Not proxied by nginx.

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/v1/models` | `Authorization: Bearer sms_...` | The starter models (`SITE_MODEL_IDS`) |
| POST | `/v1/chat/completions` | `Authorization: Bearer sms_...` | OpenAI-compatible. Fields outside an allowlist are dropped, `n` forced to 1, output capped |
| any | anything else | | 404 |

Errors use the OpenAI shape `{"error": {"message", "type", "code"}}`. Codes include `invalid_api_key` (401), `site_disabled` (403), `starter_model_disabled` (403, tenant policy is disabled), `request_too_large` (413), `upstream_unavailable` (502). Peers outside `172.16.0.0/12` and `127.0.0.0/8` get 403.

## Site login (`dsh-web-auth`, inside each site)

For reference; served by the vendored fork in `site/dsh-profile/vendor/dsh-web-auth`.

| Path | Notes |
|---|---|
| `/auth/login` | Password login |
| `/auth/enter?ticket=...` | Single-use entry ticket from the platform; optional `next` (local path only) |
| `/auth/handoff`, `/auth/handoff.js`, `/auth/native-session` | Same-origin exchange from the web-auth session to DSH's session |
| `/auth/logout`, `/auth/status` | |
| `/share/...` | Guest pages of the conversation-sharing plugin; no site login |

## Test fixture only

`platform/platform_isolated_fixture.py` composes a platform with simulated runners for offline tests. It adds these routes; `platform_service.py` never mounts them.

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/__fixture/health` | | Fixture liveness |
| GET | `/customer/site-credential` | | Whether a one-time site password is waiting (host and username only, never the password) |
| POST | `/customer/site-credential/reveal` | `{}` | Returns that password once, then deletes it |
| POST | `/customer/model-settings/simulated-probe` | `expected_revision` | Simulated model check; the outcome cannot be chosen by the caller |

## Not exposed over HTTP

Done in-process or by the command line `platform/platform_admin_cli.py`, never by an HTTP route:

- claiming, completing or reconciling a site job;
- issuing or rotating a site model key (`starter-model`);
- moving a site onto the isolated network (`isolate-site`);
- revoking an invite (`SupportCore.revoke_invite`; there is no HTTP route or CLI command for it yet).
