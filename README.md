# Swarm

[繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md) | English

[![CI](https://github.com/towNingtek/swarm/actions/workflows/ci.yml/badge.svg)](https://github.com/towNingtek/swarm/actions/workflows/ci.yml) [![Release](https://img.shields.io/github/v/release/towNingtek/swarm)](https://github.com/towNingtek/swarm/releases) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

An AI office you can host yourself, run by a **swarm**: AI agents take the issues of an EPIC in parallel, a judge accepts their work only when its acceptance criteria pass, and people decide and accept. You run one platform; each customer you invite gets an isolated [DeepSeek Harness (DSH)](https://github.com/deepseek-ai/deepseek-harness) site that already holds an office template: companies, AI staff roles, schedules and a place for their own keys.

![The operator creates a customer and sends an invite; the customer activates, the site gets built, and they work with the AI in their own site](docs/media/demo.gif)

Full video with captions: [docs/media/demo.mp4](docs/media/demo.mp4). The demo runs on a throwaway install with a scripted model, so the AI's replies are canned.

> The UI text is currently in Traditional Chinese.


## How the swarm works

Swarm turns a goal into **an EPIC of small issues**, hands each issue to an AI agent, and lets the agents work **in parallel**. A result is accepted only when the issue's **executable acceptance criteria** pass, run by a separate judge. People define the goal, release the work and accept the result; they do not need to watch it.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/swarm-flow.en.dark.png">
  <img alt="How the swarm works: define, release, build and judge in parallel, accept" src="docs/diagrams/swarm-flow.en.light.png" width="100%">
</picture>

<sub>Source: [`docs/diagrams/swarm-flow.en.mmd`](docs/diagrams/swarm-flow.en.mmd)</sub>

| Stage | Who | Gate |
|-------|-----|------|
| ① Define | Person + `epic` skill | No issue without a testable "done"; every criterion is run first and must fail today; drafts must pass `epic_lint` |
| ② Release | Person | Adding `swarm:go` freezes the criteria |
| ③ Build and judge | Agents + runner | Each agent works in its own worktree, limited to the files its issue lists; issues on the same file are chained; the runner executes the criteria verbatim and counts only exit codes |
| ④ Accept | Person | Review the EPIC pull request (low-risk EPICs may auto-merge); a closing summary feeds the monthly report |

![The board (demo data): planets are open issues on rings by status; clusters are EPICs with their completion arc; red glow means an issue is stuck](docs/media/30-board.png)

Every site has the **Wolffia board** at `/board/`: a live view of a GitHub Project where you drag cards to change status, release EPICs, add projects from any org you can see, and ask **📊 Analyse** — the site's own AI reads the board and answers. See [`swarm/`](swarm/) and [`plugins/swarm-board/`](plugins/swarm-board/).

## What it does

- **Invite-only sites.** In the admin console you create a customer, pick an office template, set a monthly model quota and send a single-use invite link. The customer picks their own username and password.
- **One container per customer.** The platform builds the site by itself: a DSH container with its own login, data and workspace, an nginx route and (optionally) a DNS record. Deleting the customer removes all of it.
- **Onboarding page with a setup copilot.** The customer sees setup progress and the site build live. The copilot explains the next step from what the platform actually recorded, and does not make up progress.
- **Into the site without a second password.** **Open my site** hands the signed-in customer over with a single-use ticket.
- **The platform's model key never enters a site.** Each site gets its own starter key, which only works against the platform's relay. The relay checks that key and the site's monthly budget, then forwards the call with the platform key.
- **An office ready on first visit.** The workspace starts with the office template (an `AGENTS.md`, a getting-started guide, a place for companies). Customers can add their own model keys later.
- **Conversation sharing.** [dsh-share-room](https://github.com/yillkid/dsh-share-room) is installed in every site, so a customer can invite guests into a conversation.
- **Operator view.** One page lists every customer with their site, quota policy and template. When a customer asks for a human, the operator answers from the same console.

## Screenshots

| Operator | Customer |
|---|---|
| ![Operator overview: every site, its usage, last run and what needs attention; 📊 board and site buttons on each card](docs/media/20-admin-overview.png) | ![Customer overview: own site, usage, recent runs, one-click board](docs/media/21-customer-overview.png) |
| ![Activating an account from the invite link](docs/media/04-activate.png) | ![Customer settings: setup progress and the site password](docs/media/22-customer-settings.png) |
| ![The single-use invite link (masked here)](docs/media/03-admin-invite.png) | ![The onboarding page with the site ready](docs/media/06-site-ready.png) |

Inside the customer's own site: the reply comes through the platform relay, and the usage counts toward the monthly quota.

![Working with the AI inside the customer's own site](docs/media/09-site-chat.png)

## How it fits together

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/architecture.en.dark.png">
  <img alt="Architecture: browser, nginx, platform, admin console, site containers, model relay, gateway" src="docs/diagrams/architecture.en.light.png" width="100%">
</picture>

<sub>Source: [`docs/diagrams/architecture.en.mmd`](docs/diagrams/architecture.en.mmd)</sub>

Each site sits on an isolated Docker network. The relay is the only host port it can reach: no other host ports, private networks or cloud metadata. The platform never runs `sudo`. Two small root units do the privileged work, one for the firewall and one that applies nginx configs. Details: [docs/architecture.md](docs/architecture.md) and [docs/security.md](docs/security.md).

## Before you run it

- **Use a host dedicated to Swarm.** The platform user is in the `docker` group, which is equivalent to root.
- **Containers are not VMs.** All customers share one kernel and one Docker daemon. DSH's in-site sandbox does not work in a container, so the container, network and firewall are the only containment.
- **Sites can reach the internet.** A customer's AI can send that customer's data anywhere.
- **You pay for the starter model.** Every site's starter calls go through your gateway on your key. Monthly caps are enforced, but token counts are estimates.
- **TLS is yours.** Nothing here terminates TLS. Put certbot, a load balancer or a tunnel in front.

The full list is under [Known limits](docs/security.md#known-limits).

## Install

You need a Linux host (Ubuntu 22.04 or similar) with Docker 24+, nginx and Python 3.10+, about 2 GB of disk for the site image, and an OpenAI-compatible model gateway (for example LiteLLM). Point `platform.<domain>` and `*.<domain>` at the host.

```sh
sudo git clone https://github.com/towNingtek/swarm /opt/swarm
cd /opt/swarm
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo docker build -f site/Dockerfile -t swarm-site:latest .
sudo deploy/install.sh
sudo editor /etc/swarm/platform.env          # domain, origins, model gateway
sudo editor /etc/swarm/secrets/model-key
sudo cp deploy/nginx/swarm-platform.conf.example /etc/nginx/conf.d/swarm-platform.conf
sudo editor /etc/nginx/conf.d/swarm-platform.conf && sudo nginx -t && sudo systemctl reload nginx
sudo deploy/install.sh --start
```

Then open `https://platform.<domain>/admin` and sign in with the password in `/etc/swarm/secrets/admin-password`. The rest of the settings, logs and the first customer are covered in [deploy/README.md](deploy/README.md).

The whole install is tested from scratch in a throwaway container: `bash deploy/tests/clean-install/run.sh`.

## Repository

| Path | What it is |
|---|---|
| [`platform/`](platform/) | The platform, the admin console and the site model relay |
| [`site/`](site/) | The site engine and the DSH site image |
| [`office-template/`](office-template/) | The office every new site starts with |
| [`plugins/`](plugins/) | DSH plugins kept here: branding, and the board (Wolffia) with the `epic` skill |
| [`swarm/`](swarm/) | The swarm orchestrator: agents, the judge, autostart, summaries and reports |
| [`deploy/`](deploy/) | Install on one host with systemd, Docker and nginx |
| [`demo/`](demo/) | The scripted model and recording scripts behind the demo above |
| [`docs/`](docs/) | Architecture, security model, HTTP API, design records, swarm contracts |

## Development

```sh
bash scripts/test.sh          # every suite; Python 3.10+, Node 22
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for how issues are written here (EPICs with executable acceptance criteria) and what a pull request needs.

The platform is Python (FastAPI) with server-rendered pages. The site image is DSH plus profile patches; DSH itself is not modified.

## Name

The idea of a swarm of cooperating agents was inspired by [openai/swarm](https://github.com/openai/swarm). This is an independent project and is not affiliated with OpenAI.

## Security

Please report vulnerabilities privately. See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © towNingtek inc.
