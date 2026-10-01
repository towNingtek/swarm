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
body { font: 1rem system-ui, sans-serif; max-width: 64rem; margin: 2rem auto; padding: 1rem; color: #192733; background: #f5f7fa; }
section { background: white; padding: 1rem; margin-block: 1rem; }
label { display: block; margin-block: .7rem; }
input, select, button { font: inherit; padding: .5rem; max-width: 100%; }
#invite-link { width: 95%; }
.warning { background: #fff0bb; padding: 1rem; }
#tenant-detail, #tenant-list { overflow-wrap: anywhere; white-space: pre-wrap; }
#bootstrap-host-row { display: inline-flex; align-items: baseline; gap: .25rem; }
#bootstrap-domain { color: #55606b; }
#bootstrap-preview { color: #55606b; margin-block: .4rem; }
#invite-row { display: flex; gap: .5rem; align-items: center; }
#invite-row input { flex: 1; }
#invite-copy-status { color: #55606b; margin-block: .4rem; }
#danger-section { border-left: .35rem solid #a3232c; }
#delete-submit:not(:disabled) { background: #a3232c; color: white; border-color: #a3232c; }
#task-context { width: 100%; font: inherit; box-sizing: border-box; }
#task-list li, #task-deliveries li { margin-block: .4rem; overflow-wrap: anywhere; }
#task-list button { margin-inline-start: .4rem; }
#task-picks label { display: inline-block; margin: .2rem 1rem .2rem 0; }
#task-cron-hint, #task-status { color: #55606b; margin-block: .3rem; }
.muted { color: #55606b; }
#chat-launcher { position: fixed; right: 1.2rem; bottom: 1.2rem; z-index: 30; padding: .8rem 1.2rem;
  border-radius: 999px; border: 1px solid #192733; background: #192733; color: white; font: inherit; font-weight: 600;
  cursor: pointer; box-shadow: 0 8px 28px #19273344; }
#chat-section { position: fixed; right: 1.2rem; bottom: 4.8rem; z-index: 31; display: flex; flex-direction: column;
  width: min(30rem, calc(100vw - 2.4rem)); height: min(44rem, calc(100vh - 6.5rem)); box-sizing: border-box;
  padding: 1rem; border-radius: .8rem; background: white; border: 1px solid #c9d1da; box-shadow: 0 18px 50px #0003; }
#chat-section[hidden] { display: none; }
#chat-section > * { flex: none; }
#chat-section header { display: flex; justify-content: space-between; align-items: center; }
#chat-section h2 { margin: 0; font-size: 1.1rem; }
#chat-section h3 { margin: .5rem 0 .2rem; font-size: 1rem; }
#chat-section p { margin-block: .3rem; }
#chat-close { width: 2rem; height: 2rem; font-size: 1.1rem; }
#chat-rooms { width: 100%; font: inherit; margin-top: .2rem; }
#chat-panel { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
#chat-panel[hidden] { display: none; }
#chat-panel > * { flex: none; }
.chat-hint { font-size: .8rem; }
.chat-badge { display: inline-block; padding: 0 .45rem; margin-inline-start: .4rem; border-radius: .7rem; background: #dfe5ec; font-size: .85rem; }
.chat-badge.waiting { background: #a3232c; color: white; }
.chat-badge[hidden] { display: none; }
#chat-modes button[aria-pressed="true"] { background: #192733; color: white; border-color: #192733; }
#chat-panel > #chat-thread { flex: 1 1 auto; min-height: 6rem; list-style: none; padding: 0; margin: .4rem 0; overflow-y: auto; border: 1px solid #dfe5ec; }
#chat-thread li { padding: .45rem .7rem; white-space: pre-wrap; overflow-wrap: anywhere; border-bottom: 1px solid #eef1f5; }
#chat-thread li.customer { background: #fff8e1; }
#chat-thread li.human { background: #e8f1fb; }
#chat-thread .who { font-weight: 600; margin-inline-end: .4rem; }
#chat-thread .when { color: #55606b; font-size: .8rem; margin-inline-start: .4rem; }
#chat-reply { width: 100%; font: inherit; box-sizing: border-box; }
.chat-actions { display: flex; gap: .5rem; }
main { padding-bottom: 5rem; }
"""

PAGE = '''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>客戶管理</title><link rel="stylesheet" href="/admin/customers/assets/style.css">
<script defer src="/admin/customers/assets/management.js"></script>
<script defer src="/admin/customers/assets/tasks.js"></script>
<script defer src="/admin/customers/assets/chat.js"></script>
</head><body><main>
<h1>客戶管理</h1><p class="warning" id="page-note">僅管理客戶與客服中繼資料；本頁不會建立 Docker 站台、不部署、不設定 DNS。顯示「已啟用」僅代表客戶帳號可使用邀請啟用，並不表示站台已上線。</p>
<section><h2>客戶清單</h2><p>沒有客戶時，請由核准的管理流程建立客戶資料與客服房間，再回此頁發送邀請。</p><ul id="tenant-list"></ul>
<form id="bootstrap-form" hidden autocomplete="off"><p class="warning" id="bootstrap-note"></p>
<label for="bootstrap-host">客戶站台名稱（此刻不需存在）</label>
<span id="bootstrap-host-row"><input id="bootstrap-host" type="text" required placeholder="ab"
 autocapitalize="off" autocorrect="off" spellcheck="false"><span id="bootstrap-domain"></span></span>
<p id="bootstrap-preview"></p>
<button id="bootstrap-submit" type="submit">建立客戶</button></form>
<label>選擇租戶 <select id="tenants"></select></label><button id="refresh" type="button">重新載入</button>
<p id="tenant-detail"></p></section>
<section><h2>發送邀請</h2><p>邀請只顯示一次，請自行安全傳遞。此頁不自動開啟連結、不保存邀請。切換租戶或離頁會清除。送出失敗可能已發出邀請，請勿盲目重試；需由可信任管理員撤銷不需要的邀請。</p>
<form id="invite-form" autocomplete="off"><label>有效秒數（60–86400）<input id="ttl" type="number" min="60" max="86400" step="1" value="3600" required></label>
<button id="invite-submit" type="submit">產生邀請</button></form>
<label for="invite-link">一次性邀請連結</label>
<span id="invite-row"><input id="invite-link" type="text" readonly autocomplete="off" spellcheck="false">
<button id="invite-copy" type="button" disabled>複製</button></span>
<p id="invite-copy-status" role="status" aria-live="polite"></p></section>
<section><h2>工作區樣板</h2><p>建站時放進客戶工作區的起始內容（只放一次；之後改選不會改動已建好的工作區，也不會覆蓋客戶改過的檔案）。所有站台都另有平台指引（回覆語言、起步模型、如何接上自己的模型）。樣板不含任何金鑰。</p>
<form id="template-form"><label>樣板 <select id="template"></select></label>
<button id="template-submit" type="submit">儲存樣板</button></form><p id="template-desc"></p></section>
<section id="site-job-section" hidden><h2>建站任務</h2><p class="warning" id="site-job-note"></p>
<button id="site-job-submit" type="button">排入建站任務</button><p id="site-job-result"></p></section>
<section id="danger-section" hidden><h2>刪除客戶</h2>
<p class="warning">會刪除容器、DNS 記錄、反向代理設定、站台資料與所有客戶紀錄（帳號、邀請、模型設定）。<strong>無法復原</strong>；客戶需重新邀請並重建站台。</p>
<label>請輸入要刪除的站台名稱以確認 <input id="delete-confirm" type="text" autocomplete="off" spellcheck="false"></label>
<button id="delete-submit" type="button" disabled>永久刪除此客戶</button><p id="delete-result"></p></section>
<section><h2>平台模型每月額度</h2><p>同一個額度池涵蓋母站客服助手與子站的起步模型（平台借給新站台、客戶尚未接上自己模型前使用）。單位為 token，每月（UTC）重算；串流中斷而無法得知實際用量時，以預估值計入。客戶在子站接上自己的模型後，不佔這個額度。停用為預設；不限額仍受系統安全速率與供應商限制。</p><form id="quota-form"><label>政策 <select id="policy">
<option value="disabled">停用（disabled）</option><option value="unlimited">無上限（unlimited）</option><option value="capped">每月上限（capped）</option></select></label>
<label>每月額度（僅 capped 使用）<input id="limit" type="number" min="0" max="1000000000000" step="1"></label>
<button id="quota-submit" type="submit">儲存政策</button></form></section>
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

<section id="tasks-section"><h2>任務庫</h2>
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
<button id="task-save" type="submit">儲存範本</button> <button id="task-cancel" type="button">清空</button></form>
<h3>交給客戶</h3>
<label>客戶 <select id="task-tenant"></select></label>
<fieldset id="task-picks"><legend>要交付的任務</legend></fieldset>
<label>方式 <select id="task-mode"><option value="offer">客戶確認：出現在客戶設定頁，由客戶選公司後加入</option>
<option value="direct">直接加入：平台寫進客戶的第一間公司（還沒有公司就等客戶建好）</option></select></label>
<button id="task-push" type="button">交付</button>
<ul id="task-deliveries"></ul>
<p id="task-status" role="status" aria-live="polite"></p></section>
<p id="status" role="status" aria-live="polite"></p></main></body></html>'''

MANAGEMENT_JS = r"""'use strict';
(() => {
  const byId = (id) => document.getElementById(id);
  const status = byId('status');
  let rows = [], current = null, generation = 0, busy = false, leaving = false;
  let templates = [];
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
    current = rows.find((row) => row.id === byId('tenants').value) || null;
    byId('tenant-detail').textContent = current ? detailText(current) : '沒有可用租戶。';
    byId('policy').value = current ? current.policy.mode : 'disabled';
    if (current) byId('template').value = current.template;
    templateDesc();
    byId('limit').value = current && current.policy.monthly_limit !== null ? String(current.policy.monthly_limit) : '';
    controls();
  }
  async function api(path, body) {
    const options = {credentials: 'same-origin', cache: 'no-store', redirect: 'error'};
    if (body !== undefined) {
      options.method = 'POST'; options.headers = {'Content-Type': 'application/json'};
      options.body = JSON.stringify(body);
    }
    const response = await fetch('/admin/tenants' + path, options);
    if (!response.ok) throw new Error('request failed');
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
    byId('tenant-detail').textContent = ''; controls();
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
    app.state.support_management_ui_installed = True
    return app
