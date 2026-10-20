# dsh-swarm-board

The swarm board as a page of a DSH site. A plugin for [DeepSeek Harness (DSH)](https://github.com/deepseek-ai/deepseek-harness).

- **`/board/`**: the board page (`wolffia/v8.html`, read once when DSH starts).
- **`/board/data/<file>.js`**: board data, read from the data directory on every request. Only `index.js`, `p<number>.js` (a acme project) and `p-<org>-<number>.js` (another org's project) are served. Any other name, including `..`, gets 404.
- **`POST /board/api/epic/go`** `{url, auto_merge?}`, **`api/epic/rego`** `{url}`, **`api/epic/automerge`** `{url, on}`, **`api/status`** `{project, url, status}`: release, re-release, auto-merge on/off, and move a card (lib/actions.js). These follow the same rules as Wolffia's host API, but use the site's `gh`. Comments say 「由 站台看板（<host name>）…」. Success returns `200 {ok:true, by, already?, auto_merge?, on?, state?}`, where `by` is the site's host name. Errors return `400/502 {error}`. Each route takes `application/json` only, rejects other sites' `Origin`, and caps the body at 4KB. `api/status` only takes projects listed in `projects`; `project` is the number (`2`, acme) or `"org/number"` (`"acme/1"`), and the Status field is changed in that project's own org. Without a logged-in `gh`, every route answers `409`. After a success, the plugin re-fetches that project's board data.
- **`GET /board/api/projects/available`**: the projects the board can add (「＋ 加入 project…」). The site's `gh` lists, by GraphQL (it has no `gh project` command), the projects of the logged-in user and of every org they belong to. Only open projects are listed, most cards first: `{github:true, projects:[{owner, number, title, items, added}]}`, where `items` is the card count and `added` means the project is already on the board. The list is cached for 10 minutes; `added` is always current. Without a logged-in `gh` the answer is `{github:false}`.
- **`POST /board/api/projects/add`** `{owner, number}`: adds one of those projects. The plugin asks GitHub again and refuses (`400`) anything that is not an open project of the user or their orgs, whatever the page sent. It appends the project to `projects.json` (all other entries stay), fetches it once right away, and returns `200 {ok:true, key, already, fetched, title, index}`, where `index` is the new `data/index.js` (`window.PROJECT_INDEX`). Adding a project that is already there is not an error (`already:true`). The project then joins every later fetch round and `api/status`. Same request rules as the other `POST` routes (JSON only, same origin, 4KB, `409` without `gh`).
- **📋 看板** button: fixed at the bottom right of the DSH Web client. It opens `/board/` in a new tab.

Every response carries `cache-control: no-store` and `x-content-type-options: nosniff`.

## Install

You need:

- DSH with the web profile (tested against the same DSH as `dsh-share-room`);
- Node.js 22 or later.

Steps:

1. Install it into your DSH web profile:

   ```bash
   dsh plugin --profile web add ./wolffia/site-plugin
   ```

   DSH adds it to the profile's `dsh.profile.bundles` for you, and its `cordis.patch.yml` inserts the `swarm-board` plugin.

2. Put the board data in the data directory (default `$DSH_HOME/swarm-board/data`): `index.js` and one `p<number>.js` (or `p-<org>-<number>.js`) per project.

3. Restart DSH. **📋 看板** appears at the bottom right.

To update, run `dsh plugin --profile web update dsh-swarm-board`. To uninstall, run `dsh plugin --profile web remove dsh-swarm-board`.

Where things live:

- Host routes: one `webServer` prefix route, `/board`.
- The button: `lib/launcher.js`, loaded by the DSH Web client as this package's `./client` bundle (`dsh.client` in `package.json`). The same file is served at `/board/_/launcher.js`.
- **`/board` is not public.** It stays behind the site login. If a site-wide login gate sits in front of DSH (for example `@summersec/dsh-web-auth`), do **not** add `/board` to its public prefixes. Only `/share` (dsh-share-room guests) belongs there.

### Config

Restate `swarm-board` in the profile's `cordis.patch.yml`:

```yaml
- id: swarm-board
  name: 'dsh-swarm-board'
  config:
    dataDir: /srv/swarm-board/data   # default: $DSH_HOME/swarm-board/data
    html: /srv/swarm-board/v8.html   # default: ../v8.html next to this package
    projects: [3, "acme/1"] # projects to fetch; also the only ones api/status may change
    siteName: board.example.com      # `by` in action responses and comments; default: os.hostname()
    org: acme            # project owner for api/status when the board data has none yet
```

Projects added from the board (「＋ 加入 project…」) are written to `projects.json`, one level above `dataDir`. On start the plugin uses `config.projects` plus every entry of that file, so added projects survive a restart.

`projects` entries:

- an integer `N` is the default org's project N (as before): data file `p<N>.js`, board URL `/board/?p=N`;
- a string `"org/N"` is project N of that org, e.g. `"acme/1"`: data file `p-acme-1.js`, board URL `/board/?p=acme/1`. `"acme/N"` is the same as `N`. Org names are compared case-insensitively.

Fetching, `api/status` and 運行中 (`/board/api/running`) each use the project's own org. `data/index.js` lists every project with `num`, `owner` and `key` (the entry above); `?p=` selects by `key`.

## Development

```bash
cd wolffia/site-plugin
node --test test/*.test.js
```

The tests use a fake ctx and fake req/res. They need no DSH.

### 「分析」的對話開在哪裡

新的「分析」對話用 `session.create` 的 `cwd` 開在站台的工作區：設定 `thinkCwd` 給的目錄；沒設就用 `HOME` 環境變數（站台裡是 `/home/dsh/workspace`）；連 `HOME` 都沒有就不帶 `cwd`（DSH 預設）。外掛不再自己建立 `$DSH_HOME/swarm-board/think` 工作區。

要放進側欄的分組（DSH Workspace）時，設定 `thinkWorkspace: <目錄>`，外掛才會在那個目錄建 Workspace（`thinkWorkspaceTitle` 改名稱，預設「📊 看板分析」）。沒給目錄就不建。

已經記在 `sessions.json` 的對話照舊續用，不會搬動。

```yaml
    thinkCwd: /home/dsh/workspace     # default: $HOME
    thinkWorkspace: /srv/board-think  # optional; only then a DSH Workspace is created
```

### 站台層級的 `epic` skill

外掛內附 `skills/epic/`：`SKILL.md`、`templates/epic.md`、`templates/sub-issue.md`，和只支援 `--file` 的離線 lint（`lint/epic_lint.py`＋`lint/epiclib/`，不需要 GitHub）。DSH 啟動時外掛把它複製到 `$DSH_HOME/skills/epic/`：目錄不存在、或內容和外掛版本不同時才寫，寫入先放暫存目錄再改名。設定 `skillsDir` 可改目標的 skills 目錄（預設 `$DSH_HOME/skills`）。

分析對話的提示寫「需要開 EPIC 時，用 `epic` skill」，不再指向 swarm repo 的路徑。

```bash
cd $DSH_HOME/skills/epic/lint
python3 epic_lint.py --file <草稿> --labels by:swarm   # 0 通過；1 不通過；2 參數錯誤
```

`skills/epic/` 是 swarm repo 的 `docs/skills/epic/SKILL.md`、`docs/templates/`、`scheduler/epiclib/{lint,criteria}.py` 的站台版；那邊改了規則要同步過來（`scheduler/tests/epic/test_skill_bundle.py` 會檢查內附的 lint 能獨立跑）。

### 每日聚合

每日聚合（`digestEveryMs`，預設一天）每一輪要跑之前先看 `gh auth status`：站台的 `gh` 沒登入就跳過這一輪、記一行 log，下一輪再看。
