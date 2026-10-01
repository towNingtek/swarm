"""Customer onboarding UI: site card, real office summary (read from the site), model status, rules."""
from fastapi import Request
from starlette.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from support_app import _error
from support_http_policy import SENSITIVE_RESPONSE_HEADERS

PAGE = '''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Swarm · 你的 AI 辦公室</title>
<link rel="stylesheet" href="/customer/ui/landing.css">
<link rel="stylesheet" href="/customer/ui/onboarding.css">
<script defer src="/customer/ui/onboarding.js"></script></head><body class="app">
<header class="nav"><a class="brand" href="/customer/onboarding"><svg class="mark" width="28" height="28" viewBox="0 0 40 40" aria-hidden="true" focusable="false"><polygon points="20.0,2.0 35.6,11.0 35.6,29.0 20.0,38.0 4.4,29.0 4.4,11.0" class="mark-outer"/><polygon points="20.0,12.0 26.9,16.0 26.9,24.0 20.0,28.0 13.1,24.0 13.1,16.0" class="mark-inner"/></svg><span>SWARM</span></a>
<nav class="nav-links"><a href="/customer/chat">完整客服對話</a><a class="nav-login" href="/customer/welcome">重新登入</a></nav></header>
<main>
<section class="hero compact">
<h1>你的 <span class="glow">AI 辦公室</span></h1>
<p class="lede">這裡是你的設定總覽：站台、辦公室裡的公司與角色、正在用的模型。內容都直接讀自你的站台，不是勾選出來的。</p>
</section>
<section id="site-card" class="panel site-card">
<div class="site-head"><div><h2>我的站台</h2><p id="site-job" class="site-state"></p><p id="site-link" class="site-link"></p></div>
<div id="site-ready" class="site-actions" hidden><button id="site-enter" type="button" class="btn primary">進入我的站台 →</button>
<p class="hint">不需要另外的帳號密碼。</p></div></div>
<p id="site-secret" class="hint"></p>
<details id="site-password" class="sitepw" hidden><summary>設定站台密碼（選用）</summary>
<p class="hint">想直接打開站台網址登入時才需要。登入帳號固定是 <code>admin</code>。平台不會保存這組密碼。
儲存時站台會重新啟動約 10–30 秒，已開著的站台分頁需要重新登入。</p>
<form id="site-password-form" autocomplete="off">
<fieldset class="choice"><legend>密碼來源</legend>
<label class="inline"><input type="radio" name="site-password-source" id="site-password-new" value="new" checked> 設定一組新密碼</label>
<label class="inline"><input type="radio" name="site-password-source" id="site-password-reuse" value="reuse"> 沿用我的平台密碼</label></fieldset>
<div id="site-password-new-fields">
<label for="site-password-input">新密碼（至少 12 個字）</label>
<input id="site-password-input" type="password" autocomplete="new-password" minlength="12" maxlength="1024">
<label for="site-password-confirm">再輸入一次</label>
<input id="site-password-confirm" type="password" autocomplete="new-password" minlength="12" maxlength="1024"></div>
<div id="site-password-reuse-fields" hidden>
<label for="site-password-platform">目前的平台密碼（驗證用，須至少 12 個字）</label>
<input id="site-password-platform" type="password" autocomplete="current-password" maxlength="1024">
<p class="hint">之後若改了平台密碼，站台密碼不會跟著變。</p></div>
<button id="site-password-save" type="submit" class="btn primary">儲存站台密碼</button>
<p id="site-password-status" role="status" aria-live="polite"></p></form></details>
</section>
<section class="panel slim" aria-labelledby="steps-title"><h2 id="steps-title" class="small-title">設定進度</h2><ol id="steps" class="progress"></ol></section>
<section id="office" class="panel"><div class="section-head"><h2>你的辦公室</h2><span id="office-meta" class="meta"></span></div>
<p id="office-note" class="hint"></p>
<div id="offers" class="offers" hidden><div class="offers-head"><h3>平台為你準備的任務</h3><span id="offers-meta" class="meta"></span></div>
<p class="hint">選一間公司按「加入」，就會變成那間公司的排程；之後可以自己改時間和內容。不需要就按「不用了」。</p>
<ul id="offer-list" class="offer-list"></ul></div>
<p id="offers-waiting" class="hint" hidden></p>
<div id="hives" class="hives"></div>
<div id="office-empty" class="empty" hidden><p class="empty-title">還沒有任何公司（hive）</p>
<ol class="howto"><li>按上方「進入我的站台」。</li><li>在輸入框打「<strong>帶我完成新手任務</strong>」。</li><li>AI 會問你公司名稱、要做什麼、要哪些角色，確認後幫你建好。</li></ol><p class="hint">排程要掛在公司底下：建好公司後，這裡就能新增與編輯排程。</p></div>
<div id="sched-home"><form id="sched-form" class="sched-form" hidden autocomplete="off">
<h3 id="sched-title">新增排程</h3>
<p class="form-group">什麼時候</p>
<div class="form-grid">
<label>頻率<select id="sched-freq"><option value="daily">每天</option><option value="weekdays">平日（週一到週五）</option><option value="weekly">每週</option><option value="hourly">每小時</option><option value="custom">自訂（cron）</option></select></label>
<label id="sched-dow-wrap">星期<select id="sched-dow"><option value="mon">週一</option><option value="tue">週二</option><option value="wed">週三</option><option value="thu">週四</option><option value="fri">週五</option><option value="sat">週六</option><option value="sun">週日</option></select></label>
<label id="sched-time-wrap">時間（台北）<input id="sched-time" type="time" value="08:00" step="60"></label>
<label id="sched-minute-wrap" hidden>每小時的第幾分<input id="sched-minute" type="number" min="0" max="59" value="0"></label>
<label id="sched-cron-wrap" hidden>cron（分 時 日 月 星期）<input id="sched-cron" type="text" maxlength="64" placeholder="0 8 * * mon-fri"></label>
</div>
<p class="form-group">誰、做什麼</p>
<div class="form-grid">
<label>由誰執行<select id="sched-role"></select></label>
<label>使用 skill（選填）<select id="sched-skill"></select></label>
<label>任務代號（選填，英文）<input id="sched-name" type="text" maxlength="63" placeholder="留空自動產生"></label>
</div>
<label for="sched-context">要做什麼</label>
<textarea id="sched-context" maxlength="2000" rows="4" placeholder="例：整理昨天的新訂單與客訴，列出今天要處理的三件事。"></textarea>
<p class="hint">不要寫入密碼或金鑰。平台只修改 <code>.swarm/registry.yaml</code> 與這間公司的 <code>project.yaml</code>，改之前會備份。</p>
<p id="sched-preview" class="preview" aria-live="polite"></p>
<div class="row"><button id="sched-save" type="submit" class="btn primary">儲存</button><button id="sched-cancel" type="button" class="btn ghost">取消</button></div>
<p id="sched-status" role="status" aria-live="polite"></p>
</form></div>
<details id="runs-box" class="runs" hidden><summary>執行紀錄 <span id="runs-meta" class="meta"></span></summary><ul id="run-list" class="run-list"></ul></details>
<p class="schedule-note"><span class="note-icon" aria-hidden="true">i</span><span id="schedule-note-text">排程會照經典 swarm 格式寫進站台設定，但<strong>這個站台還沒有排程執行器</strong>，目前只是記錄、不會自動執行。執行器上線後，標示「會執行」的排程就會照時間跑。</span></p>
</section>
<section id="model" class="panel"><div class="section-head"><h2>模型</h2><span id="model-meta" class="meta"></span></div>
<p id="model-current" class="model-current"></p>
<div id="starter-box" class="starter" hidden><div class="meter"><span id="starter-bar"></span></div><p id="starter-usage" class="hint"></p></div>
<ol id="model-howto" class="howto" hidden><li>進入站台，打開 <strong>Settings → Models</strong>。</li><li>新增你的供應商（例如 OpenRouter、OpenAI）並貼上自己的金鑰。</li><li>把它設為預設模型。金鑰只存在你的站台，不要貼到聊天。</li></ol>
</section>
<details id="rules-box" class="panel rules-box"><summary><h2>使用與支援說明</h2><span id="rules-state" class="meta"></span></summary>
<ul id="rules" class="rules"></ul>
<button id="acknowledge" class="btn primary" disabled>我已閱讀上述說明</button></details>
<div class="toolbar"><button id="reload" class="btn ghost">重新載入</button><span id="auto-note"></span></div>
<p id="status" role="status" aria-live="polite"></p></main>
<button id="copilot-launcher" type="button" aria-expanded="false" aria-controls="copilot">
<svg width="18" height="18" viewBox="0 0 40 40" aria-hidden="true" focusable="false"><polygon points="20.0,2.0 35.6,11.0 35.6,29.0 20.0,38.0 4.4,29.0 4.4,11.0"/></svg>
<span>設定助手</span></button>
<aside id="copilot" hidden aria-label="設定助手">
<header><div><h2>設定助手</h2></div><button id="copilot-close" type="button" aria-label="收起">×</button></header>
<p id="copilot-message" aria-live="polite">正在讀取你的設定進度…</p>
<button id="copilot-act" type="button" class="btn primary" hidden></button>
<p id="copilot-status" role="status" aria-live="polite"></p>
<p id="chat-mode" role="status" aria-live="polite"></p>
<ol id="chat-log" aria-live="polite"></ol>
<form id="chat-form" autocomplete="off">
<label for="chat-input">想問什麼都可以（Enter 送出，Shift+Enter 換行）</label>
<div class="composer"><textarea id="chat-input" maxlength="4000" rows="1"></textarea>
<button id="chat-send" type="submit" class="btn primary">送出</button></div>
<button id="chat-ask-ai" type="button" class="btn" hidden>請 AI 助手回答</button></form>
<p id="chat-status" role="status" aria-live="polite"></p>
<p class="hint">助手看得到你的設定進度，只能執行按鈕上寫明的動作。平台人員可能加入協助。
請勿貼上密碼或 API 金鑰。</p></aside></body></html>'''

JS = r"""'use strict';
(() => {
  const byId = id => document.getElementById(id);
  const labels = {activation:'啟用帳號', rules:'確認使用說明', site_preparation:'建立站台',
                  office:'建立辦公室', own_model:'接上自己的模型'};
  const states = {complete:'完成', pending:'待辦', in_progress:'進行中', attention:'需要協助',
                  blocked:'等待前一步', skipped:'不需要'};
  const reasons = {awaiting_operator_request:'等待管理員建立', provisioning_needs_operator:'請聯絡管理員',
                   simulated_only:'僅模擬，非真實站台', site_not_ready:'站台建立後才能進行',
                   no_hive_yet:'進站後建立第一間公司', using_starter:'目前用平台起步模型',
                   default_unknown:'讀不到站台的預設模型', no_office_template:'未使用辦公室樣板'};
  let version = null, busy = false;
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function renderSteps(steps) {
    byId('steps').replaceChildren();
    for (const step of steps) {
      if (!labels[step.id]) continue;
      const item = el('li', 'step s-' + (states[step.status] ? step.status : 'unknown'));
      item.append(el('span', 'step-name', labels[step.id]));
      item.append(el('span', 'step-state', reasons[step.reason] || states[step.status] || step.status));
      byId('steps').append(item);
    }
  }
  // --- Schedules --------------------------------------------------------
  // Same rules as the classic swarm scheduler: a cron on the role in
  // registry.yaml plus a task under reports: in project.yaml. The server
  // writes both; this page only sends the customer's choices.
  const dowNames = {mon:'週一', tue:'週二', wed:'週三', thu:'週四', fri:'週五', sat:'週六', sun:'週日'};
  // Numbers follow the classic scheduler (APScheduler): 0 = Monday.
  const dowNumbers = ['mon','tue','wed','thu','fri','sat','sun'];
  const pad = n => String(n).padStart(2, '0');
  function describeCron(cron) {
    if (typeof cron !== 'string') return '未設定時間';
    const p = cron.trim().split(/\s+/);
    if (p.length !== 5) return cron;
    const [m, h, d, mo, w] = p;
    const num = x => /^\d+$/.test(x);
    const day = x => num(x) ? dowNumbers[Number(x)] : x;
    if (d !== '*' || mo !== '*') return cron;
    if (num(m) && h === '*' && w === '*') return '每小時第 ' + Number(m) + ' 分';
    if (!num(m) || !num(h)) return cron;
    const at = pad(h) + ':' + pad(m);
    if (w === '*') return '每天 ' + at;
    if (w === 'mon-fri' || w === '0-4') return '平日 ' + at;
    const days = w.split(',').map(day);
    if (days.every(x => dowNames[x])) return '每' + days.map(x => dowNames[x]).join('、') + ' ' + at;
    return cron;
  }
  let executor = false;
  const schedStates = {ready:['會執行（待執行器上線）','ok'], disabled:['已停用','off'],
                       missing_definition:['缺少任務內容','warn'], missing_time:['缺少時間','warn']};
  let editing = null, office = null;
  const schedBoxes = {};
  function splitCron(cron) {
    // "每週一、週四 10:30" -> ["每週一、週四", "10:30"] for the time block.
    const text = describeCron(cron);
    const m = /^(.*?)\s*(\d\d:\d\d)$/.exec(text);
    return m && m[1] ? [m[1], m[2]] : ['', text];
  }
  function renderSchedules(hive) {
    const box = el('div', 'sched');
    schedBoxes[hive.id] = box;
    const head = el('div', 'sched-head');
    const items = Array.isArray(hive.schedules) ? hive.schedules : [];
    const title = el('h4', '', '排程');
    if (items.length) title.append(el('span', 'count', String(items.length)));
    head.append(title);
    const actions = el('div', 'sched-actions');
    const toggle = el('button', 'switch' + (hive.enabled ? ' on' : ''));
    toggle.type = 'button';
    toggle.setAttribute('role', 'switch');
    toggle.setAttribute('aria-checked', hive.enabled ? 'true' : 'false');
    toggle.setAttribute('aria-label', hive.enabled ? '關閉這間公司的排程' : '開啟這間公司的排程');
    toggle.setAttribute('title', hive.enabled ? '關閉這間公司的排程' : '開啟這間公司的排程');
    toggle.append(el('span', 'knob'), el('span', 'switch-text', hive.enabled ? '排程開啟' : '排程關閉'));
    toggle.addEventListener('click', () => officeWrite('hive-enabled',
      {hive: hive.id, enabled: hive.enabled ? 'false' : 'true'}));
    const add = el('button', 'btn primary small', '＋ 新增排程');
    add.type = 'button';
    add.addEventListener('click', () => openSchedule(hive, null));
    actions.append(toggle, add);
    head.append(actions);
    box.append(head);
    if (!items.length) {
      box.append(el('p', 'sched-empty', '還沒有排程。按「＋ 新增排程」，例如每天早上整理待辦、每週一寫週報。'));
      return box;
    }
    const list = el('ul', 'sched-list');
    for (const job of items) {
      const state = schedStates[job.state] || [job.state, ''];
      const row = el('li', 'sched-item st-' + (state[1] || 'none'));
      const [when, at] = splitCron(job.cron);
      const time = el('div', 'sched-time');
      if (when) time.append(el('span', 'when', when));
      time.append(el('strong', '', at));
      row.append(time);
      const main = el('div', 'sched-main');
      main.append(el('span', 'sched-what', job.context || (job.skill ? '執行 ' + job.skill + ' skill' : '（沒有任務內容）')));
      const who = el('span', 'sched-who');
      who.append(el('span', 'who-role', roleNames[job.role] || job.role || '未指定角色'));
      who.append(el('span', '', job.name));
      if (job.skill) who.append(el('span', '', 'skill ' + job.skill));
      main.append(who);
      row.append(main);
      const side = el('div', 'sched-side');
      side.append(el('span', 'tag ' + state[1], job.state === 'ready' && executor ? '會照時間執行' : state[0]));
      const buttons = el('div', 'row-actions');
      if (executor && job.role && job.state !== 'missing_definition') {
        const now = el('button', 'link', '立即執行');
        now.type = 'button';
        now.addEventListener('click', () => {
          if (typeof confirm === 'function' && !confirm('現在執行一次「' + job.name + '」？會使用站台的預設模型。')) return;
          officeWrite('schedule-run', {hive: hive.id, name: job.name}, '已開始執行，結果會出現在下方「執行紀錄」。');
        });
        buttons.append(now);
      }
      const edit = el('button', 'link', '編輯');
      edit.type = 'button';
      edit.addEventListener('click', () => openSchedule(hive, job));
      const remove = el('button', 'link danger', '刪除');
      remove.type = 'button';
      remove.addEventListener('click', () => {
        if (typeof confirm === 'function' && !confirm('刪除排程「' + job.name + '」？')) return;
        officeWrite('schedule-delete', {hive: hive.id, name: job.name});
      });
      buttons.append(edit, remove);
      side.append(buttons);
      row.append(side);
      list.append(row);
    }
    box.append(list);
    return box;
  }
  function updatePreview() {
    if (!editing) return;
    const cron = buildCron();
    const role = byId('sched-role').value;
    byId('sched-preview').textContent = '預覽：' + describeCron(cron) + '（台北時間），由 '
      + (roleNames[role] || role || '—') + ' 執行';
  }
  function autoName(hive) {
    const taken = new Set((Array.isArray(hive.schedules) ? hive.schedules : []).map(j => j.name));
    let n = taken.size + 1;
    while (taken.has('task_' + n)) n++;
    return 'task_' + n;
  }
  function option(value, label) { const o = el('option', '', label); o.value = value; return o; }
  function freqChanged() {
    const f = byId('sched-freq').value;
    byId('sched-dow-wrap').hidden = f !== 'weekly';
    byId('sched-time-wrap').hidden = f === 'hourly' || f === 'custom';
    byId('sched-minute-wrap').hidden = f !== 'hourly';
    byId('sched-cron-wrap').hidden = f !== 'custom';
  }
  function fillTime(cron) {
    const p = typeof cron === 'string' ? cron.trim().split(/\s+/) : [];
    const num = x => /^\d+$/.test(x || '');
    let f = 'custom';
    if (p.length === 5 && p[2] === '*' && p[3] === '*' && num(p[0])) {
      if (p[1] === '*' && p[4] === '*') { f = 'hourly'; byId('sched-minute').value = p[0]; }
      else if (num(p[1])) {
        byId('sched-time').value = pad(p[1]) + ':' + pad(p[0]);
        const w = p[4];
        if (w === '*') f = 'daily';
        else if (w === 'mon-fri') f = 'weekdays';
        else if (dowNames[w]) { f = 'weekly'; byId('sched-dow').value = w; }
      }
    }
    if (!cron) f = 'daily';
    byId('sched-freq').value = f;
    byId('sched-cron').value = cron || '';
    freqChanged();
  }
  function buildCron() {
    const f = byId('sched-freq').value;
    if (f === 'custom') return byId('sched-cron').value.trim();
    if (f === 'hourly') return String(Number(byId('sched-minute').value) || 0) + ' * * * *';
    const [h, m] = (byId('sched-time').value || '08:00').split(':').map(Number);
    const w = f === 'daily' ? '*' : f === 'weekdays' ? 'mon-fri' : byId('sched-dow').value;
    return m + ' ' + h + ' * * ' + w;
  }
  function openSchedule(hive, job) {
    editing = {hive: hive.id, original: job ? job.name : '', auto: autoName(hive)};
    byId('sched-title').textContent = (job ? '編輯排程 · ' : '新增排程 · ') + (hive.name || hive.id);
    const roles = byId('sched-role');
    roles.replaceChildren();
    for (const role of Array.isArray(hive.roles) ? hive.roles : [])
      roles.append(option(role.id, (roleNames[role.id] || role.id) + '（' + role.id + '）'));
    roles.value = (job && job.role) || 'pm';
    const skills = byId('sched-skill');
    skills.replaceChildren(option('', '不用 skill，照下面的說明做'));
    for (const name of (office && Array.isArray(office.skills)) ? office.skills : []) skills.append(option(name, name));
    if (job && job.skill && ![...skills.children].some(o => o.value === job.skill)) skills.append(option(job.skill, job.skill));
    skills.value = (job && job.skill) || '';
    byId('sched-name').value = job ? job.name : '';
    byId('sched-context').value = job ? job.context || '' : '';
    byId('sched-status').textContent = '';
    fillTime(job ? job.cron : null);
    const box = schedBoxes[hive.id];
    if (box) box.append(byId('sched-form'));
    byId('sched-form').hidden = false;
    updatePreview();
    if (byId('sched-form').scrollIntoView) byId('sched-form').scrollIntoView({block: 'center', behavior: 'smooth'});
  }
  function closeSchedule() {
    editing = null;
    byId('sched-form').hidden = true;
    byId('sched-home').append(byId('sched-form'));
  }
  async function officeWrite(action, body, done) {
    if (busy || !office) return false;
    busy = true;
    const out = editing ? byId('sched-status') : byId('status');
    out.textContent = '正在寫入站台設定…';
    let ok = false;
    try {
      const response = await fetch('/customer/office/' + action, {method:'POST', credentials:'same-origin',
        cache:'no-store', redirect:'error', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({revision: office.revision, ...body})});
      let reply = null;
      try { reply = await response.json(); } catch (_) { reply = null; }
      if (!response.ok) {
        const message = reply && typeof reply.message === 'string' ? reply.message
          : response.status === 401 ? '登入已失效，請重新登入。' : '沒有寫入，請重新載入後再試。';
        throw new Error(message);
      }
      ok = true;
      out.textContent = done || '';
    } catch (error) { out.textContent = error.message; }
    finally { busy = false; }
    const message = out.textContent;
    await run(false);
    if (!editing && message) byId('status').textContent = message;
    if (!ok && editing) byId('sched-status').textContent = message;
    return ok;
  }
  byId('sched-freq').addEventListener('change', () => { freqChanged(); updatePreview(); });
  for (const id of ['sched-dow', 'sched-time', 'sched-minute', 'sched-cron', 'sched-role'])
    byId(id).addEventListener(id === 'sched-dow' || id === 'sched-role' ? 'change' : 'input', updatePreview);
  byId('sched-cancel').addEventListener('click', closeSchedule);
  byId('sched-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!editing) return;
    const name = byId('sched-name').value.trim() || editing.original || editing.auto;
    if (!/^[a-z0-9][a-z0-9_-]{0,62}$/.test(name)) {
      byId('sched-status').textContent = '任務代號請用英文小寫、數字、底線或連字號，例如 morning_digest。';
      return;
    }
    const body = {hive: editing.hive, original: editing.original, name, role: byId('sched-role').value,
      cron: buildCron(), skill: byId('sched-skill').value, context: byId('sched-context').value};
    if (await officeWrite('schedule', body)) closeSchedule();
  });
  const runStates = {running:['執行中','ok'], succeeded:['完成','ok'], failed:['失敗','warn'], skipped:['跳過','off']};
  function stamp(sec) {
    const d = new Date(Number(sec) * 1000);
    return isNaN(d) ? '' : (d.getMonth() + 1) + '/' + d.getDate() + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  function renderTasks(tasks, office) {
    const t = tasks && typeof tasks === 'object' ? tasks : {};
    executor = t.executor === true;
    byId('schedule-note-text').textContent = executor
      ? '開啟排程的公司，「會照時間執行」的排程由平台在你的站台裡照時間（台北）執行，使用站台的預設模型；用起步模型時會計入每月額度。結果寫在下方執行紀錄，也會更新該角色的 WORKING.md。'
      : '排程會照經典 swarm 格式寫進站台設定，但這個站台還沒有排程執行器，目前只是記錄、不會自動執行。';
    const hives = (office && Array.isArray(office.hives) ? office.hives : []).filter(h => h.exists !== false);
    const offers = Array.isArray(t.offers) ? t.offers : [];
    byId('offers').hidden = !offers.length;
    byId('offers-meta').textContent = offers.length ? offers.length + ' 個' : '';
    const list = byId('offer-list');
    list.replaceChildren();
    for (const offer of offers) {
      const item = el('li', 'offer');
      const main = el('div', 'offer-main');
      main.append(el('strong', '', offer.title));
      main.append(el('span', 'offer-when', describeCron(offer.cron) + ' · 建議由 ' + (roleNames[offer.role] || offer.role)));
      main.append(el('p', 'offer-what', offer.context));
      item.append(main);
      const act = el('div', 'offer-act');
      if (hives.length) {
        const pick = el('select', 'offer-hive');
        pick.setAttribute('aria-label', '加入哪間公司');
        for (const h of hives) pick.append(option(h.id, h.name || h.id));
        const add = el('button', 'btn primary small', '加入');
        add.type = 'button';
        add.addEventListener('click', () => officeWrite('task-accept', {delivery: offer.id, hive: pick.value},
          '已加入「' + offer.title + '」。'));
        act.append(pick, add);
      } else {
        act.append(el('span', 'hint', '先建立公司才能加入'));
      }
      const skip = el('button', 'link danger', '不用了');
      skip.type = 'button';
      skip.addEventListener('click', () => officeWrite('task-dismiss', {delivery: offer.id}));
      act.append(skip);
      item.append(act);
      list.append(item);
    }
    const waiting = Number(t.waiting_for_hive) || 0;
    byId('offers-waiting').hidden = !waiting;
    byId('offers-waiting').textContent = waiting
      ? '平台已為你準備 ' + waiting + ' 個任務，建好第一間公司後會自動加入它的排程。' : '';
    const runs = Array.isArray(t.runs) ? t.runs : [];
    byId('runs-box').hidden = !executor;
    byId('runs-meta').textContent = runs.length ? '最近 ' + runs.length + ' 次' : '還沒有執行過';
    const rl = byId('run-list');
    rl.replaceChildren();
    for (const r of runs) {
      const st = runStates[r.status] || [r.status, ''];
      const item = el('li', 'run');
      const head = el('div', 'run-head');
      head.append(el('span', 'tag ' + st[1], st[0]));
      head.append(el('strong', '', r.name));
      head.append(el('span', 'meta', r.hive + ' · ' + stamp(r.started_at) + (r.trigger === 'manual' ? ' · 手動' : '')));
      item.append(head);
      if (r.output) {
        const out = el('details', 'run-out');
        out.append(el('summary', '', '看結果'));
        out.append(el('pre', '', r.output));
        item.append(out);
      }
      rl.append(item);
    }
  }
  const roleNames = {pm:'PM', rd:'工程', reviewer:'審查', 'tech-writer':'文件', devops:'維運', marketing:'行銷', bd:'商務'};
  function renderOffice(office, site) {
    const hives = byId('hives');
    byId('sched-home').append(byId('sched-form'));
    for (const key of Object.keys(schedBoxes)) delete schedBoxes[key];
    hives.replaceChildren();
    byId('office-empty').hidden = true;
    const ready = site && site.provisioned === true;
    if (!ready || !office || typeof office !== 'object') {
      byId('office-meta').textContent = '';
      byId('office-note').textContent = ready ? '目前讀不到站台的辦公室資料，請稍後重新載入。' : '站台建立後，這裡會顯示你辦公室裡的公司與角色。';
      return;
    }
    const list = Array.isArray(office.hives) ? office.hives : [];
    byId('office-meta').textContent = list.length ? list.length + ' 間公司' : '';
    byId('office-note').textContent = office.registry === 'unreadable'
      ? '.swarm/registry.yaml 格式有誤，請在站台裡請 AI 用 hive-list 檢查。'
      : (office.template === 'swarm' || list.length ? '' : '這個站台沒有使用辦公室樣板，可以直接在站台裡開始工作。');
    if (!list.length) { byId('office-empty').hidden = office.template !== 'swarm'; return; }
    for (const hive of list) {
      const card = el('article', 'hive');
      const head = el('div', 'hive-head');
      const name = el('div', 'hive-name');
      name.append(el('h3', '', hive.name || hive.id));
      name.append(el('span', 'hive-id', 'hives/' + hive.id));
      head.append(name);
      head.append(el('span', 'tag ' + (hive.enabled ? 'on' : 'off'), hive.enabled ? '排程開啟' : '排程關閉'));
      card.append(head);
      if (hive.description) card.append(el('p', 'hive-desc', hive.description));
      if (hive.exists === false) card.append(el('p', 'warn', 'registry 有登記，但站台裡找不到這個資料夾。'));
      const roles = el('ul', 'roles');
      for (const role of Array.isArray(hive.roles) ? hive.roles : []) {
        const chip = el('li', 'role' + (role.enabled === false ? ' off' : ''));
        chip.append(el('strong', '', roleNames[role.id] || role.id));
        chip.append(el('span', '', role.model ? role.model : '預設模型'));
        roles.append(chip);
      }
      card.append(roles);
      const facts = el('p', 'facts');
      facts.append(el('span', '', '程式碼：' + ({github:'GitHub', gitlab:'GitLab'}[hive.scm] || '未連接')));
      const ch = Array.isArray(hive.channels) ? hive.channels : [];
      facts.append(el('span', '', '通知：' + (ch.length ? ch.map(c => ({discord:'Discord', line:'LINE'}[c] || c)).join('、') : '未設定')));
      card.append(facts);
      card.append(renderSchedules(hive));
      if (editing && editing.hive === hive.id && !byId('sched-form').hidden) schedBoxes[hive.id].append(byId('sched-form'));
      hives.append(card);
    }
  }
  function renderModel(office, site, starter) {
    const model = office && office.default_model;
    const ready = site && site.provisioned === true;
    byId('starter-box').hidden = true;
    byId('model-howto').hidden = !ready || !(model && model.starter);
    if (!ready) { byId('model-current').textContent = '站台建立後，這裡會顯示站台正在用的模型。'; byId('model-meta').textContent = ''; return; }
    if (!model) { byId('model-current').textContent = '讀不到站台的預設模型。'; byId('model-meta').textContent = ''; return; }
    byId('model-meta').textContent = model.starter ? '平台起步模型' : '你自己的模型';
    byId('model-current').textContent = model.starter
      ? '目前使用平台借你的起步模型（' + model.model + '），有每月額度，適合試用。想用更強、不受額度限制的模型，照下面三步接上自己的。'
      : '目前預設模型：' + (model.provider ? model.provider + ' / ' : '') + model.model + '。費用由你自己的帳號計算。';
    if (model.starter && starter && typeof starter === 'object') {
      byId('starter-box').hidden = false;
      const used = Number(starter.used) || 0, limit = Number(starter.limit) || 0;
      const fmt = n => n >= 1000 ? Math.round(n / 1000) + 'k' : String(n);
      if (starter.mode === 'capped' && limit > 0) {
        // CSSOM, not a style attribute: allowed under the platform CSP.
        const bar = byId('starter-bar');
        if (bar.style) bar.style.width = Math.min(100, Math.round(used * 100 / limit)) + '%';
        byId('starter-usage').textContent = '本月已用 ' + fmt(used) + ' / ' + fmt(limit) + ' tokens';
      } else if (starter.mode === 'unlimited') {
        byId('starter-usage').textContent = '本月已用 ' + fmt(used) + ' tokens（不限額）';
      } else {
        byId('starter-usage').textContent = '起步模型目前未開放給你的站台，請接上自己的模型。';
      }
    }
  }
  async function run(acknowledge) {
    if (busy) return;
    busy = true;
    byId('acknowledge').disabled = true;
    byId('reload').disabled = true;
    byId('status').textContent = '';
    let data;
    try {
      const options = {credentials:'same-origin', cache:'no-store', redirect:'error'};
      if (acknowledge) {
        options.method = 'POST';
        options.headers = {'Content-Type':'application/json'};
        options.body = JSON.stringify({rules_version:version});
      }
      const response = await fetch('/customer/onboarding/' + (acknowledge ? 'acknowledge' : 'status'), options);
      if (!response.ok) throw new Error(response.status === 401 ? '登入已失效，請使用重新登入連結。' : '進度未確認成功，請重新載入；不要重複提交。');
      data = await response.json();
      if (typeof data.rules_version !== 'string' || typeof data.rules_acknowledged !== 'boolean' ||
          !Array.isArray(data.steps) || !Array.isArray(data.rules)) {
        data = null; throw new Error('進度格式錯誤，請重新載入。');
      }
      version = data.rules_version;
      const jobLabels = {not_requested:'等待管理員建立站台', queued:'已排入建立，通常需要幾分鐘', running:'正在建立站台…', reconciliation_required:'建立中斷，等待管理員確認（不會自動重建）', failed:'建立失敗，等待管理員處理', succeeded_simulated:'模擬任務完成，不代表已建立真實站台', succeeded_provisioned:'已就緒'};
      const job = data.site_job || {};
      byId('site-job').textContent = jobLabels[job.status] || '建站狀態尚未確認';
      byId('site-card').className = 'panel site-card' + (job.provisioned === true ? ' ready' : '');
      const live = ['queued','running'].includes(job.status);
      byId('auto-note').textContent = live ? '（建站期間會自動更新）' : '';
      renderSite(job);
      renderSteps(data.steps);
      office = data.office && typeof data.office === 'object' ? data.office : null;
      executor = !!(data.tasks && data.tasks.executor === true);
      renderOffice(data.office, job);
      renderTasks(data.tasks, office);
      renderModel(data.office, job, data.starter);
      byId('rules').replaceChildren();
      for (const rule of data.rules) byId('rules').append(el('li', '', rule));
      byId('rules-state').textContent = data.rules_acknowledged ? '已閱讀' : '請先閱讀';
      // Unread rules start open; once read they fold away.
      byId('rules-box').open = !data.rules_acknowledged;
      // The Copilot owns the "what to do now" line; do not write a second one.
    } catch (error) {
      byId('status').textContent = error.message;
      if (!data) byId('site-ready').hidden = true;
    }
    finally {
      busy = false; byId('reload').disabled = false;
      byId('acknowledge').disabled = !data || data.rules_acknowledged;
      schedule(data);
    }
  }
  function renderSite(job) {
    const ready = job && job.provisioned === true && typeof job.site_host === 'string';
    byId('site-ready').hidden = !ready;
    byId('site-link').textContent = ready ? 'https://' + job.site_host + '/' : '';
    if (!ready) { byId('site-password').hidden = true; return; }
    // Offer the site password form only where the platform really wires it.
    fetch('/customer/site-password', {credentials:'same-origin', cache:'no-store', redirect:'error'})
      .then(r => r.ok ? r.json() : null)
      .then(data => { byId('site-password').hidden = !(data && data.available === true); })
      .catch(() => { byId('site-password').hidden = true; });
  }
  byId('site-enter').addEventListener('click', () => {
    if (busy) return;
    busy = true;
    byId('site-enter').disabled = true;
    byId('site-secret').textContent = '正在準備進入…';
    fetch('/customer/site-entry', {method:'POST', credentials:'same-origin',
      cache:'no-store', redirect:'error', headers:{'Content-Type':'application/json'}, body:'{}'})
      .then(r => r.ok ? r.json() : Promise.reject(new Error(r.status === 409 ?
        '站台尚未就緒，請稍候再試。' : '目前無法進入，請重新載入進度。')))
      .then(data => {
        if (typeof data.url !== 'string') throw new Error('回應格式錯誤，請重新載入。');
        // One-time ticket: replace so it does not linger in history. Clear the
        // busy flag first — navigation may not happen (tests, blocked popups),
        // and a stuck flag would freeze every later refresh.
        busy = false;
        byId('site-secret').textContent = '';
        location.replace(data.url);
      })
      .catch(error => {
        byId('status').textContent = error.message;
        byId('site-secret').textContent = '';
        byId('site-enter').disabled = false;
        busy = false;
      });
  });
  // --- Site password ---------------------------------------------------
  // Sent straight to the platform, which verifies the customer and hands it to
  // the site controller; the page never stores or displays it.
  function sitePasswordMode() {
    const reuse = byId('site-password-reuse').checked;
    byId('site-password-new-fields').hidden = reuse;
    byId('site-password-reuse-fields').hidden = !reuse;
    return reuse;
  }
  byId('site-password-new').addEventListener('change', sitePasswordMode);
  byId('site-password-reuse').addEventListener('change', sitePasswordMode);
  let sitePasswordBusy = false;
  byId('site-password-form').addEventListener('submit', event => {
    event.preventDefault();
    if (sitePasswordBusy) return;
    const out = byId('site-password-status');
    const reuse = sitePasswordMode();
    let body;
    if (reuse) {
      const value = byId('site-password-platform').value;
      if (value.length < 12) { out.textContent = '平台密碼少於 12 個字，無法沿用；請改設一組新密碼。'; return; }
      body = {platform_password: value};
    } else {
      const value = byId('site-password-input').value;
      if (value.length < 12) { out.textContent = '站台密碼至少需要 12 個字。'; return; }
      if (value !== byId('site-password-confirm').value) { out.textContent = '兩次輸入的密碼不一致。'; return; }
      body = {password: value};
    }
    sitePasswordBusy = true;
    byId('site-password-save').disabled = true;
    out.textContent = '正在套用，站台重新啟動中（約 10–30 秒）…';
    const messages = {400:'密碼格式不符（至少 12 個字，不能含控制字元）。', 401:'平台密碼不正確，或登入已失效。',
      409:'站台尚未就緒。', 429:'剛剛才改過，或正在套用中，請稍候再試。',
      502:'套用失敗，站台已還原為原本的密碼。若持續失敗請聯絡管理員。'};
    fetch('/customer/site-password', {method:'POST', credentials:'same-origin', cache:'no-store',
      redirect:'error', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})
      .then(r => r.ok ? r.json() : Promise.reject(new Error(messages[r.status] || '套用結果未確認，請稍後再試。')))
      .then(data => {
        for (const id of ['site-password-input','site-password-confirm','site-password-platform']) byId(id).value = '';
        out.textContent = '已設定。可到 https://' + data.site_host + '/auth/login 以帳號 ' + data.username + ' 登入。';
      })
      .catch(error => { out.textContent = error.message; })
      .finally(() => { sitePasswordBusy = false; byId('site-password-save').disabled = false; });
  });
  // --- Copilot ---------------------------------------------------------
  // Reads the customer's real onboarding state and offers exactly one next
  // action. The action and its arguments come from the server together with a
  // signed token, so this page cannot invent or alter what will be performed.
  let copilotBusy = false, proposal = null;
  async function copilotApi(path, body) {
    const options = {credentials:'same-origin', cache:'no-store', redirect:'error'};
    if (body !== undefined) {
      options.method = 'POST';
      options.headers = {'Content-Type':'application/json'};
      options.body = JSON.stringify(body);
    }
    const response = await fetch('/customer/onboarding/copilot' + path, options);
    if (!response.ok) {
      throw new Error(response.status === 401 ? '登入已失效，請重新登入。'
        : response.status === 409 ? '這個建議已經過期（可能你在其他分頁改過設定）。已重新讀取最新狀態。'
        : '目前無法取得建議，請按「重新載入進度」再試。');
    }
    return response.json();
  }
  function renderCopilot(step) {
    byId('copilot-message').textContent = step.message;
    const button = byId('copilot-act');
    proposal = step.action || null;
    button.hidden = !proposal;
    if (proposal) button.textContent = proposal.label;
    // Mark the launcher only when an action is actually waiting.
    byId('copilot-launcher').dataset.attention = proposal ? 'yes' : '';
  }
  async function copilotRefresh() {
    try { renderCopilot(await copilotApi('')); }
    catch (error) { byId('copilot-message').textContent = error.message; }
  }
  byId('copilot-act').addEventListener('click', async () => {
    if (copilotBusy || !proposal) return;
    copilotBusy = true;
    byId('copilot-act').disabled = true;
    byId('copilot-status').textContent = '';
    try {
      // Send back exactly what was proposed, including its token.
      const step = await copilotApi('/perform', {kind: proposal.kind,
        arguments: proposal.arguments, token: proposal.token});
      renderCopilot(step);
      byId('copilot-status').textContent = '已完成。';
      await run(false);          // progress list and site panel may have moved
    } catch (error) {
      byId('copilot-status').textContent = error.message;
      await copilotRefresh();    // never leave a stale proposal on screen
    } finally {
      copilotBusy = false;
      byId('copilot-act').disabled = false;
    }
  });

  // --- conversation ------------------------------------------------------
  // The same support room an operator can join, so customer, Copilot and human
  // all share one thread. Replies may also carry the next allowed action.
  let roomId = null, chatBusy = false, pending = null, roomMode = 'ai';
  // Who answers, in words the customer can act on. Staff messages are stored
  // with sender_kind 'human'.
  const MODE_TEXT = {
    ai: '由 AI 助手回覆。',
    coassist: '平台人員已接手。「送出」只給平台人員；要 AI 回答請按「請 AI 助手回答」（輸入框的字會一起送出）。',
    human: '平台人員正在處理，會由真人回覆你。',
  };
  function renderMode(mode) {
    roomMode = MODE_TEXT[mode] ? mode : 'ai';
    byId('chat-mode').textContent = MODE_TEXT[roomMode];
    byId('chat-ask-ai').hidden = roomMode !== 'coassist';
  }
  let lastKind = null;
  // Asking again right after the AI answered only repeats that answer.
  function renderAskAi() {
    const button = byId('chat-ask-ai');
    const typed = !!byId('chat-input').value.trim();
    button.disabled = chatBusy || (!typed && (lastKind === 'ai' || lastKind === null));
  }
  function renderChat(messages) {
    lastKind = messages.length ? messages[messages.length - 1].sender_kind : null;
    const list = byId('chat-log');
    list.replaceChildren();
    for (const item of messages) {
      const row = document.createElement('li');
      row.className = 'msg ' + (item.sender_kind === 'ai' ? 'ai'
        : item.sender_kind === 'human' ? 'staff' : 'customer');
      const who = item.sender_kind === 'ai' ? '助手'
        : item.sender_kind === 'human' ? '平台人員' : '你';
      row.textContent = who + '：' + item.body;
      list.append(row);
    }
    list.scrollTop = list.scrollHeight;
    renderAskAi();
  }
  // One row by default; grow with the text up to the CSS max-height, then scroll.
  function fitInput() {
    const input = byId('chat-input');
    if (!input.style) return;
    input.style.height = 'auto';
    input.style.height = input.scrollHeight + 'px';
  }
  byId('chat-input').addEventListener('input', () => { fitInput(); renderAskAi(); });
  async function chatApi(path, body) {
    const options = {credentials:'same-origin', cache:'no-store', redirect:'error'};
    if (body !== undefined) {
      options.method = 'POST';
      options.headers = {'Content-Type':'application/json'};
      options.body = JSON.stringify(body);
    }
    const response = await fetch('/customer' + path, options);
    if (!response.ok) {
      throw new Error(response.status === 401 ? '登入已失效，請重新登入。'
        : response.status === 503 ? '助手目前無法回覆（平台模型未啟用）。上方的建議仍然可用。'
        : response.status === 409 ? '平台人員正在處理這個對話，請稍候。'
        : '訊息未確認送出，請重新整理確認，不要重複送出。');
    }
    return response.json();
  }
  async function loadChat() {
    try {
      if (!roomId) {
        const rooms = await chatApi('/rooms');
        if (!Array.isArray(rooms) || !rooms.length) return;
        roomId = rooms[0].id;
      }
      const room = await chatApi('/rooms/' + encodeURIComponent(roomId));
      renderMode(room.mode);
      renderChat(Array.isArray(room.messages) ? room.messages : []);
    } catch (error) { byId('chat-status').textContent = error.message; }
  }
  byId('chat-input').addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    sendChat();
  });
  byId('chat-form').addEventListener('submit', (event) => {
    event.preventDefault();
    return sendChat();
  });
  // Post what is typed (if anything). Returns false when nothing was sent.
  async function postTyped() {
    const body = byId('chat-input').value;
    if (!body.trim()) return false;
    // One client id per attempt: a retry must not post twice.
    if (!pending || pending.body !== body) pending = {body, id: crypto.randomUUID()};
    await chatApi('/rooms/' + encodeURIComponent(roomId) + '/messages',
                  {client_message_id: pending.id, body});
    pending = null;
    byId('chat-input').value = '';
    fitInput();
    return true;
  }
  async function chatWork(work) {
    if (chatBusy || !roomId) return;
    chatBusy = true;
    byId('chat-send').disabled = true; renderAskAi();
    byId('chat-status').textContent = '';
    try { await work(); }
    catch (error) {
      byId('chat-status').textContent = error.message;
      await loadChat();
    } finally {
      chatBusy = false;
      byId('chat-send').disabled = false; renderAskAi();
    }
  }
  function sendChat() {
    if (!byId('chat-input').value.trim()) return;
    return chatWork(async () => {
      await postTyped();
      await loadChat();
      // Only the AI mode answers on its own; staff decide otherwise.
      if (roomMode === 'ai') await askAssistant();
    });
  }
  async function askAssistant() {
    byId('chat-status').textContent = 'AI 助手回覆中…';
    const reply = await chatApi('/rooms/' + encodeURIComponent(roomId) + '/respond',
                                {requested: true});
    byId('chat-status').textContent = reply && reply.discarded
      ? '對話剛有新訊息，AI 這次的回覆已作廢，可以再按一次。' : '';
    await loadChat();
    // A reply may carry the next allowed action; show it on the button.
    if (reply && reply.suggestion) renderCopilot(reply.suggestion);
  }
  // Sends what is typed first, so the AI answers THAT, not an older message.
  byId('chat-ask-ai').addEventListener('click', () => chatWork(async () => {
    if (await postTyped()) await loadChat();
    await askAssistant();
  }));
  // While the panel is open and the tab visible, pick up staff replies.
  let chatTimer = null;
  function scheduleChat() {
    if (chatTimer) { clearTimeout(chatTimer); chatTimer = null; }
    if (panel.hidden || document.hidden) return;
    chatTimer = setTimeout(async () => {
      chatTimer = null;
      if (!chatBusy) await loadChat();
      scheduleChat();
    }, 5000);
  }

  // The widget starts collapsed: an assistant that covers the page it is
  // explaining is worse than no assistant.
  const launcher = byId('copilot-launcher'), panel = byId('copilot');
  function setOpen(open) {
    panel.hidden = !open;
    launcher.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      const log = byId('chat-log');
      log.scrollTop = log.scrollHeight;
      byId('chat-input').focus();
      loadChat();
    }
    scheduleChat();
  }
  launcher.addEventListener('click', () => setOpen(panel.hidden));
  byId('copilot-close').addEventListener('click', () => { setOpen(false); launcher.focus(); });
  addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !panel.hidden) { setOpen(false); launcher.focus(); }
  });

  byId('acknowledge').addEventListener('click', () => run(true));
  byId('reload').addEventListener('click', () => run(false));

  // Poll only while something is actually in progress, and stop when the tab is
  // hidden: an idle page must not keep hitting the server forever.
  let timer = null;
  function schedule(data) {
    if (timer) { clearTimeout(timer); timer = null; }
    const status = data && data.site_job && data.site_job.status;
    const waiting = status === 'queued' || status === 'running';
    if (!waiting || document.hidden) return;
    timer = setTimeout(() => { timer = null; run(false); }, 5000);
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { if (timer) { clearTimeout(timer); timer = null; } }
    else run(false);   // catch up on whatever happened while hidden
    scheduleChat();
  });
  run(false);
  copilotRefresh();
  loadChat();
})();
"""


CSS = """
body.app main { max-width: 60rem; padding-bottom: 7rem; }
.nav-links { display: flex; align-items: center; gap: 1.2rem; }
.nav-links a:not(.nav-login) { color: var(--muted); text-decoration: none; font-size: .92rem; }
.nav-links a:not(.nav-login):hover { color: var(--text); }
.hero.compact { padding: 2.2rem 0 1.6rem; }
.hero.compact h1 { font-size: clamp(2rem, 5vw, 2.8rem); margin-bottom: .8rem; }
.hero.compact .lede { font-size: 1rem; }
.panel { padding: 1.6rem 1.8rem; margin-bottom: 1.2rem; }
.panel h2 { margin: 0; font-size: 1.2rem; }
.hint { color: var(--muted); font-size: .88rem; margin: .4rem 0 0; }
.meta { color: var(--muted); font-size: .85rem; }
.section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin-bottom: .6rem; }
/* Site card */
.site-card.ready { border-color: #f5b30188; box-shadow: 0 0 40px #f5b3011a; }
.site-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 1rem 2rem; }
.site-state { margin: .35rem 0 0; color: #c3ccd8; }
.site-card.ready .site-state { color: var(--honey-2); font-weight: 600; }
.site-card.ready .site-state::before { content: ""; display: inline-block; width: .55rem; height: .55rem;
  margin-right: .5rem; border-radius: 50%; background: #4ade80; box-shadow: 0 0 8px #4ade80; vertical-align: .05rem; }
.site-link { margin: .2rem 0 0; font: .92rem ui-monospace, Menlo, monospace; color: var(--muted); overflow-wrap: anywhere; }
.site-link:empty, #site-secret:empty { display: none; }
.site-actions { text-align: right; }
.site-actions .hint { margin-top: .5rem; }
#site-enter { font-size: 1.05rem; padding: 1rem 1.7rem; }
/* Progress */
.panel.slim { padding: 1.1rem 1.4rem; }
.small-title { font-size: .95rem !important; color: var(--muted); font-weight: 600; margin-bottom: .7rem !important; }
.progress { list-style: none; padding: 0; margin: 0; display: grid; gap: .5rem;
  grid-template-columns: repeat(5, minmax(0, 1fr)); counter-reset: step; }
.step { counter-increment: step; position: relative; padding: .6rem .7rem .6rem 2.4rem; min-width: 0;
  border: 1px solid var(--line); border-radius: .7rem; background: #0b0f15; }
.step::before { content: counter(step); position: absolute; left: .65rem; top: .7rem; width: 1.3rem; height: 1.3rem;
  border-radius: 50%; display: grid; place-items: center; font: 700 .75rem/1 ui-monospace, Menlo, monospace;
  color: var(--muted); border: 1px solid var(--line); }
.step-name { display: block; font-size: .9rem; font-weight: 600; color: var(--text); }
.step-state { display: block; font-size: .78rem; color: var(--muted); line-height: 1.35; }
.step.s-complete { border-color: #f5b30155; }
.step.s-complete::before { content: "\\2713"; color: #1a1200; background: var(--honey); border-color: var(--honey); }
.step.s-pending, .step.s-in_progress { border-color: #3dd6f566; background: #3dd6f50a; }
.step.s-pending::before, .step.s-in_progress::before { color: var(--cyan); border-color: var(--cyan); }
.step.s-pending .step-state, .step.s-in_progress .step-state { color: #a9ecfa; }
.step.s-attention { border-color: #ff8a3d88; }
.step.s-blocked, .step.s-skipped { opacity: .55; }
/* Office */
.hives { display: grid; gap: .9rem; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr)); }
.hive { border: 1px solid var(--line); border-radius: .9rem; padding: 1rem 1.1rem; background: #0b0f15; }
.hive-head { display: flex; align-items: center; justify-content: space-between; gap: .6rem; }
.hive h3 { margin: 0; font-size: 1.05rem; }
.hive-id { margin: .1rem 0 .4rem; font: .8rem ui-monospace, Menlo, monospace; color: var(--muted); }
.hive-desc { margin: 0 0 .5rem; font-size: .9rem; color: #c3ccd8; }
.tag { flex: none; font-size: .72rem; padding: .15rem .55rem; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); }
.tag.on { color: #4ade80; border-color: #4ade8055; }
.roles { list-style: none; padding: 0; margin: .5rem 0; display: flex; flex-wrap: wrap; gap: .4rem; }
.role { display: flex; flex-direction: column; padding: .35rem .6rem; border-radius: .5rem;
  background: #f5b3010d; border: 1px solid #f5b30133; font-size: .8rem; line-height: 1.35; }
.role strong { color: var(--honey-2); font-size: .85rem; }
.role span { color: var(--muted); }
.role .cron { color: #a9ecfa; font-family: ui-monospace, Menlo, monospace; font-size: .74rem; }
.role.off { opacity: .5; }
.facts { display: flex; flex-wrap: wrap; gap: .3rem 1rem; margin: .4rem 0 0; font-size: .82rem; color: var(--muted); }
.warn { color: #ffb37a; font-size: .85rem; margin: .2rem 0; }
.empty { border: 1px dashed #3dd6f555; border-radius: .9rem; padding: 1rem 1.2rem; background: #3dd6f506; }
.empty-title { margin: 0 0 .4rem; font-weight: 600; }
.howto { margin: .4rem 0 0; padding-left: 1.3rem; color: #c3ccd8; font-size: .92rem; }
.howto li { margin: .25rem 0; }
.howto strong { color: var(--honey-2); }
.sched { margin-top: 1rem; padding-top: .9rem; border-top: 1px solid var(--line); }
.sched-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .6rem; }
.sched-head h4 { margin: 0; font-size: 1rem; display: flex; align-items: center; gap: .45rem; }
.sched-head .count { font-size: .72rem; min-width: 1.4rem; text-align: center; padding: .05rem .4rem; border-radius: 999px;
  background: #f5b3011f; color: var(--honey-2); }
.sched-actions { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem; }
.btn.small { font-size: .85rem; padding: .45rem .9rem; border-radius: .55rem; }
.switch { display: inline-flex; align-items: center; gap: .5rem; font: inherit; font-size: .82rem; color: var(--muted);
  background: none; border: 0; padding: .25rem; cursor: pointer; }
.switch .knob { position: relative; width: 2.3rem; height: 1.3rem; border-radius: 999px; background: #2a3442; transition: background .15s; flex: none; }
.switch .knob::after { content: ""; position: absolute; top: .15rem; left: .15rem; width: 1rem; height: 1rem; border-radius: 50%;
  background: #c3ccd8; transition: transform .15s; }
.switch.on { color: #4ade80; }
.switch.on .knob { background: #16a34a; }
.switch.on .knob::after { transform: translateX(1rem); background: #fff; }
.switch:focus-visible { outline: 2px solid var(--honey); outline-offset: 2px; border-radius: .4rem; }
.sched-empty { margin: .7rem 0 0; padding: .9rem 1rem; border: 1px dashed #2a3442; border-radius: .6rem; color: var(--muted); font-size: .88rem; }
.sched-list { list-style: none; padding: 0; margin: .7rem 0 0; display: grid; gap: .5rem; }
.sched-item { display: grid; grid-template-columns: 7.5rem minmax(0, 1fr) auto; align-items: center; gap: 1rem;
  padding: .75rem .9rem; border: 1px solid var(--line); border-left: 3px solid #3a4452; border-radius: .6rem; background: #ffffff04; }
.sched-item.st-ok { border-left-color: #4ade80; }
.sched-item.st-warn { border-left-color: #ff8a3d; }
.sched-time { display: flex; flex-direction: column; line-height: 1.25; }
.sched-time .when { font-size: .78rem; color: var(--muted); }
.sched-time strong { color: var(--honey-2); font-size: 1.15rem; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.sched-main { display: flex; flex-direction: column; gap: .25rem; min-width: 0; }
.sched-what { font-size: .92rem; color: var(--text); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.sched-who { display: flex; flex-wrap: wrap; gap: .3rem; font: .74rem ui-monospace, Menlo, monospace; color: var(--muted); }
.sched-who span { padding: .05rem .4rem; border-radius: .3rem; background: #ffffff0a; overflow-wrap: anywhere; }
.sched-who .who-role { color: var(--honey-2); background: #f5b30114; font-family: inherit; }
.sched-side { display: flex; flex-direction: column; align-items: flex-end; gap: .35rem; }
.row-actions { display: flex; gap: .2rem; }
.link { font: inherit; font-size: .82rem; background: none; border: 0; color: #a9ecfa; padding: .2rem .45rem; border-radius: .35rem; cursor: pointer; }
.link:hover, .link:focus-visible { background: #3dd6f514; outline: none; }
.link.danger { color: var(--muted); }
.link.danger:hover, .link.danger:focus-visible { color: #ffb4b4; background: #ff6b6b14; }
.hive-name { display: flex; align-items: baseline; flex-wrap: wrap; gap: .1rem .6rem; min-width: 0; }
.hive-name .hive-id { margin: 0; }
.form-group { margin: .2rem 0 .5rem; font-size: .78rem; letter-spacing: .06em; color: var(--muted); }
.preview { margin: .7rem 0 0; padding: .55rem .8rem; border-radius: .5rem; background: #3dd6f50d; color: #a9ecfa; font-size: .88rem; }
.preview:empty { display: none; }
.tag.ok { color: #4ade80; border-color: #4ade8055; }
.tag.warn { color: #ffb37a; border-color: #ff8a3d66; }
.sched-form { margin-top: .8rem; padding: 1.1rem 1.2rem; border: 1px solid #f5b30166; border-radius: .8rem; background: #06090d;
  box-shadow: 0 0 0 4px #f5b30110; }
.sched-form h3 { margin: 0 0 .8rem; font-size: 1.05rem; }
.form-grid { display: grid; gap: .7rem 1rem; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr)); margin-bottom: .8rem; }
.sched-form label { display: flex; flex-direction: column; gap: .3rem; font-size: .85rem; color: #c3ccd8; }
.sched-form label[hidden] { display: none; }
.sched-form select, .sched-form input, .sched-form textarea { font: inherit; font-size: .95rem; padding: .55rem .7rem;
  border-radius: .5rem; color: var(--text); background: #06090d; border: 1px solid #2a3442; width: 100%; }
.sched-form textarea { resize: vertical; margin-top: .3rem; }
.sched-form code { color: var(--honey-2); }
.sched-form .row { display: flex; gap: .6rem; margin-top: .8rem; }
#sched-status { min-height: 1rem; color: #ffb37a; font-size: .88rem; }
.hives { grid-template-columns: 1fr !important; }
.schedule-note strong { color: var(--honey-2); }
.offers { margin: .4rem 0 1rem; padding: 1rem 1.1rem; border: 1px solid #3dd6f540; border-radius: .9rem; background: #3dd6f508; }
.offers-head { display: flex; justify-content: space-between; align-items: baseline; }
.offers-head h3 { margin: 0; font-size: 1rem; }
.offer-list { list-style: none; padding: 0; margin: .6rem 0 0; display: grid; gap: .5rem; }
.offer { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .8rem; padding: .75rem .9rem;
  border: 1px solid var(--line); border-radius: .6rem; background: #06090d; }
.offer-main { display: flex; flex-direction: column; gap: .2rem; min-width: 0; flex: 1 1 18rem; }
.offer-when { font-size: .8rem; color: var(--honey-2); }
.offer-what { margin: 0; font-size: .88rem; color: var(--muted); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.offer-act { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
.offer-hive { font: inherit; font-size: .85rem; padding: .4rem .5rem; border-radius: .5rem; background: #0b0f15; color: var(--text); border: 1px solid var(--line); }
.runs { margin-top: 1rem; border: 1px solid var(--line); border-radius: .6rem; padding: .6rem .9rem; }
.runs summary { cursor: pointer; font-weight: 600; }
.run-list { list-style: none; padding: 0; margin: .6rem 0 0; display: grid; gap: .4rem; }
.run { padding: .5rem 0; border-top: 1px solid var(--line); }
.run-head { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; }
.run-out summary { font-size: .82rem; color: #a9ecfa; font-weight: 400; margin-top: .3rem; }
.run-out pre { white-space: pre-wrap; overflow-wrap: anywhere; font: .82rem/1.55 ui-monospace, Menlo, monospace; background: #06090d;
  padding: .7rem; border-radius: .5rem; max-height: 22rem; overflow: auto; }
.schedule-note { display: flex; gap: .6rem; align-items: flex-start; margin: 1rem 0 0; padding: .7rem .9rem; border-radius: .6rem;
  background: #ffffff05; border: 1px solid var(--line); font-size: .82rem; color: var(--muted); }
.note-icon { flex: none; width: 1.2rem; height: 1.2rem; border-radius: 50%; border: 1px solid var(--muted); display: grid; place-items: center;
  font: italic 600 .72rem Georgia, serif; }
#office-note:empty { display: none; }
/* Model */
.model-current { margin: 0; color: #c3ccd8; }
.starter { margin-top: .8rem; max-width: 26rem; }
.meter { height: .45rem; border-radius: 999px; background: #ffffff10; overflow: hidden; }
.meter span { display: block; height: 100%; width: 0; background: linear-gradient(90deg, var(--honey), #ff9f1a); }
.starter .hint { margin-top: .35rem; }
/* Rules */
.rules-box summary { display: flex; align-items: baseline; justify-content: space-between; cursor: pointer; list-style: none; }
.rules-box summary::-webkit-details-marker { display: none; }
.rules-box summary h2::after { content: " ▾"; color: var(--muted); font-size: .9rem; }
.rules-box[open] summary h2::after { content: " ▴"; }
.rules { padding-left: 1.2rem; color: #c3ccd8; margin: .8rem 0 1rem; }
.rules li { margin-block: .35rem; }
/* Site password */
.sitepw { margin-top: 1rem; border-top: 1px solid var(--line); padding-top: .9rem; }
.sitepw summary { cursor: pointer; color: var(--muted); font-size: .9rem; }
.sitepw summary:hover { color: var(--honey-2); }
.sitepw code { color: var(--honey-2); }
.sitepw form { max-width: 28rem; }
.choice { border: 0; padding: 0; margin: 1rem 0 0; }
.choice legend { font-size: .92rem; color: #c3ccd8; margin-bottom: .3rem; }
label.inline { display: flex; align-items: center; gap: .5rem; margin: .35rem 0; }
label.inline input { width: auto; }
#site-password-save { margin-top: 1.2rem; }
#site-password-status { min-height: 1.2rem; color: var(--honey-2); overflow-wrap: anywhere; }
.toolbar { display: flex; align-items: center; gap: 1rem; margin-top: .6rem; color: var(--muted); font-size: .9rem; }
#status:empty { display: none; }
.btn.ghost:disabled, .btn.primary:disabled { opacity: .45; cursor: not-allowed; box-shadow: none; }
@media (max-width: 52rem) {
  .progress { grid-template-columns: 1fr; }
  .step { padding: .5rem .7rem .5rem 2.4rem; display: flex; align-items: baseline; justify-content: space-between; gap: .6rem; }
  .step::before { top: .55rem; }
  .step-state { text-align: right; }
}
@media (max-width: 40rem) {
  .nav-links a:not(.nav-login) { display: none; }
  .panel { padding: 1.2rem 1.1rem; }
  .site-actions { text-align: left; width: 100%; }
  #site-enter { width: 100%; }
  #copilot-launcher span { display: none; }
  #copilot-launcher { padding: .9rem; }
  .sched-item { grid-template-columns: 1fr; gap: .5rem; }
  .sched-time { flex-direction: row; align-items: baseline; gap: .5rem; }
  .sched-side { flex-direction: row; align-items: center; justify-content: space-between; }
}

#copilot-launcher { position: fixed; right: 1.2rem; bottom: 1.2rem; z-index: 30; display: flex;
  align-items: center; gap: .5rem; font-family: inherit; font-size: .95rem; font-weight: 700; padding: .85rem 1.25rem;
  border-radius: 999px; border: 1px solid #f5b30199; cursor: pointer; color: #1a1200;
  background: linear-gradient(135deg, var(--honey), #ff9f1a); box-shadow: 0 10px 34px #f5b30144; }
#copilot-launcher svg polygon { fill: none; stroke: #1a1200; stroke-width: 4; }
#copilot-launcher[data-attention="yes"]::after { content: ""; width: .55rem; height: .55rem;
  border-radius: 50%; background: var(--cyan); box-shadow: 0 0 10px var(--cyan); }
#copilot { position: fixed; right: 1.2rem; bottom: 5rem; z-index: 31; display: flex; flex-direction: column;
  width: min(27rem, calc(100vw - 2.4rem)); height: min(40rem, calc(100vh - 7rem));
  overflow: hidden; padding: 1.2rem; border-radius: 1rem; background: #0b0f15f2;
  border: 1px solid #f5b30155; box-shadow: 0 20px 60px #000c, 0 0 40px #f5b30114;
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }
#copilot[hidden] { display: none; }
#copilot > * { flex: none; }
#copilot header { display: flex; align-items: flex-start; justify-content: space-between; }
#copilot .eyebrow { margin: 0 0 .3rem; }
#copilot h2 { margin: 0; font-size: 1.15rem; }
#copilot-close { background: none; border: 1px solid var(--line); color: var(--muted); border-radius: .4rem;
  width: 2rem; height: 2rem; font-size: 1.1rem; cursor: pointer; }
#copilot-close:hover { color: var(--text); border-color: var(--honey); }
#copilot-message { margin: .9rem 0 .7rem; padding: .8rem .9rem; border-radius: .7rem;
  background: #f5b3010f; border: 1px solid #f5b30133; color: #efe3bf; font-size: .95rem;
  max-height: 7rem; overflow-y: auto; }
#copilot-act { width: 100%; margin-bottom: .4rem; }
#copilot > #chat-log { flex: 1 1 auto; min-height: 6rem; overflow-y: auto; overscroll-behavior: contain;
  list-style: none; padding: 0 .2rem 0 0; margin: .6rem 0; display: flex; flex-direction: column; gap: .5rem; }
.msg { flex: none; }
.msg { max-width: 88%; padding: .6rem .8rem; border-radius: .8rem; font-size: .92rem;
  white-space: pre-wrap; overflow-wrap: anywhere; }
.msg.customer { align-self: flex-end; background: #f5b30122; border: 1px solid #f5b30144;
  color: #fbeec6; border-bottom-right-radius: .2rem; }
.msg.ai { align-self: flex-start; background: #3dd6f511; border: 1px solid #3dd6f533;
  color: #d5f5fc; border-bottom-left-radius: .2rem; }
.msg.staff { align-self: flex-start; background: #ffffff14; border: 1px solid #f5b30166; color: var(--text); }
#chat-mode { margin: 0; font-size: .8rem; color: var(--muted); }
#chat-ask-ai { margin-top: .4rem; font-size: .85rem; }
#chat-form label { margin-top: .4rem; font-size: .82rem; color: var(--muted); }
.composer { display: flex; gap: .5rem; align-items: flex-end; }
#chat-input { flex: 1; min-height: 2.75rem; max-height: 8rem; resize: none; overflow-y: auto;
  font-family: inherit; font-size: 1rem; line-height: 1.45; padding: .6rem .8rem;
  border-radius: .6rem; color: var(--text); background: #06090d; border: 1px solid #2a3442; }
#chat-input:focus-visible { outline: 2px solid var(--honey); outline-offset: 1px; border-color: transparent; }
#chat-send { height: 2.75rem; padding: 0 1.1rem; }
#chat-status, #copilot-status { min-height: 1rem; margin: .3rem 0; color: var(--honey-2); font-size: .85rem; }
#copilot .hint { font-size: .78rem; margin: .4rem 0 0; }
"""


def install_onboarding_ui(app):
    if getattr(app.state, 'support_onboarding', None) is None:
        raise ValueError('onboarding service required')
    for path, content, media in (
        ('/customer/onboarding', PAGE, 'text/html'),
        ('/customer/ui/onboarding.js', JS, 'text/javascript'),
        ('/customer/ui/onboarding.css', CSS, 'text/css'),
    ):
        def build(content, media):
            async def endpoint(request: Request):
                try:
                    actor = await app.state.support_actor_for(request)
                    # Validate capability, not only the hook's return type.
                    await run_in_threadpool(app.state.support_onboarding.core.authorize_customer, actor)
                except Exception:
                    if media == 'text/html':
                        return RedirectResponse('/customer/welcome', status_code=303,
                                                headers=SENSITIVE_RESPONSE_HEADERS)
                    return _error(401)
                if request.scope.get('query_string'):
                    return _error(400)
                return Response(content, media_type=media, headers=SENSITIVE_RESPONSE_HEADERS)
            return endpoint
        app.add_api_route(path, build(content, media), methods=['GET'], include_in_schema=False)
    return app
