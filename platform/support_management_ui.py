"""Opt-in metadata management UI, separate from the support chat UI.

Install before serving with the same core used by install_management. The core
is explicit so every page/asset request verifies the actual admin capability,
not merely its Python type. No server, authentication, or provisioning wiring.
"""
from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, Response

from support_app import _error
from support_chat_app import AdminBoundary
from support_http_policy import SENSITIVE_RESPONSE_HEADERS


CSS = """
:root { color-scheme: light dark;
  --bg: #f4f5f7; --panel: #ffffff; --panel-2: #f7f8fa; --line: #e3e6eb; --text: #1b2430; --muted: #5d6875;
  --accent: #ff6a3d; --accent-ink: #ffffff; --accent-soft: #fff0ea; --ok: #1f9d55; --ok-soft: #e6f6ec;
  --warn: #9a6400; --warn-soft: #fff4d6; --bad: #c2353b; --bad-soft: #fde9ea; --side: #ffffff; --shadow: 0 1px 2px #0000000f; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #16181d; --panel: #1f2228; --panel-2: #262a31; --line: #30353e; --text: #e8eaee; --muted: #9aa3ae;
  --accent: #ff7a4d; --accent-ink: #1a0d07; --accent-soft: #3a2219; --ok: #4ccf86; --ok-soft: #173326;
  --warn: #f0be55; --warn-soft: #3a2f16; --bad: #ff6b70; --bad-soft: #3d1d20; --side: #1b1e23; --shadow: none; } }
:root[data-theme="dark"] {
  --bg: #16181d; --panel: #1f2228; --panel-2: #262a31; --line: #30353e; --text: #e8eaee; --muted: #9aa3ae;
  --accent: #ff7a4d; --accent-ink: #1a0d07; --accent-soft: #3a2219; --ok: #4ccf86; --ok-soft: #173326;
  --warn: #f0be55; --warn-soft: #3a2f16; --bad: #ff6b70; --bad-soft: #3d1d20; --side: #1b1e23; --shadow: none; }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.55 system-ui, "Noto Sans TC", sans-serif; color: var(--text); background: var(--bg); }
.shell { display: grid; grid-template-columns: 15rem 1fr; min-height: 100vh; }
#side { position: sticky; top: 0; height: 100vh; display: flex; flex-direction: column; gap: .2rem;
  padding: 1rem .8rem; background: var(--side); border-right: 1px solid var(--line); }
.brand { display: flex; align-items: center; gap: .5rem; font-weight: 700; font-size: 1.05rem; padding: .3rem .5rem 1rem; }
.brand-mark { width: 1.6rem; height: 1.6rem; border-radius: .45rem; background: var(--accent); }
.brand small { font-weight: 500; color: var(--muted); font-size: .75rem; border: 1px solid var(--line);
  border-radius: .3rem; padding: 0 .35rem; }
#side nav { display: flex; flex-direction: column; gap: .15rem; }
#side nav button, #theme-toggle, #side .side-link { display: flex; align-items: center; justify-content: space-between;
  width: 100%; padding: .5rem .7rem; border: 0; border-radius: .5rem; background: none; color: var(--text);
  font: inherit; text-align: left; cursor: pointer; text-decoration: none; }
#side nav button:hover, #theme-toggle:hover, #side .side-link:hover { background: var(--panel-2); }
#side nav button[aria-current="page"] { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
.nav-count { min-width: 1.3rem; padding: 0 .4rem; border-radius: 1rem; background: var(--accent); color: var(--accent-ink);
  font-size: .75rem; text-align: center; }
.nav-count[hidden] { display: none; }
.side-foot { margin-top: auto; border-top: 1px solid var(--line); padding-top: .6rem; color: var(--muted); }
main { padding: 1.6rem 2rem 6rem; max-width: 76rem; width: 100%; }
.view[hidden] { display: none; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 1rem; margin-bottom: 1.2rem; }
h1 { font-size: 1.5rem; margin: 0; }
h2 { font-size: 1.1rem; margin: 0 0 .6rem; }
h3 { font-size: 1rem; margin: 1rem 0 .4rem; }
.sub { color: var(--muted); margin: .2rem 0 0; }
section, .card { background: var(--panel); border: 1px solid var(--line); border-radius: .8rem; padding: 1.1rem 1.2rem;
  margin-block: 0 1rem; box-shadow: var(--shadow); }
section > p, .card > p { color: var(--muted); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: .8rem; margin-bottom: 1rem; }
.stat { background: var(--panel); border: 1px solid var(--line); border-radius: .8rem; padding: .9rem 1.1rem; }
.stat .k { color: var(--muted); font-size: .85rem; }
.stat .v { font-size: 1.7rem; font-weight: 700; line-height: 1.2; margin-top: .2rem; }
.stat .d { color: var(--muted); font-size: .8rem; }
#attention-list { list-style: none; margin: 0; padding: 0; }
#attention-list li { display: flex; align-items: center; gap: .7rem; padding: .6rem .2rem; border-bottom: 1px solid var(--line); }
#attention-list li:last-child { border-bottom: 0; }
#attention-list .dot { width: .55rem; height: .55rem; border-radius: 50%; background: var(--warn); flex: none; }
#attention-list .host { font-weight: 600; }
#attention-list button { margin-left: auto; }
#site-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr)); gap: .9rem; }
.site-card { background: var(--panel); border: 1px solid var(--line); border-radius: .8rem; padding: 1rem 1.1rem;
  display: flex; flex-direction: column; gap: .5rem; cursor: pointer; text-align: left; font: inherit; color: inherit; }
.site-card:hover { border-color: var(--accent); }
.site-card .name { font-weight: 700; font-size: 1.05rem; overflow-wrap: anywhere; }
.site-card .rows { display: grid; grid-template-columns: auto 1fr; gap: .15rem .8rem; font-size: .88rem; }
.site-card .rows span:nth-child(odd) { color: var(--muted); }
.bar { height: .35rem; border-radius: 1rem; background: var(--panel-2); overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--accent); }
.pill { display: inline-flex; align-items: center; gap: .35rem; padding: .05rem .55rem; border-radius: 1rem;
  font-size: .78rem; font-weight: 600; background: var(--panel-2); color: var(--muted); width: max-content; }
.pill::before { content: ""; width: .45rem; height: .45rem; border-radius: 50%; background: currentColor; }
.pill.ok { background: var(--ok-soft); color: var(--ok); }
.pill.bad { background: var(--bad-soft); color: var(--bad); }
.pill.warn { background: var(--warn-soft); color: var(--warn); }
.tabs { display: flex; gap: .3rem; margin: 0 0 1rem; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.tabs button { border: 0; border-bottom: 2px solid transparent; border-radius: 0; background: none; color: var(--muted);
  padding: .55rem .9rem; font-weight: 600; }
.tabs button[aria-selected="true"] { color: var(--accent); border-bottom-color: var(--accent); }
.tabs button.tab-gone { display: none; }
section.tab-off { display: none; }
.two-col { display: grid; grid-template-columns: 2fr 1fr; gap: 1rem; margin-top: 1rem; }
@media (max-width: 64rem) { .two-col { grid-template-columns: 1fr; } }
.trend { display: flex; align-items: flex-end; gap: 3px; height: 9rem; padding-top: .5rem; border-bottom: 1px solid var(--line); }
.trend .col { flex: 1; display: flex; flex-direction: column-reverse; height: 100%; min-width: 0; }
.trend .col i { display: block; width: 100%; }
.trend .col:hover { outline: 1px solid var(--line); }
.trend-axis { display: flex; justify-content: space-between; color: var(--muted); font-size: .75rem; margin-top: .3rem; }
.legend { display: flex; gap: 1rem; flex-wrap: wrap; font-size: .82rem; color: var(--muted); margin-top: .5rem; }
.legend span::before { content: ""; display: inline-block; width: .7rem; height: .7rem; border-radius: .2rem;
  margin-right: .35rem; vertical-align: -.05rem; background: var(--c); }
.c0 { --c: #ff7a4d; } .c1 { --c: #4c9aff; } .c2 { --c: #4ccf86; } .c3 { --c: #c78bff; } .c4 { --c: #f0be55; } .c5 { --c: #ff6b9a; }
.trend i.c0, .trend i.c1, .trend i.c2, .trend i.c3, .trend i.c4, .trend i.c5 { background: var(--c); }
.run-list { list-style: none; margin: 0; padding: 0; font-size: .88rem; }
.run-list li { display: grid; grid-template-columns: auto 1fr; gap: .1rem .6rem; padding: .5rem 0; border-bottom: 1px solid var(--line); }
.run-list li:last-child { border-bottom: 0; }
.run-list .meta { grid-column: 2; color: var(--muted); font-size: .8rem; }
#usage-table { width: 100%; border-collapse: collapse; font-size: .9rem; }
#usage-table th, #usage-table td { text-align: left; padding: .5rem .4rem; border-bottom: 1px solid var(--line); }
#usage-table th { color: var(--muted); font-weight: 500; }
#usage-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
label { display: block; margin-block: .6rem; color: var(--text); }
input, select, textarea { font: inherit; padding: .5rem .65rem; max-width: 100%; color: var(--text); background: var(--panel-2);
  border: 1px solid var(--line); border-radius: .45rem; }
input:focus, select:focus, textarea:focus, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
button { font: inherit; padding: .45rem .9rem; border-radius: .45rem; border: 1px solid var(--line); background: var(--panel-2);
  color: var(--text); cursor: pointer; }
button:disabled { opacity: .5; cursor: not-allowed; }
button[type="submit"], .primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); font-weight: 600; }
fieldset { border: 1px solid var(--line); border-radius: .5rem; }
#invite-link { width: 95%; }
.warning { background: var(--warn-soft); color: var(--text); border-radius: .5rem; padding: .7rem .9rem; border: 0; }
#page-note { margin-bottom: 1rem; }
.site-picker { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
.site-picker label { margin: 0; }
#tenant-detail { color: var(--muted); font-size: .88rem; overflow-wrap: anywhere; white-space: pre-wrap; }
.site-links { margin: .3rem 0 0; } .site-links a { margin-right: 1rem; }
.site-card-wrap { display: flex; flex-direction: column; background: var(--panel); border: 1px solid var(--line);
  border-radius: .8rem; overflow: hidden; box-shadow: var(--shadow); }
.site-card-wrap:hover { border-color: var(--accent); }
.site-card-wrap > .site-card { width: 100%; flex: 1; border: 0; border-radius: 0; background: transparent; }
.site-card-wrap > .site-card:hover { border: 0; }
.site-card-actions { display: flex; gap: .5rem; padding: .75rem 1.1rem .9rem; border-top: 1px solid var(--line);
  background: var(--panel-2); }
.act { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: .4rem; padding: .55rem .8rem;
  border-radius: .55rem; font-weight: 600; font-size: .92rem; text-decoration: none; border: 1px solid var(--line);
  background: var(--panel); color: var(--text); transition: filter .15s, border-color .15s; }
.act:hover { border-color: var(--accent); color: var(--accent); }
.act.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.act.primary:hover { filter: brightness(1.08); color: var(--accent-ink); }
.act:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
#tenant-list { display: none; }
#bootstrap-host-row { display: inline-flex; align-items: baseline; gap: .25rem; }
#bootstrap-domain, #bootstrap-preview { color: var(--muted); }
#bootstrap-preview { margin-block: .4rem; }
#invite-row { display: flex; gap: .5rem; align-items: center; }
#invite-row input { flex: 1; }
#invite-copy-status { color: var(--muted); margin-block: .4rem; }
#packages-form label { display: block; }
#packages-input { display: block; width: 100%; margin: .4rem 0 .8rem; font-family: ui-monospace, monospace; }
#danger-section { border-color: var(--bad); }
#delete-submit:not(:disabled) { background: var(--bad); color: white; border-color: var(--bad); }
#task-context { width: 100%; }
#task-list, #task-deliveries { padding-left: 1.1rem; }
#task-list li, #task-deliveries li { margin-block: .45rem; overflow-wrap: anywhere; }
#task-list button { margin-inline-start: .4rem; padding: .2rem .6rem; }
#task-picks label { display: inline-block; margin: .2rem 1rem .2rem 0; }
#task-cron-hint, #task-status { color: var(--muted); margin-block: .3rem; }
.muted { color: var(--muted); }
#status { position: fixed; left: 50%; bottom: 1.2rem; transform: translateX(-50%); margin: 0; z-index: 40;
  max-width: min(40rem, 90vw); }
#status:not(:empty) { background: var(--text); color: var(--bg); padding: .55rem 1rem; border-radius: .5rem; }
#chat-launcher { position: fixed; right: 1.2rem; bottom: 1.2rem; z-index: 30; padding: .75rem 1.2rem;
  border-radius: 999px; border: 0; background: var(--accent); color: var(--accent-ink); font-weight: 700;
  box-shadow: 0 8px 28px #0003; }
#chat-section { position: fixed; right: 1.2rem; bottom: 4.6rem; z-index: 31; display: flex; flex-direction: column;
  width: min(30rem, calc(100vw - 2.4rem)); height: min(44rem, calc(100vh - 6.5rem));
  padding: 1rem; border-radius: .8rem; background: var(--panel); border: 1px solid var(--line); box-shadow: 0 18px 50px #0004; }
#chat-section[hidden] { display: none; }
#chat-section > * { flex: none; }
#chat-section header { display: flex; justify-content: space-between; align-items: center; }
#chat-section h2 { margin: 0; }
#chat-section h3 { margin: .5rem 0 .2rem; }
#chat-section p { margin-block: .3rem; }
#chat-close { width: 2rem; height: 2rem; padding: 0; font-size: 1.1rem; }
#chat-rooms { width: 100%; margin-top: .2rem; }
#chat-panel { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
#chat-panel[hidden] { display: none; }
#chat-panel > * { flex: none; }
.chat-hint { font-size: .8rem; }
.chat-badge { display: inline-block; padding: 0 .45rem; margin-inline-start: .4rem; border-radius: .7rem; background: var(--panel-2);
  color: var(--text); font-size: .85rem; }
.chat-badge.waiting { background: var(--bad); color: white; }
.chat-badge[hidden] { display: none; }
#chat-modes { display: flex; gap: .3rem; flex-wrap: wrap; }
#chat-modes button[aria-pressed="true"] { background: var(--text); color: var(--bg); border-color: var(--text); }
#chat-panel > #chat-thread { flex: 1 1 auto; min-height: 6rem; list-style: none; padding: 0; margin: .4rem 0; overflow-y: auto;
  border: 1px solid var(--line); border-radius: .5rem; }
#chat-thread li { padding: .45rem .7rem; white-space: pre-wrap; overflow-wrap: anywhere; border-bottom: 1px solid var(--line); }
#chat-thread li.customer { background: var(--warn-soft); }
#chat-thread li.human { background: var(--accent-soft); }
#chat-thread .who { font-weight: 600; margin-inline-end: .4rem; }
#chat-thread .when { color: var(--muted); font-size: .8rem; margin-inline-start: .4rem; }
#chat-reply { width: 100%; }
.chat-actions { display: flex; gap: .5rem; }
@media (max-width: 52rem) {
  .shell { grid-template-columns: 1fr; }
  #side { position: static; height: auto; flex-direction: row; flex-wrap: wrap; align-items: center; }
  #side nav { flex-direction: row; flex-wrap: wrap; }
  .side-foot { margin: 0; border: 0; padding: 0; }
  main { padding: 1rem; }
}
"""

PAGE = '''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>客戶管理</title><link rel="stylesheet" href="/admin/customers/assets/style.css">
<script defer src="/admin/customers/assets/shell.js"></script>
<script defer src="/admin/customers/assets/management.js"></script>
<script defer src="/admin/customers/assets/tasks.js"></script>
<script defer src="/admin/customers/assets/chat.js"></script>
</head><body><div class="shell">
<aside id="side" aria-label="主選單"><div class="brand"><span class="brand-mark" aria-hidden="true"></span>Swarm <small>管理</small></div>
<nav>
<button type="button" data-view="overview" aria-current="page">總覽<span id="nav-attention" class="nav-count" hidden></span></button>
<button type="button" data-view="site">站台管理</button>
<button type="button" data-view="tasks">任務庫</button>
<button type="button" data-view="usage">用量</button>
<button type="button" data-view="settings">設定</button>
</nav>
<div class="side-foot"><button id="theme-toggle" type="button">切換深淺色</button></div></aside>
<main>

<div class="view" id="view-overview">
<div class="page-head"><div><h1>總覽</h1><p class="sub" id="overview-sub">各站台目前狀況與需要你處理的事</p></div>
<button id="overview-refresh" type="button">重新整理</button></div>
<div class="stats">
<div class="stat"><div class="k">站台</div><div class="v" id="stat-sites">–</div><div class="d" id="stat-online"></div></div>
<div class="stat"><div class="k">本月平台模型用量</div><div class="v" id="stat-tokens">–</div><div class="d">token（客戶自己的模型不算）</div></div>
<div class="stat"><div class="k">等你處理</div><div class="v" id="stat-attention">–</div><div class="d">見下方清單</div></div>
</div>
<div class="card"><h2>等你處理</h2><ul id="attention-list"></ul></div>
<h2>站台</h2><div id="site-cards"></div>
<div class="two-col">
<div class="card"><h2>近 30 天平台模型用量</h2><div id="trend" class="trend" role="img" aria-label="近 30 天每日用量"></div>
<div id="trend-legend" class="legend"></div></div>
<div class="card"><h2>最近排程</h2><ul id="run-list" class="run-list"></ul></div>
</div>
</div>

<div class="view" id="view-site" hidden>
<div class="page-head"><div><h1>站台管理</h1><p class="sub">選一個客戶站台，下面的操作都只作用在它身上。</p></div></div>
<p class="warning" id="page-note">僅管理客戶與客服中繼資料；本頁不會建立 Docker 站台、不部署、不設定 DNS。顯示「已啟用」僅代表客戶帳號可使用邀請啟用，並不表示站台已上線。</p>
<section><h2>客戶清單</h2><div class="site-picker"><label>目前站台 <select id="tenants"></select></label><button id="refresh" type="button">重新載入</button></div>
<p id="tenant-detail"></p><p id="site-links" class="site-links"></p><ul id="tenant-list"></ul></section>
<div class="tabs" role="tablist" aria-label="站台功能">
<button type="button" role="tab" data-tab="invite" aria-selected="true">邀請</button>
<button type="button" role="tab" data-tab="quota" aria-selected="false">額度</button>
<button type="button" role="tab" data-tab="template" aria-selected="false">樣板</button>
<button type="button" role="tab" data-tab="build" aria-selected="false">建站</button>
<button type="button" role="tab" data-tab="packages" aria-selected="false">套件</button>
<button type="button" role="tab" data-tab="danger" aria-selected="false">刪除</button>
</div>
<section data-panel="invite"><h2>發送邀請</h2><p>邀請只顯示一次，請自行安全傳遞。此頁不自動開啟連結、不保存邀請。切換租戶或離頁會清除。送出失敗可能已發出邀請，請勿盲目重試；需由可信任管理員撤銷不需要的邀請。</p>
<form id="invite-form" autocomplete="off"><label>有效秒數（60–86400）<input id="ttl" type="number" min="60" max="86400" step="1" value="3600" required></label>
<button id="invite-submit" type="submit">產生邀請</button></form>
<div id="accounts-box" hidden><h3>帳號與重設密碼</h3><p class="muted">客戶忘記密碼時，對他的帳號產生「重設密碼連結」（只能用一次，有效時間同上方的有效秒數）。客戶打開連結自己設新密碼；設定後他在其他地方的登入都會被登出。</p>
<table class="tbl"><thead><tr><th>帳號</th><th>上次重設</th><th></th></tr></thead><tbody id="accounts-body"></tbody></table>
<label>重設連結<input id="reset-link" type="text" readonly placeholder="按「產生重設連結」"></label><button id="reset-copy" type="button" disabled>複製</button></div>
<label for="invite-link">一次性邀請連結</label>
<span id="invite-row"><input id="invite-link" type="text" readonly autocomplete="off" spellcheck="false">
<button id="invite-copy" type="button" disabled>複製</button></span>
<p id="invite-copy-status" role="status" aria-live="polite"></p></section>
<section data-panel="quota"><h2>平台模型每月額度</h2><p>同一個額度池涵蓋母站客服助手與子站的起步模型（平台借給新站台、客戶尚未接上自己模型前使用）。單位為 token，每月（UTC）重算；串流中斷而無法得知實際用量時，以預估值計入。客戶在子站接上自己的模型後，不佔這個額度。停用為預設；不限額仍受系統安全速率與供應商限制。</p><form id="quota-form"><label>政策 <select id="policy">
<option value="disabled">停用（disabled）</option><option value="unlimited">無上限（unlimited）</option><option value="capped">每月上限（capped）</option></select></label>
<label>每月額度（僅 capped 使用）<input id="limit" type="number" min="0" max="1000000000000" step="1"></label>
<button id="quota-submit" type="submit">儲存政策</button></form></section>
<section data-panel="template"><h2>工作區樣板</h2><p>建站時放進客戶工作區的起始內容（只放一次；之後改選不會改動已建好的工作區，也不會覆蓋客戶改過的檔案）。所有站台都另有平台指引（回覆語言、起步模型、如何接上自己的模型）。樣板不含任何金鑰。</p>
<form id="template-form"><label>樣板 <select id="template"></select></label>
<button id="template-submit" type="submit">儲存樣板</button></form><p id="template-desc"></p></section>
<section id="site-job-section" data-panel="build" hidden><h2>建站任務</h2><p class="warning" id="site-job-note"></p>
<button id="site-job-submit" type="button">排入建站任務</button><p id="site-job-result"></p></section>
<section id="packages-section" data-panel="packages" hidden><h2>額外套件</h2>
<p>站台以一般使用者執行，自己無法安裝軟體。在這裡列出要裝的 Debian 套件（例如 <code>openssh-client curl python3 jq</code>），平台會為這個站台建一個專屬映像檔並重建容器：約一到數分鐘，期間站台暫停；工作區、對話與設定都不受影響。失敗會自動退回原本的容器。清空後儲存即回到標準映像檔。</p>
<form id="packages-form"><label>套件（以空白或換行分隔）<textarea id="packages-input" rows="3" spellcheck="false" autocomplete="off"></textarea></label>
<button id="packages-submit" type="submit" disabled>儲存並重建站台</button></form><p id="packages-result" role="status" aria-live="polite"></p></section>
<section id="danger-section" data-panel="danger" hidden><h2>刪除客戶</h2>
<p class="warning">會刪除容器、DNS 記錄、反向代理設定、站台資料與所有客戶紀錄（帳號、邀請、模型設定）。<strong>無法復原</strong>；客戶需重新邀請並重建站台。</p>
<label>請輸入要刪除的站台名稱以確認 <input id="delete-confirm" type="text" autocomplete="off" spellcheck="false"></label>
<button id="delete-submit" type="button" disabled>永久刪除此客戶</button><p id="delete-result"></p></section>
</div>

<div class="view" id="view-tasks" hidden>
<div class="page-head"><div><h1>任務庫</h1><p class="sub">寫好的任務可以交給任何客戶站台。</p></div></div>
<section id="tasks-section"><h2>任務範本</h2>
<p>在這裡寫好任務（prompt + 建議時間 + 由哪個角色執行），交給客戶站台。任務進到客戶站台後就是客戶的：客戶可以改、刪，這裡之後改範本也不會覆蓋。客戶站台的排程由平台執行器照時間在客戶自己的站台裡執行，使用站台的預設模型（起步模型時會計入平台額度）。</p>
<ul id="task-list"></ul>
<form id="task-form" autocomplete="off"><h3 id="task-form-title">新增任務範本</h3>
<label>任務名稱 <input id="task-title" type="text" maxlength="60" required placeholder="每日待辦整理"></label>
<label>任務代號（英文小寫） <input id="task-name" type="text" maxlength="63" required placeholder="daily_todo" spellcheck="false"></label>
<label>建議由誰執行（角色代號；站台沒有這個角色時改由 pm 或第一個角色） <input id="task-role" type="text" maxlength="63" required value="pm" spellcheck="false"></label>
<label>時間（cron，台北時間，星期用 mon…sun） <input id="task-cron" type="text" maxlength="64" required value="0 9 * * *" spellcheck="false"></label>
<p id="task-cron-hint"></p>
<label>skill（選填，站台 .dsh/skills 裡的名稱） <input id="task-skill" type="text" maxlength="63" spellcheck="false"></label>
<label for="task-context">要做什麼（prompt）</label>
<textarea id="task-context" rows="5" maxlength="2000" required></textarea>
<label><input id="task-default" type="checkbox"> 新站台預設帶入（建好站、客戶建立第一間公司後自動加入）</label>
<button id="task-save" type="submit">儲存範本</button> <button id="task-cancel" type="button">清空</button></form></section>
<section><h2>交給客戶</h2>
<label>客戶 <select id="task-tenant"></select></label>
<fieldset id="task-picks"><legend>要交付的任務</legend></fieldset>
<label>方式 <select id="task-mode"><option value="offer">客戶確認：出現在客戶設定頁，由客戶選公司後加入</option>
<option value="direct">直接加入：平台寫進客戶的第一間公司（還沒有公司就等客戶建好）</option></select></label>
<button id="task-push" type="button">交付</button>
<ul id="task-deliveries"></ul>
<p id="task-status" role="status" aria-live="polite"></p></section>
</div>

<div class="view" id="view-usage" hidden>
<div class="page-head"><div><h1>用量</h1><p class="sub">平台借給站台的起步模型用量（token）。客戶接上自己的模型後就不算在這裡。</p></div></div>
<section><table id="usage-table"><thead id="usage-head"></thead><tbody id="usage-body"></tbody></table></section>
</div>

<div class="view" id="view-settings" hidden>
<div class="page-head"><div><h1>設定</h1><p class="sub">新增客戶站台。</p></div></div>
<section><h2>新增客戶</h2>
<form id="bootstrap-form" hidden autocomplete="off"><p class="warning" id="bootstrap-note"></p>
<label for="bootstrap-host">客戶站台名稱（此刻不需存在）</label>
<span id="bootstrap-host-row"><input id="bootstrap-host" type="text" required placeholder="ab"
 autocapitalize="off" autocorrect="off" spellcheck="false"><span id="bootstrap-domain"></span></span>
<p id="bootstrap-preview"></p>
<button id="bootstrap-submit" type="submit">建立客戶</button></form>
<p class="muted">建立後到「站台管理」選它，排入建站任務，再產生邀請。</p></section>
</div>

<p id="status" role="status" aria-live="polite"></p></main></div>
<button id="chat-launcher" type="button" aria-expanded="false" aria-controls="chat-section">客戶對話<span id="chat-count" class="chat-badge waiting" hidden></span></button>
<aside id="chat-section" hidden aria-label="客戶對話">
<header><h2>客戶對話</h2><button id="chat-close" type="button" aria-label="收起">×</button></header>
<label for="chat-rooms">回覆哪位客戶</label><select id="chat-rooms"></select>
<p id="chat-overview-status" class="muted" role="status" aria-live="polite"></p>
<div id="chat-panel" hidden><h3 id="chat-title"></h3>
<p id="chat-room-mode" class="muted"></p>
<p id="chat-modes"><button type="button" data-room-mode="ai">AI 自動回覆</button><button type="button" data-room-mode="coassist">真人接手</button><button type="button" data-room-mode="human">只由真人</button></p>
<ol id="chat-thread"></ol>
<form id="chat-reply-form" autocomplete="off"><label for="chat-reply">回覆客戶（Enter 送出，Shift+Enter 換行）</label>
<textarea id="chat-reply" maxlength="4000" rows="3"></textarea>
<p class="chat-actions"><button id="chat-reply-send" type="submit">回覆客戶</button><button id="chat-assist" type="button" title="客戶看不到這段文字，只看到 AI 依指示寫的回覆">指示 AI 回覆</button></p></form>
<p id="chat-panel-status" role="status" aria-live="polite"></p></div>
<p class="muted chat-hint">「回覆客戶」：客戶看到、標示「平台人員」。「指示 AI 回覆」：這段文字只有你看得到，AI 依它寫一則回覆給客戶。模式只在你按上方按鈕時改變：AI 自動回覆＝客戶每句 AI 都回；真人接手＝AI 只在客戶按「請 AI 助手回答」時回；只由真人＝AI 不回。</p></aside>
</body></html>'''

SHELL_JS = r"""'use strict';
(() => {
  // Navigation, theme and the read-only overview. Text only (textContent).
  const byId = (id) => document.getElementById(id);
  const VIEWS = ['overview', 'site', 'tasks', 'usage', 'settings'];
  const JOB = {queued:'排隊中', running:'建立中', succeeded_provisioned:'運作中', succeeded_simulated:'模擬',
    failed:'建站失敗', reconciliation_required:'需人工確認'};
  let data = null;
  // 進入站台／看板連結（開新分頁、noopener）；只給合法網域。
  const HOST_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$/;
  function siteLink(href, text) {
    const a = document.createElement('a');
    a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = text;
    return a;
  }
  function actButton(href, text, title, primary) {
    const a = siteLink(href, text);
    a.className = primary ? 'act primary' : 'act'; a.title = title;
    return a;
  }
  function el(tag, text, cls) {
    const n = document.createElement(tag);
    if (text !== undefined && text !== null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }
  const fmt = (n) => Number(n || 0).toLocaleString('zh-TW');
  function ago(at) {
    if (!at) return '—';
    const s = Math.max(0, Date.now() / 1000 - at);
    if (s < 3600) return Math.round(s / 60) + ' 分鐘前';
    if (s < 86400) return Math.round(s / 3600) + ' 小時前';
    return Math.round(s / 86400) + ' 天前';
  }
  function show(view) {
    if (!VIEWS.includes(view)) view = 'overview';
    for (const v of VIEWS) byId('view-' + v).hidden = v !== view;
    for (const b of document.querySelectorAll('#side nav button')) {
      if (b.dataset.view === view) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    }
    if (view === 'overview' || view === 'usage') load();
  }
  let showTab = () => {};
  function openSite(id, wanted) {
    const select = byId('tenants');
    if (id && [...select.options].some((o) => o.value === id)) {
      select.value = id; select.dispatchEvent(new Event('change'));
    }
    show('site');
    if (wanted) showTab(wanted);
  }
  function pill(site) {
    if (site.online === true) return el('span', '在線', 'pill ok');
    if (site.online === false) return el('span', '沒有回應', 'pill bad');
    return el('span', JOB[site.site_job] || '尚未建站', 'pill warn');
  }
  function render() {
    if (!data) return;
    const t = data.totals;
    byId('stat-sites').textContent = fmt(t.sites);
    byId('stat-online').textContent = t.online + ' 個在線';
    byId('stat-tokens').textContent = fmt(t.tokens_month);
    byId('stat-attention').textContent = fmt(t.attention);
    byId('overview-sub').textContent = '各站台目前狀況與需要你處理的事（' + data.month + '）';
    const badge = byId('nav-attention');
    badge.textContent = t.attention ? String(t.attention) : ''; badge.hidden = !t.attention;
    const list = byId('attention-list'); list.replaceChildren();
    if (!data.attention.length) list.append(el('li', '目前沒有需要處理的事。', 'muted'));
    for (const a of data.attention) {
      const li = el('li'); li.append(el('span', null, 'dot'), el('span', a.host, 'host'), el('span', a.text));
      const go = el('button', a.kind === 'chat' ? '回覆' : '前往'); go.type = 'button';
      go.addEventListener('click', () => {
        if (a.kind === 'chat') byId('chat-launcher').click();
        else openSite(a.tenant_id, {site_job: 'build', quota: 'quota', invite: 'invite'}[a.kind]);
      });
      li.append(go); list.append(li);
    }
    const cards = byId('site-cards'); cards.replaceChildren();
    for (const s of data.sites) {
      const card = el('button', null, 'site-card'); card.type = 'button';
      card.append(el('span', s.host, 'name'), pill(s));
      const rows = el('div', null, 'rows');
      const add = (k, v) => rows.append(el('span', k), el('span', v));
      add('本月用量', fmt(s.usage_month.tokens) + ' token');
      add('額度', s.policy.mode === 'capped' ? fmt(s.policy.monthly_limit) + ' / 月'
        : s.policy.mode === 'unlimited' ? '不限' : '停用');
      add('客戶帳號', s.accounts ? s.accounts + ' 個' : '尚無（' + (s.open_invites ? '邀請有效中' : '未邀請') + '）');
      add('最近排程', s.last_run ? s.last_run.name + '・' + (RUN[s.last_run.status] || s.last_run.status) + '・' + ago(s.last_run.at) : '—');
      if (s.waiting) add('客服', s.waiting + ' 則等回覆');
      card.append(rows);
      if (s.policy.mode === 'capped' && s.policy.monthly_limit) {
        const bar = el('div', null, 'bar'); const fill = el('i');
        fill.style.width = Math.min(100, 100 * s.usage_month.tokens / s.policy.monthly_limit) + '%';
        bar.append(fill); card.append(bar);
      }
      card.addEventListener('click', () => openSite(s.id));
      // 進入站台／看板：卡片本身是按鈕（點了開站台管理），連結放在卡片下方，開新分頁。
      const wrap = el('div', null, 'site-card-wrap'); wrap.append(card);
      const host = String(s.host || '').toLowerCase();
      if (HOST_RE.test(host) && s.site_job === 'succeeded_provisioned') {
        // 卡片底部的動作按鈕：看板（主要）＋進入站台；都開新分頁。沒裝看板就只有進入站台。
        const links = el('div', null, 'site-card-actions');
        if (s.board === true) links.append(actButton('https://' + host + '/board/', '📊 看板', '開這個站台的看板（新分頁）', true));
        links.append(actButton('https://' + host + '/', '進入站台 ↗', '開這個站台的 AI 對話（新分頁）', s.board !== true));
        wrap.append(links);
      }
      cards.append(wrap);
    }
    const months = [...new Set(data.sites.flatMap((s) => s.usage_history.map((h) => h.month)))].sort().slice(-6);
    const head = byId('usage-head'); head.replaceChildren();
    const hr = el('tr'); hr.append(el('th', '站台')); for (const m of months) hr.append(el('th', m));
    head.append(hr);
    const body = byId('usage-body'); body.replaceChildren();
    for (const s of data.sites) {
      const tr = el('tr'); tr.append(el('td', s.host));
      for (const m of months) {
        const h = s.usage_history.find((x) => x.month === m);
        tr.append(el('td', h ? fmt(h.tokens) : '—', 'num'));
      }
      body.append(tr);
    }
    if (!data.sites.length) { const tr = el('tr'); tr.append(el('td', '還沒有站台。')); body.append(tr); }
  }
  function renderTrend() {
    const box = byId('trend'); box.replaceChildren();
    const legend = byId('trend-legend'); legend.replaceChildren();
    const order = data.sites.map((s) => s.id);
    const color = (id) => 'c' + (Math.max(0, order.indexOf(id)) % 6);
    const days = [];
    const today = new Date(data.generated_at * 1000 + 8 * 3600 * 1000);
    for (let i = 29; i >= 0; i--) days.push(new Date(today - i * 86400000).toISOString().slice(0, 10));
    const by = {};
    for (const d of data.daily) (by[d.day] = by[d.day] || []).push(d);
    const max = Math.max(1, ...days.map((d) => (by[d] || []).reduce((a, x) => a + x.tokens, 0)));
    for (const day of days) {
      const col = el('div', null, 'col');
      const parts = by[day] || [];
      const total = parts.reduce((a, x) => a + x.tokens, 0);
      col.title = day + '：' + fmt(total) + ' token' + parts.map((x) => '\n' + hostOf(x.tenant_id) + ' ' + fmt(x.tokens)).join('');
      for (const x of parts) { const seg = el('i', null, color(x.tenant_id)); seg.style.height = (100 * x.tokens / max) + '%'; col.append(seg); }
      box.append(col);
    }
    const axis = byId('trend-axis') || el('div', null, 'trend-axis'); axis.id = 'trend-axis'; axis.replaceChildren(
      el('span', days[0].slice(5)), el('span', '最高 ' + fmt(max) + ' / 天'), el('span', days[29].slice(5)));
    box.after(axis);
    for (const s of data.sites) legend.append(el('span', s.host, color(s.id)));
    if (!data.daily.length) legend.append(el('span', '近 30 天沒有使用平台模型。'));
  }
  const hostOf = (id) => (data.sites.find((s) => s.id === id) || {}).host || '';
  const RUN = {succeeded: '成功', failed: '失敗', running: '執行中', skipped: '略過'};
  function renderRuns() {
    const list = byId('run-list'); list.replaceChildren();
    if (!data.recent_runs.length) list.append(el('li', '還沒有排程執行紀錄。', 'muted'));
    for (const r of data.recent_runs) {
      const li = el('li');
      const cls = r.status === 'succeeded' ? 'pill ok' : r.status === 'failed' ? 'pill bad' : 'pill warn';
      li.append(el('span', RUN[r.status] || r.status, cls), el('strong', r.name),
        el('span', r.host + '・' + r.hive + '・' + (r.trigger === 'manual' ? '手動' : '排程') + '・' + ago(r.started_at), 'meta'));
      list.append(li);
    }
  }
  async function load() {
    try {
      const r = await fetch('/admin/overview', {credentials: 'same-origin', cache: 'no-store', redirect: 'error'});
      if (!r.ok) throw new Error(String(r.status));
      data = await r.json(); render(); renderTrend(); renderRuns();
    } catch (_) {
      byId('overview-sub').textContent = '總覽載入失敗，請重新整理。';
    }
  }
  for (const b of document.querySelectorAll('#side nav button')) b.addEventListener('click', () => show(b.dataset.view));
  // Site page tabs. Sections keep their own `hidden` (management.js decides
  // whether build/delete apply); tabs only add a class on top of that.
  const tabs = [...document.querySelectorAll('.tabs button')];
  let tab = 'invite';
  showTab = function (name) {
    const usable = (t) => { const p = document.querySelector('section[data-panel="' + t.dataset.tab + '"]'); return p && !p.hidden; };
    for (const t of tabs) t.classList.toggle('tab-gone', !usable(t));
    if (!tabs.some((t) => t.dataset.tab === name && usable(t))) name = 'invite';
    tab = name;
    for (const t of tabs) t.setAttribute('aria-selected', t.dataset.tab === name ? 'true' : 'false');
    for (const p of document.querySelectorAll('section[data-panel]')) p.classList.toggle('tab-off', p.dataset.panel !== name);
  };
  for (const t of tabs) t.addEventListener('click', () => showTab(t.dataset.tab));
  new MutationObserver(() => showTab(tab)).observe(byId('view-site'), {subtree: true, attributes: true, attributeFilter: ['hidden']});
  showTab('invite');
  byId('overview-refresh').addEventListener('click', load);
  const root = document.documentElement;
  const dark = () => root.dataset.theme ? root.dataset.theme === 'dark'
    : matchMedia('(prefers-color-scheme: dark)').matches;
  byId('theme-toggle').addEventListener('click', () => { root.dataset.theme = dark() ? 'light' : 'dark'; });
  show('overview');
  setInterval(() => { if (!document.hidden && !byId('view-overview').hidden) load(); }, 60000);
})();
"""

MANAGEMENT_JS = r"""'use strict';
(() => {
  const byId = (id) => document.getElementById(id);
  const status = byId('status');
  let rows = [], current = null, generation = 0, busy = false, leaving = false;
  let templates = [];
  let packagesReady = false;
  // Mode comes from a server-rendered attribute, never from user input.
  // 'loopback'  = isolated fixture, simulated executor
  // 'live'      = real deployment: queuing a job builds REAL infrastructure
  const mode = document.body.dataset.mode || '';
  const fixture = mode === 'loopback';
  const live = mode === 'live';
  const manage = fixture || live;
  const inviteOrigin = document.body.dataset.inviteOrigin || null;
  // The operator types only the site name; the domain is fixed by deployment
  // and is never something they should have to retype (or get wrong).
  const SITE_DOMAIN = document.body.dataset.siteDomain || '';
  byId('bootstrap-form').hidden = !manage;
  byId('site-job-section').hidden = !manage;
  byId('danger-section').hidden = !manage;
  if (live) byId('page-note').textContent = '新客戶流程：①建立客戶 → ②確認「選擇租戶」是這位客戶 → ③排入建站任務（真的建立站台）→ ④產生邀請並安全傳給客戶。下方每個按鈕都只作用在「選擇租戶」選到的那一位。';
  byId('bootstrap-domain').textContent = SITE_DOMAIN ? '.' + SITE_DOMAIN : '';
  function previewHost() {
    const raw = byId('bootstrap-host').value.trim();
    if (!raw) { byId('bootstrap-preview').textContent = ''; return; }
    try {
      byId('bootstrap-preview').textContent = '將建立：https://' + checkSiteHost(raw) + '/';
    } catch (error) { byId('bootstrap-preview').textContent = error.message; }
  }
  byId('bootstrap-host').addEventListener('input', previewHost);
  byId('bootstrap-note').textContent = live
    ? '只建立客戶中繼資料與客服房間，不建立站台；站台要另外排建站任務。'
    : '隔離 fixture 專用：只建立中繼資料，不建立站台。';
  byId('site-job-note').textContent = live
    ? '會真的建立容器、DNS 記錄與反向代理設定，約需數分鐘。失敗不會自動重試，需人工確認。'
    : '只把意圖寫入佇列，由 fixture 的模擬執行器標為「模擬完成」，不建立任何真實資源。';
  if (fixture) byId('bootstrap-host').value = 'localhost';
  previewHost();
  function clearInvite() {
    byId('invite-link').value = '';
    byId('invite-copy').disabled = true;
    byId('invite-copy').textContent = '複製';
    byId('invite-copy-status').textContent = '';
  }
  byId('invite-copy').addEventListener('click', async () => {
    const value = byId('invite-link').value;
    if (!value) return;
    const done = (message) => { byId('invite-copy-status').textContent = message; };
    try {
      // clipboard API needs a secure context; fall back to selecting the text
      // so the operator can copy manually rather than silently failing.
      if (!navigator.clipboard) throw new Error('unavailable');
      await navigator.clipboard.writeText(value);
      byId('invite-copy').textContent = '已複製';
      done('已複製到剪貼簿。連結只顯示一次，請立即安全傳給客戶。');
    } catch (_) {
      byId('invite-link').focus();
      byId('invite-link').select();
      done('無法自動複製，已為你選取，請用鍵盤複製。');
    }
  });
  function controls() {
    byId('invite-submit').disabled = busy || !current || !current.enabled || leaving;
    byId('quota-submit').disabled = busy || !current || leaving;
    byId('template-submit').disabled = busy || !current || leaving || !templates.length;
    byId('refresh').disabled = busy || leaving;
    byId('limit').disabled = byId('policy').value !== 'capped';
    byId('bootstrap-submit').disabled = busy || leaving || !manage;
    const target = current ? current.host : '';
    // A tenant that already has (or is getting) a site cannot be queued again:
    // that click could only ever mean the operator meant another customer.
    const built = Boolean(current && ['succeeded_provisioned', 'queued', 'running'].includes(current.site_job));
    byId('invite-submit').textContent = target ? '產生 ' + target + ' 的邀請' : '產生邀請';
    byId('site-job-submit').textContent = !target ? '排入建站任務'
      : built ? target + ' ' + jobText(current.site_job) : '為 ' + target + ' 排入建站任務';
    byId('site-job-submit').disabled = busy || leaving || !manage || !current || !current.enabled || built;
    // Typing the exact site name is the deliberate act; a disabled button until
    // then prevents deleting the wrong tenant by reflex.
    const typed = byId('delete-confirm').value.trim().toLowerCase();
    byId('packages-submit').disabled = busy || leaving || !current || !packagesReady;
    byId('delete-submit').disabled = busy || leaving || !manage || !current ||
      typed !== String(current.host || '').toLowerCase();
  }
  const JOB_TEXT = {queued:'已排入，等待執行', running:'建立中', succeeded_provisioned:'已完成',
    succeeded_simulated:'模擬完成（非真實站台）', failed:'失敗，需人工處理',
    reconciliation_required:'未完成，需人工確認後才能重試'};
  function jobText(status) { return JOB_TEXT[status] || status; }
  // Identifiers are long and rarely useful at a glance; lead with the host and
  // the build state, and keep the id available but short.
  function shortId(id) {
    const value = String(id || '');
    return value.length > 20 ? value.slice(0, 8) + '…' + value.slice(-6) : value;
  }
  function describe(row) {
    const parts = [row.host, row.enabled ? '啟用' : '停用',
                   '建站：' + (row.site_job ? jobText(row.site_job) : '尚未建立')];
    if (row.policy.mode !== 'disabled') {
      parts.push('客服額度：' + row.policy.mode +
                 (row.policy.mode === 'capped' ? ' / ' + row.policy.monthly_limit : ''));
    }
    const tpl = templates.find((t) => t.id === row.template);
    parts.push('樣板：' + (tpl ? tpl.name : String(row.template || '')));
    parts.push('id ' + shortId(row.id));
    return parts.join('　|　');
  }
  // One renderer for the detail pane: a second, shorter version had drifted in
  // after a quota save, so the pane silently lost information depending on path.
  function detailText(row) {
    return describe(row) + '\n完整 id：' + row.id +
      '\n客戶啟用網址：' + (row.platform_host || row.host);
  }
  // 進入站台／看板：只對已建好的站台、而且 host 是合法網域時才給連結（開新分頁、noopener）。
  const HOST_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$/;
  function siteLink(href, text) {
    const a = document.createElement('a');
    a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = text;
    return a;
  }
  function renderSiteLinks(row) {
    const box = byId('site-links');
    if (!box) return;
    box.replaceChildren();
    const host = row && String(row.host || '').toLowerCase();
    // site_job is the job status string ("succeeded_provisioned" once built).
    const job = row && (typeof row.site_job === 'string' ? row.site_job : row.site_job && row.site_job.status);
    if (!host || !HOST_RE.test(host) || job !== 'succeeded_provisioned') return;
    box.append(siteLink('https://' + host + '/', '進入站台 ↗'));
    // The board plugin is optional: link it only where the site has it.
    if (row.board === true) box.append(siteLink('https://' + host + '/board/', '看板 ↗'));
  }
  function renderList() {
    byId('tenant-list').replaceChildren();
    for (const row of rows) {
      const item = document.createElement('li');
      item.textContent = describe(row);
      byId('tenant-list').append(item);
    }
  }
  function select() {
    generation++; clearInvite(); status.textContent = '';
    // Never carry a typed confirmation across a tenant switch.
    byId('delete-confirm').value = '';
    byId('delete-result').textContent = '';
    // The queue-time message goes stale once the job runs; the button and the
    // detail line show the live state after every reload.
    byId('site-job-result').textContent = '';
    current = rows.find((row) => row.id === byId('tenants').value) || null;
    byId('tenant-detail').textContent = current ? detailText(current) : '沒有可用租戶。';
    renderSiteLinks(current);
    byId('policy').value = current ? current.policy.mode : 'disabled';
    if (current) byId('template').value = current.template;
    templateDesc();
    byId('limit').value = current && current.policy.monthly_limit !== null ? String(current.policy.monthly_limit) : '';
    loadPackages();
    loadAccounts();
    controls();
  }
  // Only provisioned sites have packages; the tab stays hidden otherwise
  // (and on deployments where the route is not wired).
  async function loadPackages() {
    packagesReady = false; byId('packages-input').value = ''; byId('packages-result').textContent = '';
    byId('packages-section').hidden = true;
    const row = current, epoch = generation;
    if (!row || row.site_job !== 'succeeded_provisioned') return;
    try {
      const data = await api('/' + encodeURIComponent(row.id) + '/packages');
      if (leaving || epoch !== generation || !Array.isArray(data.packages)) return;
      byId('packages-input').value = data.packages.join(' ');
      byId('packages-section').hidden = false; packagesReady = true;
    } catch (_) { /* route absent or site unavailable: keep the tab hidden */ }
    controls();
  }
  async function api(path, body) {
    const options = {credentials: 'same-origin', cache: 'no-store', redirect: 'error'};
    if (body !== undefined) {
      options.method = 'POST'; options.headers = {'Content-Type': 'application/json'};
      options.body = JSON.stringify(body);
    }
    const response = await fetch('/admin/tenants' + path, options);
    if (!response.ok) {
      let message = 'request failed';
      try { const e = await response.json(); if (e && e.error === 'refused' && typeof e.message === 'string') message = e.message; } catch (_) {}
      throw new Error(message);
    }
    return response.json();
  }
  async function task(action, failure) {
    if (busy || leaving) return;
    busy = true; status.textContent = ''; controls();
    const epoch = generation;
    try { await action(epoch); }
    catch (_) { if (!leaving && epoch === generation) status.textContent = failure; }
    finally { busy = false; controls(); }
  }
  function integer(id, min, max) {
    const raw = byId(id).value;
    const value = Number(raw);
    if (!/^\d+$/.test(raw) || !Number.isSafeInteger(value) || value < min || value > max) throw new Error('invalid integer');
    return value;
  }
  // Reject names provisioning would refuse later, so the operator finds out
  // here instead of after a failed build that needs reconciliation.
  const RESERVED = new Set(['admin','api','app','auth','cdn','dashboard','dev','docs','ftp',
    'hub','imap','login','mail','mx','ns','ns1','ns2','pop','prod','root','smtp','ssh','stage',
    'staging','static','status','swarm','test','vpn','www']);
  function checkSiteHost(value) {
    let label = String(value || '').trim().toLowerCase();
    if (!label) throw new Error('請輸入站台名稱');
    // Accept a pasted full domain too, rather than rejecting a reasonable input.
    if (SITE_DOMAIN && label.endsWith('.' + SITE_DOMAIN)) {
      label = label.slice(0, -(SITE_DOMAIN.length + 1));
    }
    if (label.includes('.')) throw new Error('只要填名稱，不用加 .' + SITE_DOMAIN);
    if (!/^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/.test(label)) {
      throw new Error('名稱只能用小寫英數與 -，且不可用 - 開頭或結尾');
    }
    if (RESERVED.has(label)) throw new Error('「' + label + '」是保留字，請換一個名稱');
    if (label.startsWith('dev-')) throw new Error('dev- 前綴保留給其他用途');
    if (!SITE_DOMAIN) throw new Error('此部署未設定站台網域，無法建立客戶');
    return label + '.' + SITE_DOMAIN;
  }

  function hostForInvite(host) {
    // Do not let malformed metadata turn a displayed URL into another authority.
    if (typeof host !== 'string' || host.length > 253 || !host.split('.').every(
      (label) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label))) throw new Error('invalid host');
    return host;
  }
  function templateDesc() {
    const tpl = templates.find((t) => t.id === byId('template').value);
    byId('template-desc').textContent = tpl ? tpl.description + (tpl.version ? '（版本 ' + tpl.version + '）' : '') : '';
  }
  async function loadTemplates() {
    const response = await fetch('/admin/site-templates', {credentials: 'same-origin', cache: 'no-store', redirect: 'error'});
    if (!response.ok) throw new Error('request failed');
    const data = await response.json();
    if (!Array.isArray(data.templates)) throw new Error('invalid response');
    templates = data.templates;
    byId('template').replaceChildren();
    for (const t of templates) {
      const option = document.createElement('option');
      option.value = t.id; option.textContent = t.name + (t.id === data.default ? '（預設）' : '');
      byId('template').append(option);
    }
  }
  async function list(selectId) {
    if (!templates.length) { try { await loadTemplates(); } catch (_) { templates = []; } }
    generation++; clearInvite(); current = null; rows = [];
    byId('tenants').replaceChildren(); byId('tenant-list').replaceChildren();
    byId('tenant-detail').textContent = ''; if (byId('site-links')) byId('site-links').replaceChildren(); controls();
    const epoch = generation;
    try {
      const data = await api('');
      if (leaving || epoch !== generation) return;
      rows = data;
      for (const row of rows) {
        const option = document.createElement('option'); option.value = row.id;
        option.textContent = row.host + '（' + shortId(row.id) + '）';
        byId('tenants').append(option);
      }
      renderList();
      byId('tenants').value = rows.some((r) => r.id === selectId) ? selectId : (rows.length ? rows[0].id : ''); select();
    } catch (_) { if (!leaving && epoch === generation) status.textContent = '載入失敗，請確認管理員授權後手動重新載入。'; }
  }
  byId('tenants').addEventListener('change', select);
  byId('policy').addEventListener('change', controls);
  byId('template').addEventListener('change', templateDesc);
  byId('template-form').addEventListener('submit', (event) => {
    event.preventDefault();
    return task(async (epoch) => {
      if (!current) throw new Error('select tenant');
      const row = current, template = byId('template').value;
      if (!templates.some((t) => t.id === template)) throw new Error('invalid template');
      const result = await api('/' + encodeURIComponent(row.id) + '/template', {template});
      if (leaving || epoch !== generation) return;
      if (result.template !== template) throw new Error('invalid response');
      row.template = template; renderList();
      byId('tenant-detail').textContent = detailText(row);
      status.textContent = row.site_job ? '樣板已儲存；已建好的工作區不會變動，重建站台時才會套用。' : '樣板已儲存，建站時套用。';
    }, '樣板未確認成功，請重新載入確認。');
  });
  byId('refresh').addEventListener('click', () => task(list, '載入失敗。'));
  // Accounts and password reset links (swarm#580). The link is shown once, like an invite.
  async function loadAccounts() {
    const box = byId('accounts-box'); const body = byId('accounts-body');
    if (!box || !body || !current) { if (box) box.hidden = true; return; }
    byId('reset-link').value = ''; byId('reset-copy').disabled = true;
    let d; try { d = await api('/' + encodeURIComponent(current.id) + '/accounts'); } catch (e) { box.hidden = true; return; }
    body.replaceChildren();
    for (const a of (d.accounts || [])) {
      const tr = document.createElement('tr');
      const td = (t) => { const x = document.createElement('td'); x.textContent = t; return x; };
      const btn = document.createElement('button'); btn.type = 'button'; btn.textContent = '產生重設連結';
      btn.addEventListener('click', () => task(async () => {
        if (!confirm('為帳號「' + a.username + '」產生重設密碼連結？連結只顯示一次，請私訊給本人。')) return;
        const ttl = integer('ttl', 60, 86400);
        let r = await api('/' + encodeURIComponent(current.id) + '/password-resets', {account_id: a.id, ttl_seconds: ttl});
        try {
          if (typeof r.token !== 'string' || !r.token) throw new Error('missing token');
          const base = inviteOrigin || ('https://' + hostForInvite(current.platform_host || current.host));
          byId('reset-link').value = base + '/?reset=' + encodeURIComponent(r.token);
          byId('reset-copy').disabled = false;
          status.textContent = '已產生「' + a.username + '」的重設連結，請私訊給本人（不要貼在公開頻道）。';
        } finally { if (r) r.token = ''; r = null; }
      }, '重設連結未產生成功。'));
      const last = a.last_reset_at ? new Date(a.last_reset_at * 1000).toLocaleString('zh-TW', {hour12: false}) : '—';
      const act = document.createElement('td'); act.append(btn);
      tr.append(td(a.username), td(last), act); body.append(tr);
    }
    box.hidden = !(d.accounts || []).length;
  }
  const resetCopy = byId('reset-copy');
  if (resetCopy) resetCopy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(byId('reset-link').value); status.textContent = '已複製重設連結。'; } catch (e) {}
  });
  addEventListener('pagehide', () => { const l = byId('reset-link'); if (l) l.value = ''; });
  byId('invite-form').addEventListener('submit', (event) => {
    event.preventDefault();
    if (busy || leaving) return;
    clearInvite();
    return task(async (epoch) => {
      if (!current || !current.enabled) throw new Error('select tenant');
      const row = current;
      const ttl = integer('ttl', 60, 86400);
      let result = await api('/' + encodeURIComponent(row.id) + '/invites', {ttl_seconds: ttl});
      try {
        if (leaving || epoch !== generation) return;
        if (typeof result.token !== 'string' || !result.token) throw new Error('missing token');
        // Invites are redeemed on the PLATFORM host: the customer's own site
        // may not exist yet. Never build the link from the site host.
        const platform = hostForInvite(row.platform_host || row.host);
        // Prefer the server-declared invite origin so the link matches the
        // authority customers actually reach (loopback port, or the real host).
        const base = inviteOrigin || ('https://' + platform);
        byId('invite-link').value = base + '/?token=' + encodeURIComponent(result.token);
        byId('invite-copy').disabled = false;
        status.textContent = '邀請已產生，請安全傳遞上方連結。';
      } finally { if (result) result.token = ''; result = null; }
    }, '邀請未確認成功。請檢查有效秒數或管理員授權；請勿盲目重試，邀請可能已發出。');
  });
  byId('bootstrap-form').addEventListener('submit', (event) => {
    event.preventDefault();
    if (!manage) return;
    return task(async (epoch) => {
      const host = checkSiteHost(byId('bootstrap-host').value);
      const result = await api('', {client_request_id: 'admin-' + crypto.randomUUID(), host, room_mode: 'ai', policy: {mode: 'disabled'}});
      if (leaving || epoch !== generation) return;
      if (!result.tenant || result.tenant.host !== host) throw new Error('invalid response');
      // Select the tenant just created: every action below targets the selected
      // tenant, so leaving the previous one selected sent invites and build jobs
      // to the wrong customer.
      await list(result.tenant.id);
      byId('bootstrap-host').value = ''; previewHost();
      status.textContent = '已建立 ' + host + '，並已選取。下一步：排入建站任務，再產生邀請。';
    }, '建立測試客戶未確認成功；可能主機名已存在。請重新載入清單，不要盲目重試。');
  });
  byId('site-job-submit').addEventListener('click', () => {
    if (!manage) return;
    if (live && !confirm('這會真的建立容器、DNS 記錄與代理設定。確定要為 ' +
        (current ? current.host : '') + ' 建站？')) return;
    return task(async (epoch) => {
      if (!current || !current.enabled) throw new Error('select tenant');
      const row = current;
      const result = await api('/' + encodeURIComponent(row.id) + '/site-jobs', {client_request_id: 'admin-job-' + crypto.randomUUID()});
      if (leaving || epoch !== generation) return;
      if (!result.job || typeof result.job.status !== 'string') throw new Error('invalid response');
      const done = '任務狀態：' + jobText(result.job.status) + '（' + row.host + '）';
      await list(row.id);
      byId('site-job-result').textContent = done;
      status.textContent = live
        ? '建站任務已排入；執行器會在數分鐘內建立真實站台。請用「重新載入」查看進度。'
        : '建站意圖已排入佇列；fixture 模擬執行器會標為模擬完成，並非真實建站。';
    }, '排入建站任務未確認成功；請重新載入，不要盲目重試。');
  });
  byId('packages-form').addEventListener('submit', (event) => {
    event.preventDefault();
    if (!current || !packagesReady) return;
    const row = current;
    const packages = byId('packages-input').value.split(/\s+/).filter(Boolean);
    if (packages.some((p) => !/^[a-z0-9][a-z0-9+.-]{0,62}$/.test(p))) {
      byId('packages-result').textContent = '套件名稱只能用小寫英數與 + . -（例如 openssh-client）。'; return;
    }
    if (!confirm('會重建 ' + row.host + ' 的容器，站台暫停約一到數分鐘。繼續？')) return;
    byId('packages-result').textContent = '建置中，請勿關閉頁面…';
    return task(async (epoch) => {
      let result;
      try { result = await api('/' + encodeURIComponent(row.id) + '/packages', {packages}); }
      catch (error) { byId('packages-result').textContent = '未完成，站台已退回原本的容器。'; throw error; }
      if (leaving || epoch !== generation) return;
      byId('packages-input').value = result.packages.join(' ');
      byId('packages-result').textContent = result.packages.length
        ? '完成：' + row.host + ' 已安裝 ' + result.packages.join('、') + '。'
        : '完成：' + row.host + ' 已回到標準映像檔。';
    }, '套件變更未完成（可能是套件名稱不存在或建置失敗），站台已退回原本的容器。');
  });
  byId('delete-confirm').addEventListener('input', controls);
  byId('delete-submit').addEventListener('click', () => {
    if (!manage || !current) return;
    const row = current;
    if (!confirm('確定永久刪除 ' + row.host + '？\n\n會移除站台、DNS、代理設定與所有客戶紀錄，無法復原。')) return;
    return task(async (epoch) => {
      const result = await api('/' + encodeURIComponent(row.id) + '/delete',
                               {expected_host: row.host});
      if (leaving || epoch !== generation) return;
      byId('delete-result').textContent = '已刪除 ' + result.site_host +
        (result.site_removed ? '（含站台 ' + result.site_removed + '）' : '（先前未建立站台）');
      byId('delete-confirm').value = '';
      status.textContent = '客戶已刪除。';
      await list();
    }, '刪除未完成。站台可能仍存在或部分資源未移除，請重新載入確認，不要盲目重試。');
  });
  byId('quota-form').addEventListener('submit', (event) => {
    event.preventDefault();
    return task(async (epoch) => {
      if (!current) throw new Error('select tenant');
      const row = current, mode = byId('policy').value;
      if (!['disabled', 'unlimited', 'capped'].includes(mode)) throw new Error('invalid mode');
      const policy = {mode};
      if (mode === 'capped') policy.monthly_limit = integer('limit', 0, 1000000000000);
      const result = await api('/' + encodeURIComponent(row.id) + '/quota', {policy});
      if (leaving || epoch !== generation) return;
      if (!result.policy || result.policy.mode !== mode ||
          (mode === 'capped' && result.policy.monthly_limit !== policy.monthly_limit)) throw new Error('invalid response');
      row.policy = result.policy; renderList();
      byId('tenant-detail').textContent = detailText(row);
      status.textContent = '額度政策已儲存（僅中繼資料，不部署服務）。';
    }, '額度政策未確認成功，請重新載入確認；未顯示為已儲存。');
  });
  addEventListener('pagehide', () => {
    leaving = true; generation++; clearInvite(); status.textContent = ''; controls();
  });
  addEventListener('pageshow', (event) => {
    if (event.persisted) { leaving = false; clearInvite(); controls(); }
  });
  task(list, '載入失敗。');
})();
"""


TASKS_JS = r"""'use strict';
(() => {
  const byId = (id) => document.getElementById(id);
  const say = (text) => { byId('task-status').textContent = text; };
  let items = [], editing = '', tenants = [];
  const STATUS = {pending: '等待中', applied: '已加入', dismissed: '客戶略過', failed: '失敗'};
  const DOW = {mon:'一',tue:'二',wed:'三',thu:'四',fri:'五',sat:'六',sun:'日'};
  function describeCron(cron) {
    const p = String(cron || '').trim().split(/\s+/);
    if (p.length !== 5) return '';
    const [m, h, dom, mon, dow] = p;
    if (!/^\d+$/.test(m)) return '自訂：' + cron;
    const t = /^\d+$/.test(h) ? String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') : null;
    if (h === '*' && dom === '*' && mon === '*' && dow === '*') return '每小時第 ' + m + ' 分';
    if (!t || dom !== '*' || mon !== '*') return '自訂：' + cron;
    if (dow === '*') return '每天 ' + t;
    if (dow === 'mon-fri') return '平日 ' + t;
    const days = dow.split(',');
    if (days.every((d) => DOW[d])) return '每週' + days.map((d) => DOW[d]).join('、') + ' ' + t;
    return '自訂：' + cron;
  }
  async function call(path, body) {
    const options = {credentials: 'same-origin', cache: 'no-store', redirect: 'error'};
    if (body !== undefined) {
      options.method = 'POST'; options.headers = {'Content-Type': 'application/json'};
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    let data = null;
    try { data = await response.json(); } catch (_) { data = null; }
    if (!response.ok) throw new Error(data && data.message ? data.message : '操作失敗（' + response.status + '）');
    return data;
  }
  function el(tag, text) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; return n; }
  function render() {
    const list = byId('task-list'); list.replaceChildren();
    if (!items.length) list.append(el('li', '還沒有任務範本。'));
    for (const t of items) {
      const li = el('li');
      li.append(el('strong', t.title), document.createTextNode('　' + describeCron(t.cron) + '　由 ' + t.role +
        (t.skill ? '　skill ' + t.skill : '') + (t.is_default ? '　［新站台預設］' : '')));
      const edit = el('button', '編輯'); edit.type = 'button';
      edit.addEventListener('click', () => fill(t));
      const del = el('button', '刪除'); del.type = 'button';
      del.addEventListener('click', async () => {
        if (!confirm('刪除任務範本「' + t.title + '」？已交給客戶的不受影響。')) return;
        try { items = (await call('/admin/tasks/delete', {name: t.name})).templates; render(); say('已刪除。'); }
        catch (e) { say(e.message); }
      });
      li.append(edit, del);
      const p = el('div', t.context); p.className = 'muted'; li.append(p);
      list.append(li);
    }
    const picks = byId('task-picks');
    picks.replaceChildren(el('legend', '要交付的任務'));
    for (const t of items) {
      const label = el('label'); const box = el('input'); box.type = 'checkbox'; box.value = t.name;
      label.append(box, document.createTextNode(' ' + t.title)); picks.append(label);
    }
  }
  function fill(t) {
    editing = t ? t.name : '';
    byId('task-form-title').textContent = t ? '編輯任務範本：' + t.title : '新增任務範本';
    byId('task-title').value = t ? t.title : '';
    byId('task-name').value = t ? t.name : '';
    byId('task-role').value = t ? t.role : 'pm';
    byId('task-cron').value = t ? t.cron : '0 9 * * *';
    byId('task-skill').value = t ? t.skill : '';
    byId('task-context').value = t ? t.context : '';
    byId('task-default').checked = t ? t.is_default : false;
    hint();
  }
  function hint() { byId('task-cron-hint').textContent = describeCron(byId('task-cron').value) || '格式：分 時 日 月 星期'; }
  async function deliveries() {
    const list = byId('task-deliveries'); list.replaceChildren();
    const id = byId('task-tenant').value;
    if (!id) return;
    try {
      const data = await call('/admin/tenants/' + encodeURIComponent(id) + '/tasks');
      if (!data.deliveries.length) list.append(el('li', '這個客戶還沒有收到任務。'));
      for (const d of data.deliveries) {
        list.append(el('li', d.title + '　' + (d.mode === 'direct' ? '直接加入' : '客戶確認') + '　' +
          (STATUS[d.status] || d.status) + (d.hive ? '（' + d.hive + '）' : '') + (d.message ? '：' + d.message : '')));
      }
    } catch (e) { list.append(el('li', e.message)); }
  }
  byId('task-cron').addEventListener('input', hint);
  byId('task-cancel').addEventListener('click', () => fill(null));
  byId('task-tenant').addEventListener('change', deliveries);
  byId('task-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const body = {original: editing, name: byId('task-name').value.trim(), title: byId('task-title').value.trim(),
      role: byId('task-role').value.trim(), cron: byId('task-cron').value.trim().replace(/\s+/g, ' '),
      skill: byId('task-skill').value.trim(), context: byId('task-context').value.trim(),
      is_default: byId('task-default').checked};
    try { items = (await call('/admin/tasks', body)).templates; render(); fill(null); say('已儲存。'); }
    catch (e) { say(e.message); }
  });
  byId('task-push').addEventListener('click', async () => {
    const id = byId('task-tenant').value;
    const names = Array.from(byId('task-picks').querySelectorAll('input:checked')).map((b) => b.value);
    if (!id || !names.length) { say('請選客戶與至少一個任務。'); return; }
    const mode = byId('task-mode').value;
    const host = (tenants.find((t) => t.id === id) || {}).host || '';
    if (mode === 'direct' && !confirm('直接寫進 ' + host + ' 的排程？（客戶之後可以自己改或刪）')) return;
    try {
      await call('/admin/tenants/' + encodeURIComponent(id) + '/tasks', {names, mode});
      say(mode === 'direct' ? '已交付，平台會在一分鐘內寫進站台。' : '已交付，客戶會在設定頁看到。');
      await deliveries();
    } catch (e) { say(e.message); }
  });
  (async () => {
    try {
      items = (await call('/admin/tasks')).templates; render(); fill(null);
      tenants = await call('/admin/tenants');
      for (const t of tenants) { const o = el('option', t.host); o.value = t.id; byId('task-tenant').append(o); }
      await deliveries();
    } catch (e) { say('任務庫載入失敗：' + e.message); }
  })();
})();
"""


CHAT_JS = r"""'use strict';
(() => {
  // Operator popup (bottom-right) over customer Copilot rooms: pick a room, read, reply, set mode.
  // Text only (textContent); never HTML from a customer.
  const byId = (id) => document.getElementById(id);
  const KIND = {customer: '客戶', ai: 'AI 助手', human: '平台人員'};
  const MODE = {ai: 'AI 自動回覆（你也可以隨時插話）', coassist: '真人接手（AI 只在客戶要求時回覆）', human: '只由真人（AI 不回覆）'};
  let overview = [], current = null, room = null, sending = false, pending = null;
  let overviewTimer = null, threadTimer = null;
  function el(tag, text, cls) {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }
  function when(at) {
    if (!at) return '';
    const d = new Date(at * 1000);
    const pad = (x) => String(x).padStart(2, '0');
    return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  async function call(path, body) {
    const options = {credentials: 'same-origin', cache: 'no-store', redirect: 'error'};
    if (body !== undefined) {
      options.method = 'POST'; options.headers = {'Content-Type': 'application/json'};
      options.body = JSON.stringify(body);
    }
    const response = await fetch('/admin/' + path, options);
    if (!response.ok) {
      const error = new Error(response.status === 409 ? '對話狀態剛改變（或 AI 正在回覆），已重新載入，請再試一次。'
        : response.status === 503 ? 'AI 助手目前無法使用。'
        : response.status === 401 ? '登入已失效，請重新登入。' : '操作失敗（' + response.status + '）。');
      error.status = response.status;
      throw error;
    }
    return response.json();
  }
  function label(r) {
    return (r.waiting ? '● ' : '') + r.site_host + '（' + r.messages + ' 則' + (r.waiting ? '，等回覆' : '') + '）';
  }
  function renderOverview() {
    const select = byId('chat-rooms'); select.replaceChildren();
    const waiting = overview.filter((r) => r.waiting).length;
    document.title = (waiting ? '(' + waiting + ') ' : '') + '客戶管理';
    const count = byId('chat-count');
    count.textContent = waiting ? String(waiting) : ''; count.hidden = !waiting;
    if (!overview.length) {
      const none = el('option', '目前沒有客戶對話'); none.value = ''; select.append(none);
      select.disabled = true; return;
    }
    select.disabled = false;
    for (const r of overview) {
      const option = el('option', label(r)); option.value = r.id;
      if (current === r.id) option.selected = true;
      select.append(option);
    }
    select.value = current || '';
    const info = overview.find((r) => r.id === current);
    byId('chat-overview-status').textContent = info && info.last ? '最後：' + KIND[info.last.sender_kind] +
      (info.last.at ? ' ' + when(info.last.at) : '') : '';
  }
  // Pick a room when none is chosen: the first one waiting for a reply, else the first.
  function autoPick() {
    if (current && overview.some((r) => r.id === current)) return;
    const pick = overview.find((r) => r.waiting) || overview[0];
    if (pick) openRoom(pick.id, false);
  }
  async function loadOverview() {
    try { overview = await call('room-overview'); renderOverview(); if (isOpen()) autoPick(); }
    catch (error) { byId('chat-overview-status').textContent = '對話清單讀取失敗：' + error.message; }
  }
  function renderRoom() {
    const info = overview.find((r) => r.id === room.id);
    byId('chat-title').textContent = '與 ' + (info ? info.site_host : '客戶') + ' 的對話';
    byId('chat-room-mode').textContent = '目前：' + MODE[room.mode];
    for (const b of byId('chat-modes').querySelectorAll('button')) {
      b.setAttribute('aria-pressed', b.dataset.roomMode === room.mode ? 'true' : 'false');
    }
    const list = byId('chat-thread');
    const pinned = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    list.replaceChildren();
    for (const m of room.messages) {
      const li = el('li', undefined, m.sender_kind);
      li.append(el('span', KIND[m.sender_kind] || m.sender_kind, 'who'), document.createTextNode(m.body));
      if (m.created_at) li.append(el('span', when(m.created_at), 'when'));
      list.append(li);
    }
    if (pinned) list.scrollTop = list.scrollHeight;
  }
  async function loadRoom() {
    if (!current) return;
    const id = current;
    // Replay everything (a room holds at most a few hundred short messages).
    const messages = [];
    let data = null, after = 0;
    for (;;) {
      data = await call('rooms/' + encodeURIComponent(id) + (after ? '?after=' + after : ''));
      messages.push(...data.messages);
      if (data.messages.length < 100) break;
      after = data.messages[data.messages.length - 1].seq;
    }
    if (current !== id) return;
    room = {...data, messages};
    renderRoom();
  }
  async function openRoom(id, focus = true) {
    if (!id) return;
    current = id; room = null; pending = null; byId('chat-reply').value = '';
    byId('chat-panel').hidden = false; byId('chat-panel-status').textContent = '';
    byId('chat-thread').replaceChildren();
    renderOverview();
    try { await loadRoom(); byId('chat-thread').scrollTop = byId('chat-thread').scrollHeight; }
    catch (error) { byId('chat-panel-status').textContent = error.message; }
    if (focus) byId('chat-reply').focus();
  }
  const isOpen = () => !byId('chat-section').hidden;
  function setOpen(open) {
    byId('chat-section').hidden = !open;
    byId('chat-launcher').setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) { autoPick(); if (current) loadRoom().catch(() => {}); }
  }
  async function reply() {
    const body = byId('chat-reply').value;
    if (sending || !room || !body.trim()) return;
    sending = true; byId('chat-reply-send').disabled = true; byId('chat-panel-status').textContent = '';
    try {
      // One client id per attempt: a retry must not post twice.
      if (!pending || pending.body !== body) pending = {body, id: crypto.randomUUID()};
      await call('rooms/' + encodeURIComponent(room.id) + '/reply', {client_message_id: pending.id, body});
      pending = null; byId('chat-reply').value = '';
      await loadRoom(); await loadOverview();
      byId('chat-thread').scrollTop = byId('chat-thread').scrollHeight;
    } catch (error) {
      byId('chat-panel-status').textContent = error.message + '（未確認送出前請勿改字重送，直接再按一次即可）';
    } finally { sending = false; byId('chat-reply-send').disabled = false; }
  }
  async function assist() {
    const instruction = byId('chat-reply').value;
    if (sending || !room || !instruction.trim()) return;
    sending = true; byId('chat-reply-send').disabled = true; byId('chat-assist').disabled = true;
    byId('chat-panel-status').textContent = 'AI 正在依你的指示回覆客戶…';
    try {
      const result = await call('rooms/' + encodeURIComponent(room.id) + '/assist', {instruction});
      byId('chat-reply').value = '';
      byId('chat-panel-status').textContent = result && result.discarded
        ? '對話在 AI 回覆期間有新訊息，這次回覆已作廢；請看最新對話再指示一次。' : '';
      await loadRoom(); await loadOverview();
      byId('chat-thread').scrollTop = byId('chat-thread').scrollHeight;
    } catch (error) {
      byId('chat-panel-status').textContent = error.message;
    } finally { sending = false; byId('chat-reply-send').disabled = false; byId('chat-assist').disabled = false; }
  }
  async function setMode(mode) {
    if (!room || mode === room.mode) return;
    byId('chat-panel-status').textContent = '';
    try {
      await call('rooms/' + encodeURIComponent(room.id) + '/control', {mode, expected_epoch: room.epoch});
      byId('chat-panel-status').textContent = '已改為：' + MODE[mode];
    } catch (error) { byId('chat-panel-status').textContent = error.message; }
    try { await loadRoom(); await loadOverview(); } catch (_) {}
  }
  byId('chat-launcher').addEventListener('click', () => setOpen(!isOpen()));
  byId('chat-close').addEventListener('click', () => { setOpen(false); byId('chat-launcher').focus(); });
  byId('chat-rooms').addEventListener('change', () => openRoom(byId('chat-rooms').value));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && isOpen()) { setOpen(false); byId('chat-launcher').focus(); }
  });
  byId('chat-reply-form').addEventListener('submit', (event) => { event.preventDefault(); reply(); });
  byId('chat-reply').addEventListener('keydown', (event) => {
    // Enter sends; Shift+Enter is a new line; never while an IME is composing.
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault(); reply();
  });
  byId('chat-assist').addEventListener('click', () => assist());
  for (const b of byId('chat-modes').querySelectorAll('button')) {
    b.addEventListener('click', () => setMode(b.dataset.roomMode));
  }
  // Poll only while the tab is visible: the thread every 5 s, the list every 15 s.
  function schedule() {
    clearTimeout(overviewTimer); clearTimeout(threadTimer);
    if (document.hidden) return;
    threadTimer = setTimeout(async function tick() {
      if (current && !sending && isOpen()) { try { await loadRoom(); } catch (_) {} }
      if (!document.hidden) threadTimer = setTimeout(tick, 5000);
    }, 5000);
    overviewTimer = setTimeout(async function tick() {
      await loadOverview();
      if (!document.hidden) overviewTimer = setTimeout(tick, 15000);
    }, 15000);
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { loadOverview(); if (current && isOpen()) loadRoom().catch(() => {}); }
    schedule();
  });
  loadOverview().then(() => { if (location.hash === '#chat-section') setOpen(true); });
  schedule();
})();
"""


def install_management_ui(app, core, *, loopback_fixture=False, live=False, site_domain=''):
    """Install on the management app using its core; never mint admin identity.

    loopback_fixture=True renders `data-fixture="loopback"` so the page shows the
    fixture-only bootstrap/site-job controls and emits http://localhost:18204
    invite links. It is a trusted composition flag, never request-selectable.
    """
    if not getattr(app.state, 'support_management_installed', False):
        raise ValueError('management must be installed first')
    if not callable(getattr(app.state, 'support_actor_for', None)):
        raise ValueError('existing administrator adapter required')
    if not any(m.cls is AdminBoundary for m in app.user_middleware):
        raise ValueError('existing administrator edge middleware required')
    if getattr(app.state, 'support_management_ui_installed', False):
        return app

    def add(path, content, media_type):
        async def endpoint(request: Request):
            try:
                actor = await app.state.support_actor_for(request)
                await run_in_threadpool(core._admin, actor)
            except Exception:
                return _error(401)
            if request.scope.get('query_string'):
                return _error(400)
            response = HTMLResponse if media_type == 'text/html' else Response
            return response(content, media_type=media_type, headers=SENSITIVE_RESPONSE_HEADERS)
        app.add_api_route(path, endpoint, methods=['GET'], include_in_schema=False)

    if loopback_fixture and live:
        raise ValueError('a deployment is either an isolated fixture or live, never both')
    import re as _re
    if site_domain and not _re.fullmatch(r'[a-z0-9.-]{1,253}', site_domain):
        raise ValueError('invalid site domain')
    if loopback_fixture:
        page = PAGE.replace('<body>', '<body data-mode="loopback" '
                            'data-invite-origin="http://localhost:18204" '
                            f'data-site-domain="{site_domain}">', 1)
    elif live:
        origin = getattr(app.state, 'support_invite_origin', '')
        if not origin.startswith('https://'):
            raise ValueError('live management requires an https invite origin')
        if not site_domain:
            raise ValueError('live management requires the site domain')
        page = PAGE.replace('<body>', f'<body data-mode="live" data-invite-origin="{origin}" '
                            f'data-site-domain="{site_domain}">', 1)
    else:
        page = PAGE
    add('/admin/customers', page, 'text/html')
    add('/admin/customers/assets/management.js', MANAGEMENT_JS, 'text/javascript')
    add('/admin/customers/assets/style.css', CSS, 'text/css')
    add('/admin/customers/assets/tasks.js', TASKS_JS, 'text/javascript')
    add('/admin/customers/assets/chat.js', CHAT_JS, 'text/javascript')
    add('/admin/customers/assets/shell.js', SHELL_JS, 'text/javascript')
    app.state.support_management_ui_installed = True
    return app
