# Changelog

## 0.2.0 — 2026-10-21

The swarm: issues are run by AI agents in parallel and accepted only when their criteria pass; a board to see and steer it; new operator and customer consoles.

### Swarm orchestrator (`swarm/`)

- `epic_run.py --swarm`: runs an EPIC's `by:swarm` sub-issues with AI agents (official `claude`, `codex` or `opencode` CLIs, signed in by their owners), each in its own git worktree and limited to the files the sub-issue lists. Sub-issues that touch the same files are chained by their dependencies.
- The judge: a separate runner executes each sub-issue's acceptance commands verbatim and counts only exit codes; up to three tries, then `swarm:stuck` for a person. Secrets in command output are redacted before results are posted.
- `epic_autostart.py` starts EPICs a person released with `swarm:go`, and skips a round when less than 20 % of the GitHub API budget is left. `epic_lint.py` checks drafts offline (`--file`) or on GitHub. `epic_summary.py` writes a closing summary per EPIC; `epic_report.py` a monthly trend (first-pass rate, stuck rate, criteria defects, cost).
- Low-risk EPICs can be released with `swarm:auto-merge`: the EPIC pull request merges by itself when every criterion passed and only listed files changed.
- Issue templates (`swarm/templates/`, also `.github/ISSUE_TEMPLATE/`), the contracts (`docs/swarm/`) and the `epic` skill (`swarm/skills/epic/`).

### Board (`plugins/swarm-board/`, in every site at `/board/`)

- Wolffia, a live view of a GitHub Project: planets are open issues on rings by status, red glow marks issues that stay too long, clusters are EPICs with their completion arc and the agents working on them.
- Writes back to GitHub with the site's own `gh` login: drag to change status, release an EPIC, toggle auto-merge, release again after edits.
- 「＋ 加入 project」: lists the open projects the site's GitHub account can see (any org, most cards first) and adds the ticked ones. Projects are keyed `org/number`; bare numbers belong to the default org (`SWARM_BOARD_ORG`).
- 📊 Analyse: the site's own AI reads the board (and the selected cards) and answers; the answer links `#numbers` to cards and can be continued in a site conversation.
- The `epic` skill is installed site-wide (`$DSH_HOME/skills/epic`), with its templates and an offline lint, so any conversation in the site can open an EPIC by the rules.
- Without a GitHub login the board says so and fetches nothing. Settings: `SWARM_BOARD_ORG`, `SWARM_BOARD_PROJECTS`, `SWARM_BOARD_THINK_CWD` (see `site/dsh-profile/cordis.patch.yml`).

### Consoles

Operator and customer consoles, ported from the private deployment (generic parts only; nothing that depends on the old opencode scheduler).

- Operator console (`/admin/customers`): a sidebar shell (總覽 / 站台管理 / 任務庫 / 用量 / 設定) with a dark and light theme that follows the system and can be switched. The overview (`GET /admin/overview`, `platform/support_admin_overview.py`) shows, per site, whether it answers, this month's platform-model usage against its quota, accounts and open invites, the last scheduled run and unanswered customer messages; an attention list; 30 days of daily usage per site; the latest scheduled runs (never their output). Each built site card has 📊 看板 and 進入站台 buttons in its footer (看板 only where the optional `dsh-swarm-board` plugin is installed, read from the site's web profile on disk). The site page is split into tabs (邀請 / 額度 / 樣板 / 建站 / 套件 / 刪除); tabs whose feature is not enabled are hidden.
- One sign-in page: when the console shares the platform origin, the landing page's username `admin` signs in to the operator console, and `/admin/login` (GET) and unauthenticated console requests go to `/`. Customers can no longer take the name `admin`.
- Operator sessions survive restarts: the session key is a 0600 file next to the database (`admin-session.key`) mixed with the admin password, so a redeploy keeps the operator signed in and a password change still signs everyone out.
- Customer console (`/customer/onboarding`): a sidebar (總覽 / 排程 / 用量 / 設定) with the view kept in the URL hash. The overview (`GET /customer/overview`, `platform/support_customer_overview.py`) shows the customer's own usage this month, schedule count, recent runs and what needs attention. A one-click 📊 看板 button opens the site's `/board/` through a single-use entry ticket (`POST /customer/site-entry {"next": "/board/"}`; `SiteEntry.ALLOWED_NEXT` allows only `/` and `/board/`); it appears only when `GET /customer/onboarding/status` reports `board: true`.
- Per-site extra packages (`ADMIN_SITE_PACKAGES=1`, `platform/support_site_packages.py`, `site/dsh_sitectl.py set_site_packages`): the operator lists Debian packages for a site; the platform builds a derived image on the site's base image and recreates the container with the same safety as a password change (built before anything stops, old container kept until the new one passes the self-test). An empty list returns the site to the shared image; password and isolation changes keep a package site on its own image.
- Teardown: a retry after an earlier attempt removed the site but failed on the records now finishes (state folder and container both gone counts as removed; any other error is still reported).

## 0.1.0 — 2026-10-02

First public release.

- P5: README in English, Traditional Chinese and Simplified Chinese with an architecture diagram, a captioned demo video, a GIF and screenshots (`docs/media/`). `demo/` holds the scripted model, the recording script and the compose script behind them.
- Fix, found while recording: a customer's first visit to their site showed "could not create the default workspace", because DSH looks up the Documents directory with `xdg-user-dir`, which the site image does not have. The site profile now sets the Documents directory, and the default workspace in it points at the office workspace (`/home/dsh/workspace`). The clean install checks both.
- P4, an adversarial security review (six areas, each finding reproduced before it was fixed), found and fixed:
  - **high**: moving a site onto the isolated network (`isolate-site`) took the site's host from its own `dsh.env`, which code in the site can rewrite; a site could be handed another site's entry key. The host now comes from the platform's `site.yaml`, and the identity lines are rewritten from it;
  - the platform's login limiter saw every visitor as nginx (one bucket for everybody), and the site's own login limiter saw everybody as the in-site relay. Both now key on the visitor address set by nginx (`X-Swarm-Client` on the platform, `X-Real-IP` passed on by the relay in sites); the admin login is now rate-limited too;
  - the support assistant spent the platform model key without checking the tenant's quota; it now reserves from and settles into the same monthly pool as the site relay;
  - a site that hung up mid-stream left its quota hold behind, and four of them locked the tenant out; holds are now always settled, and stale holds expire;
  - the relay passed gateway-side tools (`mcp`, `web_search`, ...) and the `user`/`store` fields through; only function tools are accepted now. Image parts are counted in the admission estimate;
  - one site's broken files (an invalid date, deep nesting) stopped scheduled runs for every tenant, a whitespace-filled file made a regex take seconds, and long task names could hang task delivery; each site is now isolated and parsing is linear;
  - deleting a tenant failed once its support room had messages, after the site was already gone;
  - cookies planted by a sibling site on the parent domain locked users out of the platform; foreign cookies are now ignored;
  - invite activation could probe which usernames exist; an invite is revoked after 5 taken-name attempts;
  - invite tokens and entry tickets reached the nginx access log; the shipped configs log without query strings;
  - the template's `git init` could follow a `.git` symlink a site swapped in; the marker is written with the same no-follow file code as everything else;
  - a tenant host could equal the platform's own host;
  - the root nginx watcher would copy a hard-linked staged file (where `fs.protected_hardlinks` is off); files with more than one link are refused.
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
