# dsh-synapse

synapse（策略神經系統）as a page of a DSH site. A plugin for DeepSeek Harness (DSH), shipped in the site image beside `dsh-swarm-board`.

- **`/synapse/`** — the graph: 中樞（HackMD roadmap）→ Function（S 編號）→ hive → GitHub Project → EPIC. Built into `page/index.html` at image build time (`build/build.mjs`); no data, token or host name is baked in.
- **`/synapse/data/{hub,projects,hives}.json`** — kept fresh *inside the site* by `lib/fetcher.js`: the HackMD hub with the site's HackMD token (every minute), open Projects + EPICs with the site's own `gh` (every 10 min), and the site's swarm hives (every minute). Only these three names are served.
- **`GET /synapse/api/models`** — the site's own DSH model catalog (`session.modelCatalog`), routable providers only. Same menu as the DSH chat box.
- **`POST /synapse/api/ask`** `{question, model:{provider,model}, context}` and **`GET /synapse/api/reply`** — the Copilot runs in one DSH session per site process titled 「🧠 synapse Copilot」, with the chosen model (`session.selectModel`). Same-origin JSON only; a model the site does not have is refused (400). The prompt tells the model it may not change anything.
- **🧠 策略中樞** launcher button in the DSH Web client, just above 🌌 協作星系.

Everything is behind the site login (`dsh-web-auth`). **Do not add `/synapse` to any public prefix.** Nothing here writes to HackMD, GitHub or swarm.

## Config (`site/dsh-profile/cordis.patch.yml`)

| Env | Effect |
|---|---|
| `SYNAPSE_HACKMD_NOTE` | HackMD note id of the hub; empty = no hub fetch |
| `SYNAPSE_HACKMD_TOKEN_FILE` | path of the site's HackMD token file |
| `SYNAPSE_ORGS` | comma-separated GitHub orgs for Projects/EPICs; empty = none |
| `SYNAPSE_HIVES_DIR` | the site's swarm `hives/` directory |
| `SYNAPSE_DEVICES` | comma-separated hive names that are devices, not systems |
| `SYNAPSE_HUB_URL` | where double-clicking the hub opens (the HackMD note) |

## Tests

```bash
cd plugins/synapse && node --test test/*.test.js
```
