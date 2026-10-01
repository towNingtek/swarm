# Swarm 站台 image。在 repo 根目錄 build：
#   docker build -f site/Dockerfile -t swarm-site:latest .
# 由 npm 乾淨建構：DSH 本體版本由 DSH_VERSION 控制，外掛版本由 site/dsh-profile/package.json 控制。
# repo 內的外掛（plugins/、site/dsh-profile/vendor/）直接複製進去。
# 尚無鎖定依賴的 lockfile，不保證重建可重現；manifest 識別實際建出的內容。
#
# 重要：dsh web 為了防 RCE 暴露，刻意拒絕 --host 0.0.0.0（只能綁 127.0.0.1）。
# 所以容器內用 node 做一層本地 relay：聆聽 RELAY_PORT(預設 3080) 並轉發到
# 127.0.0.1:3081(dsh)。對外仍只暴露容器內 relay 埠，host 再 -p 127.0.0.1:{port} 接回。
FROM node:22-bookworm-slim

# 0.1.5-rc.1 是壞的：它的 dsh-base 依賴 @deepseek-ai/dsh-sandbox-local@^0.1.5-rc.3，
# 但 npm 未把該套件裝進 dsh 的 node_modules，啟動時 Cordis 解析不到而整棵 plugin tree
# 失敗（實測 exit 1）。0.1.7-rc.2 自帶 dsh-sandbox-local，且與 profile 解析到的
# dsh-plugin-subscriptions / cordis 4.0.4 一致。仍無 lockfile，重建不保證可重現。
ARG DSH_VERSION=0.1.7-rc.2

# git 必要：dsh 的 worktree/版本控制功能需要。
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1001 dsh

# dsh 本體（全域安裝，--profile web 找不到 bundles 時從這解析）
RUN npm install -g @deepseek-ai/dsh@${DSH_VERSION}

# 乾淨 web profile 骨架 + 安裝其依賴（subscriptions / web-auth 插件）
COPY site/dsh-profile/package.json site/dsh-profile/cordis.yml site/dsh-profile/cordis.patch.yml /opt/dsh-web-profile/
WORKDIR /opt/dsh-web-profile
# npm 會把 web-auth/subscriptions 的 peer（cordis、dsh-scope、dsh-system-prompt…）
# 另裝一份到 profile。DSH 本體也有一份，兩份同時載入時 dsh-scope 的 Symbol 不同，
# agent preset 的 persona 會被當成全域重複註冊，「New Session」失敗
# （agent-preset/invalid: "deployment:persona-prefix" is already registered）。
# 核心套件一律只用 DSH 本體那份。
RUN npm install --no-audit --no-fund --omit=dev \
    && rm -rf node_modules/@deepseek-ai \
    && find node_modules/.bin -xtype l -delete
# @summersec/dsh-web-auth（MIT）的修改版，見 site/dsh-profile/vendor/dsh-web-auth/FORK.md。
# 加了 renderIndex 相容層、單次入場票、publicPrefixes。改動已準備送回原作者，合併後改回 npm 版。
COPY site/dsh-profile/vendor/dsh-web-auth/ /opt/dsh-web-profile/node_modules/@summersec/dsh-web-auth/
# Swarm 去識別化：自家品牌（側欄/新對話 logo）＋關閉 DeepSeek 帳號登入、DeepSeek 模型供應商、
# DeepSeek 搜尋與內測聲明（見 plugins/swarm-brand/cordis.patch.yml）。profile bundles 已列入。
COPY plugins/swarm-brand/ /opt/dsh-web-profile/node_modules/swarm-brand/
# 對話分享 dsh-share-room 由 npm 安裝（見 package.json）：站主把對話分享給沒有帳號的訪客。
# 訪客頁在 /share/，由外掛自己驗證訪客；web-auth 只對 /share/ 放行（cordis.patch.yml 的 publicPrefixes）。

# 內部 relay
# Default is localhost standalone, NOT production-ready. sitectl must supply
# RELAY_PUBLIC_ORIGIN + RELAY_TRUSTED_PEERS explicitly for public deployment.
COPY site/templates/relay.mjs /usr/local/bin/dsh-relay.mjs
COPY site/templates/relay-policy.mjs /usr/local/bin/relay-policy.mjs
COPY site/templates/profile-init.mjs /usr/local/bin/profile-init.mjs

# container 以 host uid 執行，git 需要預設身分；node_modules 權限放開給 runtime 寫
ARG GIT_NAME="Swarm Site"
ARG GIT_EMAIL="site@swarm.invalid"
RUN git config --system user.name "${GIT_NAME}" \
    && git config --system user.email "${GIT_EMAIL}" \
    && git config --system init.defaultBranch main \
    && git config --system --add safe.directory '*' \
    && chmod -R a+rX /opt/dsh-web-profile \
    && chown -R dsh:dsh /opt/dsh-web-profile \
    && chmod 755 /usr/local/bin/dsh-relay.mjs \
    && chmod 644 /usr/local/bin/relay-policy.mjs /usr/local/bin/profile-init.mjs

USER dsh
# Seal last, after all dependency/vendor overlays and profile permission changes.
RUN node --input-type=module -e 'import { writeProfileManifest } from "/usr/local/bin/profile-init.mjs"; writeProfileManifest("/opt/dsh-web-profile", process.argv[1])' "${DSH_VERSION}"
WORKDIR /home/dsh

ENV RELAY_PORT=3080 \
    DSH_TARGET_PORT=3081 \
    DSH_WEB_PROFILE_DIR=/opt/dsh-web-profile

EXPOSE 3080

CMD node /usr/local/bin/dsh-relay.mjs