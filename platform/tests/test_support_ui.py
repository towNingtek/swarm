"""Offline TestClient + optional Node fake-DOM checks; NOT browser acceptance."""
import os
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from support_app import COOKIE
from support_chat_app import create_admin_chat_app, create_customer_chat_app
from support_core import SupportCore
from support_model import FakeModel
from support_rooms import SupportRooms
from support_ui import CHAT_JS, WELCOME_JS, _page, install_admin_ui, install_customer_ui


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.scripts = []
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        self.in_script = tag == 'script'

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.scripts.append(data)


class SupportUITests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'ui.sqlite'),
                                admin_verifier=lambda a: 'operator' if a == 'test-assertion' else None)
        self.actor = self.core.admin_actor('test-assertion')
        self.tenant = self.core.create_tenant(self.actor, 'customer.example')
        self.rooms = SupportRooms(self.core)
        self.room = self.rooms.create_room(self.actor, self.tenant.id)['id']
        self.invite = self.core.issue_invite(self.actor, self.tenant.id)
        self.customer_app = create_customer_chat_app(self.core, self.rooms, 'https://customer.example', model=FakeModel())
        self.calls = []

        def assertion(request):
            self.calls.append(request.url.path)
            return 'test-assertion' if request.headers.get('x-test-admin') == 'yes' else None

        self.admin_app = create_admin_chat_app(self.core, self.rooms, 'https://mother.example', request_assertion=assertion)
        self.customer = self.client(self.customer_app, 'customer.example')
        self.admin = self.client(self.admin_app, 'mother.example')
        self.ah = {'x-test-admin': 'yes', 'origin': 'https://mother.example'}
        self.ch = {'origin': 'https://customer.example'}

    def client(self, app, host):
        client = TestClient(app, base_url='https://' + host)
        self.addCleanup(client.close)
        return client

    def safe(self, response):
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['referrer-policy'], 'same-origin')
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertNotIn('unsafe-inline', response.headers['content-security-policy'])
        self.assertIn("default-src 'self'", response.headers['content-security-policy'])
        self.assertNotIn(self.invite, response.text)
        self.assertNotIn('test-assertion', response.text)

    def test_opt_in_and_missing_hook_fail_closed(self):
        self.assertEqual(self.customer.get('/customer/welcome').status_code, 404)
        self.assertEqual(self.admin.get('/admin/support', headers=self.ah).status_code, 404)
        for install in (install_customer_ui, install_admin_ui):
            with self.assertRaises(ValueError):
                install(FastAPI())
        self.assertIs(install_customer_ui(self.customer_app), self.customer_app)
        count = len(self.customer_app.routes)
        install_customer_ui(self.customer_app)
        self.assertEqual(len(self.customer_app.routes), count)

    def test_public_welcome_external_assets_and_no_inline(self):
        install_customer_ui(self.customer_app)
        response = self.customer.get('/customer/welcome?token=' + self.invite)
        self.assertEqual(response.status_code, 200)
        self.safe(response)
        root = self.customer.get('/?token=' + self.invite)
        self.assertEqual(root.status_code, 200)
        self.safe(root)
        self.assertNotIn('location', root.headers)
        tags = Tags()
        tags.feed(response.text)
        self.assertEqual(tags.scripts, [])
        for tag, attrs in tags.tags:
            self.assertNotIn('style', attrs)
            self.assertFalse(any(k.startswith('on') for k in attrs))
            if tag == 'script' or tag == 'link':
                path = attrs.get('src', attrs.get('href'))
                self.assertTrue(path.startswith('/customer/ui/'))
                asset = self.customer.get(path)
                self.assertEqual(asset.status_code, 200)
                self.safe(asset)
        self.assertEqual(self.customer.get('/customer/chat').status_code, 401)
        self.assertEqual(self.customer.get('/customer/ui/chat.js?token=x').status_code, 400)
        self.assertEqual(self.customer.get('/customer/welcome', headers={'host': 'other.example'}).status_code, 403)
        self.assertEqual(self.customer.get('/customer/ui/welcome.js', headers={'sec-fetch-site': 'cross-site'}).status_code, 403)

    def test_customer_cookie_and_api_contract(self):
        install_customer_ui(self.customer_app)
        response = self.customer.post('/customer/activate', headers=self.ch, json={
            'invite': self.invite, 'username': 'alice', 'password': 'test-password-long'})
        self.assertEqual(response.status_code, 201)
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('Secure', response.headers['set-cookie'])
        token = self.customer.cookies.get(COOKIE)
        page = self.customer.get('/customer/chat')
        self.assertEqual(page.status_code, 200)
        self.safe(page)
        self.assertNotIn(token, page.text)
        base = '/customer/rooms/' + self.room
        self.assertEqual(self.customer.post(base + '/messages', headers=self.ch, json={
            'client_message_id': 'ui1', 'body': '<img src=x onerror=alert(1)>'}).status_code, 200)
        self.assertTrue(self.customer.post(base + '/respond', headers=self.ch, json={'requested': True}).json()['simulated'])
        self.assertEqual(len(self.customer.get(base + '?after=0').json()['messages']), 2)
        self.customer.post('/customer/logout', headers=self.ch, json={})
        self.assertEqual(self.customer.get('/customer/chat').status_code, 401)

    def test_admin_every_resource_uses_factory_assertion(self):
        install_admin_ui(self.admin_app)
        for path in ['/admin/support', '/admin/ui/chat.js', '/admin/ui/style.css']:
            response = self.admin.get(path)
            self.assertEqual(response.status_code, 401)
            self.safe(response)
            self.assertEqual(self.admin.get(path, headers=self.ah).status_code, 200)
            self.assertIn(path, self.calls)
        before = len(self.calls)
        self.assertEqual(self.admin.get('/admin/support?token=x', headers=self.ah).status_code, 400)
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.admin.get('/admin/support', headers=dict(self.ah, origin='https://evil.example')).status_code, 403)
        self.assertEqual(self.admin.get('/customer/welcome', headers=self.ah).status_code, 404)
        disabled_app = create_admin_chat_app(self.core, self.rooms, 'https://mother.example')
        install_admin_ui(disabled_app)
        self.assertEqual(self.client(disabled_app, 'mother.example').get('/admin/support', headers=self.ah).status_code, 401)
        base = '/admin/rooms/' + self.room
        for epoch, mode in enumerate(('coassist', 'human', 'ai')):
            response = self.admin.post(base + '/control', headers=self.ah, json={'mode': mode, 'expected_epoch': epoch})
            self.assertEqual(response.json()['mode'], mode)
        self.assertEqual(self.admin.post(base + '/messages', headers=self.ah, json={'client_message_id': 'admin-ui', 'body': 'hello'}).status_code, 200)

    def test_static_security_and_schemas(self):
        for source in (WELCOME_JS, CHAT_JS):
            for forbidden in ('innerHTML', 'outerHTML', 'document.write', 'localStorage', 'sessionStorage', 'document.cookie', 'console.', 'eval('):
                self.assertNotIn(forbidden, source)
            self.assertIn("credentials: 'same-origin'", source)
        self.assertEqual(WELCOME_JS.count('location.search'), 1)
        self.assertIn('history.replaceState', WELCOME_JS)
        self.assertIn("method: 'POST'", WELCOME_JS)
        self.assertIn('textContent', CHAT_JS)
        self.assertIn('lang="zh-Hant"', _page('customer', True))
        self.assertIn('測試模型', _page('customer'))
        self.assertIn('room-empty', _page('customer'))
        self.assertIn('請勿在聊天訊息中貼上密碼或 API 金鑰', _page('customer', True))
        self.assertIn('expected_epoch: current.epoch', CHAT_JS)
        self.assertIn('{requested: true}', CHAT_JS)
        self.assertIn("'?after=' + after", CHAT_JS)

    @unittest.skipUnless(shutil.which(os.environ.get('NODE', 'node')), 'Node unavailable')
    def test_chat_js_replay_safe_dom_and_retry_id(self):
        harness = r'''
const assert = require('node:assert/strict');
const nodes = {};
for (const id of ['status','rooms','messages','room-state','room-empty','refresh','compose','message','respond','logout']) nodes[id] = {value: '', children: [], addEventListener(k, fn) { this[k] = fn; }, replaceChildren() { this.children = []; }, append(child) { this.children.push(child); }};
global.document = {body: {dataset: {role: 'customer'}}, getElementById: (id) => nodes[id], querySelectorAll: () => [], createElement: () => ({set innerHTML(_) { throw new Error('Unsafe DOM'); }})};
let uuids = 0, sends = [], reads = [];
Object.defineProperty(global, 'crypto', {value: {randomUUID: () => 'id-' + (++uuids)}, configurable: true});
global.fetch = async (path, options) => {
 if (path === '/customer/rooms') return {ok: true, json: async () => [{id: 'r'}]};
 if (path.includes('/messages')) {
   sends.push(JSON.parse(options.body));
   if (sends.length === 1) throw new Error('Response lost');
   return {ok: true, json: async () => ({})};
 }
 reads.push(path);
 const messages = path.endsWith('after=0') ? Array.from({length:100}, (_, i) => ({seq:i+1, sender_kind:'customer', body:'<img onerror=alert(1)>'})) : [{seq:101, sender_kind:'ai', body:'Fake'}];
 return {ok:true, json:async () => ({id:'r', mode:'ai', epoch:0, messages})};
};
const tick = () => new Promise((resolve) => setImmediate(resolve));
'''
        assertions = r'''
(async () => {
await tick();
assert.ok(reads.includes('/customer/rooms/r?after=100'));
assert.equal(nodes.messages.children.length, 101);
assert.equal(nodes.messages.children[0].textContent, '客戶：<img onerror=alert(1)>');
nodes.message.value = 'hello';
nodes.compose.submit({preventDefault(){}}); await tick();
assert.equal(nodes.message.value, 'hello');
nodes.compose.submit({preventDefault(){}}); await tick();
assert.equal(sends.length, 2); assert.equal(sends[0].client_message_id, sends[1].client_message_id);
assert.ok(sends[0].client_message_id); assert.equal(nodes.message.value, '');
})().catch((error) => { process.stderr.write(String(error)); process.exitCode = 1; });
'''
        result = subprocess.run([os.environ.get('NODE', 'node'), '-e', harness + CHAT_JS + assertions], capture_output=True, text=True,
                                env={k: v for k, v in os.environ.items() if k != 'NODE_OPTIONS'})
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which(os.environ.get('NODE', 'node')), 'Node unavailable; static JS assertions still run')
    def test_welcome_js_scrubs_before_post_and_never_stores_credentials(self):
        harness = r'''
const assert = require('node:assert/strict');
const nodes = {};
for (const id of ['credentials', 'password', 'password-confirm', 'confirm-row', 'status', 'action', 'username', 'page-title', 'intro', 'invite-required']) nodes[id] = {value: '', textContent: '', autocomplete: '', disabled: false, required: false, hidden: false, addEventListener(k, fn) { this[k] = fn; }};
nodes.credentials.hidden = false;
global.document = {documentElement: {lang: ''}, title: '', getElementById: (id) => nodes[id]};
let searchReads = 0, scrubbed = false, posted;
global.location = {get search() { searchReads++; return '?token=one-time-secret'; }, pathname: '/customer/welcome', replace(p) { assert.equal(p, '/customer/onboarding'); }};
global.history = {replaceState(state, title, path) { assert.equal(state, null); assert.equal(path, '/customer/welcome'); scrubbed = true; }};
global.addEventListener = () => {};
global.fetch = async (path, options) => { assert.ok(scrubbed); assert.equal(path, '/customer/activate'); assert.equal(options.method, 'POST'); assert.equal(nodes.password.value, ''); posted = JSON.parse(options.body); return {ok: true}; };
'''
        assertions = r'''
(async () => {
assert.equal(searchReads, 1); assert.ok(scrubbed);
assert.equal(nodes.credentials.hidden, false);
assert.equal(document.documentElement.lang, 'zh-Hant');
assert.equal(nodes['page-title'].textContent, '啟用你的工作空間');
assert.ok(document.title === 'Swarm · 啟用工作空間');
assert.ok(nodes.intro.textContent.includes('邀請碼只用於這次啟用'));
assert.equal(nodes['confirm-row'].hidden, false);  // activation asks twice
assert.equal(nodes['password-confirm'].required, true);
nodes.username.value = 'alice'; nodes.password.value = 'a-long-password';
// A mismatch must block the request entirely, not merely warn.
nodes['password-confirm'].value = 'different-password';
await nodes.credentials.submit({preventDefault() {}});
assert.equal(posted, undefined);
assert.ok(nodes.status.textContent.includes('不一致'));
assert.equal(nodes.action.disabled, false);
nodes['password-confirm'].value = 'a-long-password';
await nodes.credentials.submit({preventDefault() {}});
assert.deepEqual(posted, {username: 'alice', password: 'a-long-password', invite: 'one-time-secret'});
assert.equal(nodes.password.value, '');
assert.equal(nodes['password-confirm'].value, '');
})().catch((error) => { process.stderr.write(String(error.stack)); process.exitCode = 1; });
'''
        result = subprocess.run([os.environ.get('NODE', 'node'), '-e', harness + WELCOME_JS + assertions], capture_output=True, text=True,
                                env={k: v for k, v in os.environ.items() if k != 'NODE_OPTIONS'})
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which(os.environ.get('NODE', 'node')), 'Node unavailable')
    def test_landing_page_is_invite_only_and_self_contained(self):
        install_customer_ui(self.customer_app)
        page = self.customer.get('/')
        self.assertEqual(page.status_code, 200)
        text = page.text
        # States the platform and its access model.
        self.assertIn('Swarm', text)
        self.assertIn('邀請制', text)
        self.assertIn('不開放自由註冊', text)
        # Architecture diagram is inline SVG with an accessible title.
        self.assertIn('<svg class="sa"', text)
        self.assertIn('id="sa-title"', text)
        # No registration entry point anywhere on the page.
        for word in ('register', 'signup', 'sign up', '註冊帳號', '免費註冊'):
            self.assertNotIn(word, text.lower())
        # The stylesheet is served same-origin.
        css = self.customer.get('/customer/ui/landing.css')
        self.assertEqual(css.status_code, 200)
        self.assertIn('text/css', css.headers['content-type'])

    def test_existing_customer_can_login_without_invitation(self):
        harness = r'''
const assert = require('node:assert/strict');
const nodes = {};
for (const id of ['credentials','password','password-confirm','confirm-row','status','action','username','page-title','intro','invite-required']) nodes[id] = {value:'', textContent:'', disabled:false, required:false, hidden:true, addEventListener(k,fn){this[k]=fn;}};
global.document = {documentElement:{},getElementById:id=>nodes[id]};
let destination;
global.location = {search:'',pathname:'/customer/welcome',replace:p=>destination=p};
global.history = {replaceState(){}}; global.addEventListener = () => {};
global.fetch = async (path, options) => {
 assert.equal(path,'/customer/login');
 assert.deepEqual(JSON.parse(options.body),{username:'alice',password:'test-password-long'});
 return {ok:true};
};
'''
        assertions = r'''
(async () => {
 assert.equal(nodes.credentials.hidden,false);
 assert.equal(nodes.password.autocomplete,'current-password');
 // Invite-only: without a token the form can only log in, never register.
 assert.equal(nodes['confirm-row'].hidden,true);
 assert.equal(nodes.action.textContent,'登入');
 nodes.username.value='alice'; nodes.password.value='test-password-long';
 await nodes.credentials.submit({preventDefault(){}});
 assert.equal(destination,'/customer/onboarding');
 assert.equal(nodes.password.value,'');
})().catch(e=>{process.stderr.write(String(e.stack));process.exitCode=1;});
'''
        result = subprocess.run([os.environ.get('NODE', 'node'), '-e', harness + WELCOME_JS + assertions],
                                capture_output=True, text=True,
                                env={k:v for k,v in os.environ.items() if k != 'NODE_OPTIONS'})
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
