# Swarm

[繁體中文](README.zh-TW.md) | English

An unmanned AI office you can host yourself. One platform hands out invite-only sites; every customer gets an isolated [DeepSeek Harness (DSH)](https://github.com/deepseek-ai/deepseek-harness) site with an office template of AI staff, schedules and keys.

> **Status: work in progress.** The code is being moved here from a private deployment. Nothing is ready to install yet. See [PLAN.md](PLAN.md).

## What it will include

- **Platform**: invite-only sign-up, accounts, model quota and metering, key management, notifications and an admin console.
- **Sites**: one DSH container per customer, with branding, login, network isolation and a model relay. The platform never puts its own model keys inside a site.
- **Office template**: companies (hives), AI staff roles, schedules, a task library and two-layer keys, ready inside every new site.
- **Plugins**: DSH plugins such as [dsh-share-room](https://github.com/yillkid/dsh-share-room) for sharing a conversation with guests.

## Name

The idea of a swarm of cooperating agents was inspired by [openai/swarm](https://github.com/openai/swarm). This is an independent project and is not affiliated with OpenAI.

## Security

Please report vulnerabilities privately. See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
