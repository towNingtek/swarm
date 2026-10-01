"""Explicit opt-in, offline UI for the support_chat_app factories only.

Install before serving. No server/deployment wiring and no authentication adapter
is supplied here: protected resources re-use the factory's support_actor_for
hook on every request, in addition to its unchanged edge middleware. All assets
are same-origin. Invitations are transient JS memory; session identity stays in
the API's Secure HttpOnly cookie. URL scrubbing cannot erase upstream access logs.
"""
from fastapi import Request
from starlette.responses import HTMLResponse, Response

from support_app import _error
from support_core import SupportError
from support_http_policy import SENSITIVE_RESPONSE_HEADERS


CSS = """
body { font: 1rem system-ui, sans-serif; max-width: 64rem; margin: 2rem auto; padding: 1rem; color: #192733; background: #f5f7fa; }
main, section { background: white; padding: 1rem; margin-block: 1rem; border-radius: .5rem; }
label { display: block; margin-block: .7rem; }
input, textarea, select, button { font: inherit; padding: .6rem; max-width: 100%; }
textarea { display: block; width: 90%; min-height: 6rem; }
button { margin: .3rem; cursor: pointer; }
.badge { background: #fff0bb; border: 1px solid #826200; padding: .3rem; display: inline-block; }
#messages { white-space: pre-wrap; overflow-wrap: anywhere; }
#status { min-height: 1.5rem; }
/* The pending action must be the first thing a customer sees, not a line of
   body text below a list of steps. */
/* Floating assistant. Fixed to the corner so it is reachable from any page,
   and constrained in height so it never covers the content it explains. */
#copilot-launcher { position: fixed; right: 1rem; bottom: 1rem; z-index: 30;
  border-radius: 2rem; padding: .7rem 1.2rem; background: #1d6fd0; color: white;
  border: none; box-shadow: 0 2px 8px rgba(0,0,0,.25); }
#copilot-launcher[data-attention="yes"]::after { content: " ●"; color: #ffd24a; }
#copilot { position: fixed; right: 1rem; bottom: 4.5rem; z-index: 31;
  width: min(26rem, calc(100vw - 2rem)); max-height: min(34rem, calc(100vh - 7rem));
  overflow-y: auto; background: white; border: 1px solid #c8d2dc;
  border-radius: .6rem; box-shadow: 0 6px 24px rgba(0,0,0,.2); padding: 1rem; }
#copilot header { display: flex; align-items: baseline; justify-content: space-between; }
#copilot h2 { margin: 0; font-size: 1.05rem; }
#copilot-close { background: none; border: none; font-size: 1.3rem; cursor: pointer; }
#copilot-message { margin-block: .5rem; }
#copilot-act { font-size: 1rem; padding: .55rem 1rem; background: #1d6fd0;
  color: white; border: none; border-radius: .3rem; }
#chat-log { padding-left: 1.1rem; margin: .6rem 0; }
#chat-log li { margin-block: .35rem; overflow-wrap: anywhere; }
#chat-input { width: 100%; }
.hint { color: #55606b; font-size: .85rem; }
@media (prefers-reduced-motion: no-preference) { #copilot { scroll-behavior: smooth; } }
#site-ready { border-left: .35rem solid #1a7f47; background: #eefaf2; }
#site-link a { font-size: 1.15rem; font-weight: 600; overflow-wrap: anywhere; }
#site-secret { font-family: ui-monospace, monospace; overflow-wrap: anywhere; }
.warning { background: #fff0bb; border: 1px solid #826200; padding: .6rem; }
"""

WELCOME_JS = r"""'use strict';
(() => {
  let invite = new URLSearchParams(location.search).get('token') || '';
  const fromInvite = Boolean(invite);
  history.replaceState(null, '', location.pathname);
  const form = document.getElementById('credentials');
  const password = document.getElementById('password');
  const status = document.getElementById('status');
  document.documentElement.lang = 'zh-Hant';
  document.title = 'Swarm · ' + (invite ? '啟用工作空間' : '登入工作空間');
  document.getElementById('page-title').textContent = invite ? '啟用你的工作空間' : '登入工作空間';
  document.getElementById('intro').textContent = invite ? '這是註冊：請自行設定新的帳號與密碼（至少 8 個字）。邀請碼只用於這次啟用，之後請用這組帳密登入。' : '請輸入你先前設定的帳號與密碼。若邀請連結已過期或失效，請聯絡平台管理員重新發送。';
  document.getElementById('action').textContent = invite ? '建立帳號並啟用' : '登入';
  document.getElementById('invite-required').hidden = fromInvite;
  form.hidden = false;
  // On the landing page the form sits below the introduction; an invitee came
  // here to activate, so take them straight to it.
  const access = document.getElementById('access');
  if (fromInvite && access && access.scrollIntoView) access.scrollIntoView();
  const confirmRow = document.getElementById('confirm-row');
  const confirmField = document.getElementById('password-confirm');
  // Only when creating a password: confirming an existing one adds no value.
  confirmRow.hidden = !invite;
  confirmField.required = Boolean(invite);
  password.autocomplete = invite ? 'new-password' : 'current-password';
  addEventListener('pagehide', () => {
    invite = ''; password.value = ''; confirmField.value = '';
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = document.getElementById('action');
    if (button.disabled) return;
    button.disabled = true;
    if (invite && password.value !== confirmField.value) {
      status.textContent = '兩次輸入的密碼不一致，請重新輸入。';
      button.disabled = false;
      return;
    }
    let body = {username: document.getElementById('username').value, password: password.value};
    password.value = ''; confirmField.value = '';
    const activating = Boolean(invite);
    if (activating) body.invite = invite;
    try {
      const response = await fetch(activating ? '/customer/activate' : '/customer/login', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
      });
      if (!response.ok) {
        // Distinguish the cases: one fixed message made an already-activated
        // account look like a wrong password, with no way to act on it.
        if (response.status === 429) throw new Error('嘗試次數過多，請稍後再試。');
        if (response.status === 409) {
          throw new Error('這個站台已經啟用過了。請改用「重新登入」，以你先前設定的帳號密碼登入；忘記密碼請聯絡管理員。');
        }
        throw new Error(activating
          ? '啟用失敗：邀請可能已使用或過期，或帳號格式不符。請確認後再試，或聯絡管理員重新發送邀請。'
          : '帳號或密碼不正確。請確認後再試，或聯絡管理員。');
      }
      invite = '';
      location.replace('/customer/onboarding');
    } catch (error) {
      status.textContent = error.message || '無法連線。請檢查網路後再試。';
    } finally {
      body.password = ''; delete body.invite; body = null;
      button.disabled = false;
    }
  });
})();
"""

CHAT_JS = r"""'use strict';
(() => {
  const admin = document.body.dataset.role === 'admin';
  const prefix = admin ? '/admin' : '/customer';
  const byId = (id) => document.getElementById(id);
  const status = byId('status');
  let current = null;
  let pending = null;
  let busy = false;
  async function api(path, body) {
    const options = {credentials: 'same-origin', cache: 'no-store', redirect: 'error'};
    if (body !== undefined) {
      options.method = 'POST';
      options.headers = {'Content-Type': 'application/json'};
      options.body = JSON.stringify(body);
    }
    const response = await fetch(prefix + path, options);
    if (!response.ok) {
      const labels = {400: '請求格式不正確，請重新整理後再試。', 401: '登入已失效，請重新登入。', 403: '目前無權執行此操作。',
        409: '對話狀態已更新，請重新載入後再試。', 413: '內容太長，請縮短後再送出。',
        429: '操作太頻繁，請稍後再試。', 503: '目前沒有啟用客服 AI，請聯絡管理員。'};
      throw new Error(labels[response.status] || '連線失敗，請檢查網路後重新整理。');
    }
    return response.json();
  }
  async function task(action) {
    if (busy) return;
    busy = true;
    document.querySelectorAll('button, select').forEach((el) => { el.disabled = true; });
    status.textContent = '';
    try { await action(); } catch (error) { status.textContent = error.message; }
    finally {
      busy = false;
      document.querySelectorAll('button, select').forEach((el) => { el.disabled = false; });
    }
  }
  function path(id) { return '/rooms/' + encodeURIComponent(id); }
  const labels = {customer: '客戶', human: '平台管理員', ai: '客服助手'};
  async function read(id) {
    current = null;
    byId('messages').replaceChildren();
    byId('room-empty').hidden = true;
    byId('room-state').textContent = '';
    let after = 0;
    let room;
    do {
      room = await api(path(id) + '?after=' + after);
      for (const message of room.messages) {
        if (!Number.isSafeInteger(message.seq) || message.seq <= after) throw new Error('訊息順序驗證失敗，請重新載入。');
        const item = document.createElement('li');
        item.textContent = (labels[message.sender_kind] || '參與者') + '：' + message.body;
        byId('messages').append(item);
        after = message.seq;
      }
    } while (room.messages.length === 100);
    current = room;
    const modeLabels = {ai: 'AI 引導', coassist: '共同協助', human: '真人接手'};
    byId('room-state').textContent = '目前模式：' + (modeLabels[room.mode] || '未知') + '｜狀態版本：' + room.epoch;
    byId('room-empty').hidden = room.messages.length > 0;
    if (admin) byId('mode').value = room.mode;
  }
  async function list() {
    const rooms = await api('/rooms');
    const previous = current && current.id;
    current = null;
    byId('rooms').replaceChildren();
    byId('messages').replaceChildren();
    byId('room-state').textContent = '';
    for (const room of rooms) {
      const option = document.createElement('option');
      option.value = room.id;
      option.textContent = (admin ? room.tenant_id + ' / ' : '') + room.id;
      byId('rooms').append(option);
    }
    if (!rooms.length) { status.textContent = admin ? '目前沒有待支援的對話。' : '目前尚未建立客服對話，請聯絡平台管理員。'; byId('room-empty').hidden = false; return; }
    byId('room-empty').hidden = true;
    byId('rooms').value = rooms.some((room) => room.id === previous) ? previous : rooms[0].id;
    await read(byId('rooms').value);
  }
  byId('refresh').addEventListener('click', () => task(list));
  byId('rooms').addEventListener('change', () => task(() => read(byId('rooms').value)));
  byId('compose').addEventListener('submit', (event) => {
    event.preventDefault();
    task(async () => {
      if (!current) throw new Error('請先選擇一段客服對話。');
      const id = current.id;
      const body = byId('message').value;
      if (!body.trim()) throw new Error('請先輸入訊息。');
      if (!pending || pending.room !== id || pending.body !== body) {
        pending = {room: id, body, id: crypto.randomUUID()};
      }
      await api(path(id) + '/messages', {client_message_id: pending.id, body});
      pending = null;
      byId('message').value = '';
      await read(id);
    });
  });
  if (admin) {
    byId('control').addEventListener('click', () => task(async () => {
      if (!current) throw new Error('請先選擇一段客服對話。');
      const id = current.id;
      await api(path(id) + '/control', {mode: byId('mode').value, expected_epoch: current.epoch});
      await read(id);
    }));
  } else {
    byId('respond').addEventListener('click', () => task(async () => {
      if (!current) throw new Error('請先選擇一段客服對話。');
      const id = current.id;
      await api(path(id) + '/respond', {requested: true});
      await read(id);
    }));
    byId('logout').addEventListener('click', () => task(async () => {
      await api('/logout', {});
      current = null;
      byId('messages').replaceChildren();
      location.replace('/customer/welcome');
    }));
  }
  task(list);
})();
"""


def _page(role, welcome=False):
    prefix = '/' + role + '/ui'
    title = ('歡迎使用工作空間' if welcome else ('客服支援中心' if role == 'admin' else '我的工作空間'))
    if welcome:
        content = '''<p id="intro">邀請連結會提供一次性啟用碼；啟用後即可登入此工作空間。</p><p>管理員會在邀請時告知本站用途與支援範圍。請勿在聊天訊息中貼上密碼或 API 金鑰；憑證請只填入本站專用設定頁。</p><p id="invite-required" hidden>已啟用帳號可在下方重新登入，繼續設定站台。尚未啟用者請使用管理員提供的有效邀請連結。</p><form id="credentials" autocomplete="off">
<label for="username">帳號</label><input id="username" name="username" required maxlength="64" pattern="[A-Za-z0-9_.-]{1,64}" autocomplete="username">
<label for="password">密碼</label><input id="password" name="password" type="password" required minlength="8" autocomplete="new-password">
<p id="confirm-row" hidden><label for="password-confirm">再次輸入密碼</label><input id="password-confirm" name="password-confirm" type="password" minlength="8" autocomplete="new-password"></p>
<button id="action" type="submit">繼續</button></form>'''
    else:
        control = '''<fieldset><legend>支援模式</legend><label for="mode">選擇模式</label><select id="mode"><option value="ai">AI 引導</option><option value="coassist">共同協助</option><option value="human">真人接手</option></select><button id="control" type="button">更新模式</button></fieldset>''' if role == 'admin' else '''<a href="/customer/onboarding">繼續設定站台</a><button id="respond" type="button">請求 AI 協助（測試模型）</button><button id="logout" type="button">登出</button>'''
        content = '''<p class="badge">目前僅使用測試模型；回覆為模擬內容，並非正式 AI 服務。</p>
<p>客服對話可由授權的平台支援人員查看。請勿在聊天中輸入密碼、API 金鑰或其他機密；需要設定模型時請使用工作空間內的專用設定頁。</p>
<label for="rooms">客服對話</label><select id="rooms"></select><button id="refresh" type="button">重新載入對話</button>
<p id="room-state"></p><p id="room-empty" hidden>尚無訊息。你可以在下方傳送第一則訊息。</p><ol id="messages" aria-label="對話訊息"></ol>
<form id="compose"><label for="message">輸入訊息</label><textarea id="message" required maxlength="8000"></textarea><button type="submit">送出訊息</button></form>''' + control
    script = 'welcome.js' if welcome else 'chat.js'
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title><link rel="stylesheet" href="{prefix}/style.css"><script defer src="{prefix}/{script}"></script></head><body data-role="{role}" data-invite=""><main><h1 id="page-title">{title}</h1>{content}<p id="status" role="status" aria-live="polite"></p></main></body></html>'''


def _install(app, role):
    actor_for = getattr(app.state, 'support_actor_for', None)
    if not callable(actor_for):
        raise ValueError('UI requires the support chat factory authorization hook')
    marker = 'support_' + role + '_ui_installed'
    if getattr(app.state, marker, False):
        return app
    prefix = '/' + role

    def add(path, content, media_type, protected, allow_query=False):
        async def endpoint(request: Request):
            if request.scope.get('query_string') and not allow_query:
                return _error(400)
            page_content = content
            if protected:
                try:
                    # Resolve the current factory hook each time, never cache identity.
                    await app.state.support_actor_for(request)
                except SupportError:
                    return _error(401)
                except Exception:
                    return _error(401)
            response_type = HTMLResponse if media_type == 'text/html' else Response
            return response_type(page_content, media_type=media_type, headers=SENSITIVE_RESPONSE_HEADERS)
        app.add_api_route(path, endpoint, methods=['GET'], include_in_schema=False)

    if role == 'customer':
        from support_onboarding_ui import install_onboarding_ui
        install_onboarding_ui(app)
        from support_landing import LANDING_CSS, landing_page
        add('/', landing_page(), 'text/html', False, True)
        add(prefix + '/welcome', landing_page(), 'text/html', False, True)
        add(prefix + '/ui/landing.css', LANDING_CSS, 'text/css', False)
        add(prefix + '/ui/welcome.js', WELCOME_JS, 'text/javascript', False)
    add(prefix + ('/support' if role == 'admin' else '/chat'), _page(role), 'text/html', True)
    add(prefix + '/ui/chat.js', CHAT_JS, 'text/javascript', role == 'admin')
    add(prefix + '/ui/style.css', CSS, 'text/css', role == 'admin')
    setattr(app.state, marker, True)
    return app


def install_customer_ui(app):
    """Opt in on create_customer_chat_app; return the same app unchanged in policy."""
    return _install(app, 'customer')


def install_admin_ui(app):
    """Opt in on create_admin_chat_app; its request_assertion remains mandatory."""
    return _install(app, 'admin')
