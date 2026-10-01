# Swarm

[繁體中文](README.zh-TW.md) | English

An unmanned AI office you can host yourself. One platform hands out invite-only sites; every customer gets an isolated [DeepSeek Harness (DSH)](https://github.com/deepseek-ai/deepseek-harness) site with an office template of AI staff, schedules and keys.

> **Status: pre-release.** All the code is here, but a clean install has not been verified end to end yet. See [PLAN.md](PLAN.md).

## What it includes

- **Platform**: invite-only sign-up, accounts, model quota and metering, key management, notifications and an admin console.
- **Sites**: one DSH container per customer, with branding, login, network isolation and a model relay. The platform never puts its own model keys inside a site.
- **Office template**: companies (hives), AI staff roles, schedules, a task library and two-layer keys, ready inside every new site.
- **Plugins**: DSH plugins such as [dsh-share-room](https://github.com/yillkid/dsh-share-room) for sharing a conversation with guests.

## Repository

| Path | What it is |
|---|---|
| [`platform/`](platform/) | The platform site, the admin console and the site model relay |
| [`site/`](site/) | The site engine and the DSH site image |
| [`office-template/`](office-template/) | The office every new site starts with |
| [`plugins/`](plugins/) | DSH plugins kept in this repository |
| [`deploy/`](deploy/) | Install on one host with systemd, Docker and nginx |
| [`docs/`](docs/) | Architecture, security model, HTTP API, design records |

Run the tests with `bash scripts/test.sh` (Python 3.10+, Node 22).

## Name

The idea of a swarm of cooperating agents was inspired by [openai/swarm](https://github.com/openai/swarm). This is an independent project and is not affiliated with OpenAI.

## Security

Please report vulnerabilities privately. See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
