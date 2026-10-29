# Upgrading sites to DSH 0.2

How a site moves from DSH 0.1.7 (Node 22) to DSH 0.2.1 (Node 24), and how it goes back. Written from the
first run on a test site (EPIC #50, sub-issues #45–#49). Follow it for each production site in #49.

## What is different on 0.2

| | 0.1.7 build (default) | 0.2 build |
|---|---|---|
| Build argument | `DSH_MAJOR=0.1` (or none) | `DSH_MAJOR=0.2` |
| Node, DSH | 22, `0.1.7-rc.2` | 24, `0.2.1-rc.5` |
| Plugin versions | `site/dsh-profile/package.json` | `site/dsh-profile/package.dsh02.json` |
| `dsh-plugin-subscriptions` | `0.9.11` | `0.9.12` |
| `dsh-share-room` | `0.3.1` | `0.3.3` |

`site/tests/test_dsh02_image.py` keeps the two package files identical except for those two plugins, and
only forward.

### Incompatibilities found on the test site

None of these showed up in unit tests; all three appeared only on a real 0.2 site. Each now has a test.

| Symptom on 0.2 | Cause | Fix and test |
|---|---|---|
| No setting can be saved; the 0.2 「預覽版說明」 dialog cannot be dismissed (「暫時無法儲存確認狀態」) | An unquoted `!!js` expression containing `: ` in `cordis.patch.yml` is read as a YAML mapping. 0.2's config editor refuses the whole file. (0.1.7 silently got a wrong value: `SWARM_BOARD_PROJECTS` never applied.) | Quote such expressions. `site/tests/test_dsh_profile_yaml.py` |
| Every AI turn ends in error: `ctx.webServer.getOrigin is not a function` | The web-auth fork replaces the stock web server and lacked `protocol` and `getOrigin()`, which 0.2's `dsh-web-app` reads every turn | Added to the fork. `site/tests/webauth_surface.test.mjs` (needs `DSH_PREFIX`; run by `scripts/test.sh`) |
| The owner's messages never reach the conversation: `Cannot read properties of undefined (reading 'signal')` | 0.2's gateway requires a Peer on every in-process call | `dsh-share-room` 0.3.3 calls as the owner Peer |

## Before you start

- **Never let a 0.2 image become `dsh-hub:latest` until every site has switched (#49).** New sites and
  every 0.1.7 site are built from it. Build 0.2 images under their own tag.
- **The site's `cordis.patch.yml` is carried across a migration.** A site created before the YAML fix still
  has the unquoted `projects:` line: fix it on the site first (see below), or no setting saves on 0.2.
- A site needs a working model to test anything AI-related. The starter model is set per site in the admin
  console (「額度」); `disabled` makes every turn fail with 403 `starter_model_disabled`.

## 切換一個站台（Switch one site）

All commands run on the platform host. `SITE` is the site's name.

1. **Back up share data** (the migration keeps it, this is the second copy):

   ```bash
   TS=$(date +%Y%m%d-%H%M%S)
   sudo tar -czf /srv/sites/.swarm-platform/backup-$SITE-share-room-$TS.tgz -C /srv/sites/$SITE/dsh-home share-room
   ```

2. **Fix the site's `cordis.patch.yml`** if its `projects: !!js` line is not quoted. Copy the line from
   `site/dsh-profile/cordis.patch.yml` in this repository; keep a backup of the site's file. Check the file
   parses under 0.2 before going on: the value must be one expression, not a mapping.

3. **Build the 0.2 image under its own tag** (the platform's build script retags `dsh-hub:latest`; do not
   use it for 0.2):

   ```bash
   CODE=<platform checkout>; SHA=$(git -C "$CODE" rev-parse --short origin/main)
   B=$(mktemp -d); git -C "$CODE" archive origin/main | tar -x -C "$B"
   docker build -f "$B/site/Dockerfile" --build-arg DSH_MAJOR=0.2 -t "swarm-site:dsh02-$SHA" "$B"
   docker image inspect -f '{{.Id}}' dsh-hub:latest   # must be unchanged
   ```

4. **Rehearse, then migrate** (same tool as every profile change; a failed live swap restores the old
   profile and container by itself, and a retry usually passes):

   ```bash
   M=/srv/sites/.swarm-platform/migrate-site-profile.sh
   $M "$SITE" "swarm-site:dsh02-$SHA" --rehearse [KEY=VALUE…]   # must print REHEARSAL OK
   $M "$SITE" "swarm-site:dsh02-$SHA" [KEY=VALUE…]              # MIGRATION OK
   ```

   Pass the same `KEY=VALUE` site settings the site already has (for example `SWARM_BOARD_ORG=…`).

5. **Check the site** (all must hold):

   - `docker exec oc-$SITE dsh --version` prints `0.2.1…`
   - `docker logs --since 5m oc-$SITE 2>&1 | grep -c 'skipping profile bundle'` prints `0`
   - the login page answers 200
   - `shares.json` keeps format 1 and the same shares
   - in a browser: 「設定 → 訂閱」 is there with the same accounts; the board and the strategy hub open
   - the share acceptance passes (needs a working model on the site; the platform host's Playwright):

     ```bash
     python3 site/tests/e2e/share_acceptance.py "$SITE.<domain>"   # RESULT 16/16, exit 0
     ```

     It goes through the real entry with the owner and a guest in two browser contexts, and checks the
     preview dialog, read-only and full-access AI questions, who answered, and that an ended share refuses
     the guest on the server. It leaves the site's full-access switch as it was.

## 退回 0.1.7（Go back）

The migration keeps the previous profile as `profiles/web.pre-migrate-<stamp>`. To return a site to 0.1.7,
migrate it again with the current 0.1.7 image (the one `dsh-hub:latest` points at):

```bash
$M "$SITE" dsh-hub:latest --rehearse [KEY=VALUE…] && $M "$SITE" dsh-hub:latest [KEY=VALUE…]
```

Share data in format 1 reads the same on both versions. Do **not** edit files inside a site's profile
directory by hand: the profile is sealed, and a changed file stops the site from starting ("DSH profile
migration required … Profile entry missing or changed"). Restore the file to bring it back, then build an
image instead.

## 切換紀錄

| 站台 | 日期 | 結果 |
|---|---|---|
| 測試站台 | 2026-10-30 | DSH 0.2.1-rc.5，從 `epic/50`（`4ec920a`，分享房 0.3.3 取自 npm）建置；`share_acceptance.py` 16/16；`dsh-hub:latest` 未變 |
