"""Offline TestClient and executable Node fakeDOM checks, not browser acceptance."""
import os
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from support_chat_app import create_admin_chat_app
from support_core import AdminActor, SupportCore
from support_management import install_management
from support_management_ui import MANAGEMENT_JS, install_management_ui
from support_quota import SupportQuota
from support_rooms import SupportRooms
from support_ui import install_admin_ui


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class ManagementUITests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'ui.sqlite'),
                                admin_verifier=lambda a: 'operator' if a == 'test' else None)
        self.actor = self.core.admin_actor('test')
        self.tenant = self.core.create_tenant(self.actor, 'customer.example')
        self.rooms = SupportRooms(self.core)
        self.quota = SupportQuota(self.core)
        self.calls = []

        def assertion(request):
            self.calls.append(request.url.path)
            return request.headers.get('x-test-admin')

        self.app = create_admin_chat_app(self.core, self.rooms, 'https://mother.example',
                                         request_assertion=assertion)
        install_management(self.app, self.core, self.rooms, self.quota)
        self.client = TestClient(self.app, base_url='https://mother.example')
        self.addCleanup(self.client.close)
        self.headers = {'x-test-admin': 'test', 'origin': 'https://mother.example'}
        self.paths = ['/admin/customers', '/admin/customers/assets/management.js',
                      '/admin/customers/assets/style.css', '/admin/customers/assets/tasks.js',
                      '/admin/customers/assets/chat.js']

    def test_install_dependencies_opt_in_and_isolation(self):
        self.assertEqual(self.client.get(self.paths[0], headers=self.headers).status_code, 404)
        with self.assertRaises(ValueError):
            install_management_ui(FastAPI(), self.core)
        bare = FastAPI()
        bare.state.support_management_installed = True
        with self.assertRaises(ValueError):
            install_management_ui(bare, self.core)
        bare.state.support_actor_for = self.app.state.support_actor_for
        with self.assertRaises(ValueError):
            install_management_ui(bare, self.core)
        install_admin_ui(self.app)
        before = self.client.get('/admin/support', headers=self.headers).text
        self.assertIs(install_management_ui(self.app, self.core), self.app)
        count = len(self.app.routes)
        install_management_ui(self.app, self.core)
        self.assertEqual(len(self.app.routes), count)
        self.assertEqual(self.client.get('/admin/support', headers=self.headers).text, before)
        self.assertEqual(self.client.get('/customer/chat', headers=self.headers).status_code, 404)

    def test_every_request_assets_and_query_authorized_no_store_csp(self):
        install_management_ui(self.app, self.core)
        for path in self.paths:
            for headers, code in (({}, 401), (self.headers, 200), ({}, 401)):
                before = len(self.calls)
                response = self.client.get(path, headers=headers)
                self.assertEqual(response.status_code, code)
                self.assertEqual(len(self.calls), before + 1)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(response.headers['referrer-policy'], 'same-origin')
                self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
                self.assertNotIn('unsafe-inline', response.headers['content-security-policy'])
            self.assertEqual(self.client.get(path + '?token=secret', headers=self.headers).status_code, 400)
            self.assertEqual(self.client.get(path + '?token=secret').status_code, 401)
            self.assertEqual(self.client.get(path, headers={**self.headers, 'origin': 'https://evil.example'}).status_code, 403)
        parser = Tags()
        parser.feed(self.client.get(self.paths[0], headers=self.headers).text)
        for tag, attrs in parser.tags:
            self.assertNotIn('style', attrs)
            self.assertFalse(any(k.startswith('on') for k in attrs))
            if tag == 'script':
                self.assertIn(attrs['src'], (self.paths[1], '/admin/customers/assets/tasks.js',
                                             '/admin/customers/assets/chat.js'))
            if attrs.get('id') == 'invite-link':
                self.assertIn('readonly', attrs)
        self.assertFalse(any(tag == 'a' for tag, _ in parser.tags))
        page = self.client.get(self.paths[0], headers=self.headers).text
        self.assertIn('不會建立 Docker 站台', page)
        self.assertIn('不限額仍受系統安全速率', page)
        self.assertIn('客戶清單', page)

    def test_customer_forged_and_broken_adapter_rejected(self):
        install_management_ui(self.app, self.core)
        customer = self.core.redeem_invite(self.core.issue_invite(self.actor, self.tenant.id),
                                           'customer.example', 'alice', 'test-password-long').actor
        foreign_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(foreign_tmp.cleanup)
        foreign = SupportCore(str(Path(foreign_tmp.name) / 'foreign.sqlite'),
                              admin_verifier=lambda _: 'operator')
        for actor in (customer, AdminActor(self.actor.id, object()), foreign.admin_actor('test'), None):
            async def wrong(request):
                return actor
            self.app.state.support_actor_for = wrong
            for path in self.paths:
                self.assertEqual(self.client.get(path, headers=self.headers).status_code, 401)
        async def broken(request):
            raise RuntimeError('must-not-leak')
        self.app.state.support_actor_for = broken
        for path in self.paths:
            response = self.client.get(path, headers=self.headers)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn('must-not-leak', response.text)

    def test_real_api_contract_invite_and_policies(self):
        install_management_ui(self.app, self.core)
        rows = self.client.get('/admin/tenants', headers=self.headers).json()
        self.assertEqual(rows[0]['host'], 'customer.example')
        self.assertTrue(rows[0]['metadata_only'])
        path = '/admin/tenants/' + self.tenant.id
        response = self.client.post(path + '/invites', json={'ttl_seconds': 60}, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        token = response.json()['token']
        self.core.preview_invite(token, 'customer.example')
        self.assertNotIn(token, self.client.get(self.paths[0], headers=self.headers).text)
        for policy in ({'mode': 'disabled'}, {'mode': 'unlimited'}, {'mode': 'capped', 'monthly_limit': 7}):
            response = self.client.post(path + '/quota', json={'policy': policy}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['policy']['mode'], policy['mode'])
            self.assertEqual(self.client.get(path, headers=self.headers).json()['policy'], response.json()['policy'])
        self.assertEqual(self.client.post(path + '/quota', json={'policy': {'mode': 'capped', 'monthly_limit': -1}}, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get(path, headers=self.headers).json()['policy']['monthly_limit'], 7)

    def test_node_functional_dom_requests_failures_and_lifecycle(self):
        harness = r'''
const assert = require('node:assert/strict');
const nodes = {}, events = {}, calls = [];
function element() { return {value:'', textContent:'', children:[], disabled:false,
 focus() {}, select() {},
 addEventListener(k, fn) { this[k] = fn; }, replaceChildren() { this.children = []; },
 append(c) { this.children.push(c); }, set innerHTML(_) { throw Error('unsafe DOM'); },
 set outerHTML(_) { throw Error('unsafe DOM'); }}; }
for (const id of ['status','invite-link','invite-submit','quota-submit','refresh','limit','policy','tenant-list','tenants','tenant-detail','ttl','invite-form','quota-form','bootstrap-form','bootstrap-host','bootstrap-submit','bootstrap-note','bootstrap-domain','bootstrap-preview','invite-copy','invite-copy-status','site-job-section','site-job-submit','site-job-result','site-job-note','danger-section','delete-confirm','delete-submit','delete-result','template','template-form','template-submit','template-desc','page-note']) nodes[id] = element();
global.document = {getElementById:id=>nodes[id], createElement:element, body:{dataset:{}}};
global.confirm = () => true;
let copied = null;
// Node 22 ships a read-only built-in `navigator`, so plain assignment is
// silently ignored and the code under test would take its fallback path.
Object.defineProperty(global, 'navigator', {
  configurable: true, value: {clipboard: {async writeText(v) { copied = v; }}}});
global.addEventListener = (name, fn) => events[name] = fn;
for (const key of ['localStorage','sessionStorage','location']) Object.defineProperty(global,key,{get(){throw Error('forbidden '+key);}});
global.console = new Proxy({}, {get(){throw Error('logging forbidden');}});
let failInvite = false, failQuota = false, holdInvite = false, release;
const rows = [
 {id:'a/x<img onerror=bad>',host:'customer.example',enabled:true,quota_mode:'metered',metadata_only:true,policy:{mode:'disabled',monthly_limit:null},template:'swarm'},
 {id:'b',host:'other.example',enabled:true,quota_mode:'metered',metadata_only:true,policy:{mode:'capped',monthly_limit:5},template:'none'}
];
global.fetch = async (path, options) => {
 calls.push({path, options});
 assert.equal(options.credentials,'same-origin'); assert.equal(options.cache,'no-store'); assert.equal(options.redirect,'error');
 if (path === '/admin/tenants') return {ok:true,json:async()=>structuredClone(rows)};
 if (path === '/admin/site-templates') return {ok:true,json:async()=>({default:'swarm',templates:[
  {id:'none',name:'不放樣板',version:0,description:'空白'},{id:'swarm',name:'Swarm <b>辦公室</b>',version:1,description:'d'}]})};
 assert.equal(options.method,'POST'); assert.equal(options.headers['Content-Type'],'application/json');
 const body = JSON.parse(options.body);
 if (path.endsWith('/invites')) {
  if (holdInvite) await new Promise(resolve=>release=resolve);
  if (failInvite) throw Error('lost response secret');
  return {ok:true,json:async()=>({token:'secret+/?&',ttl_seconds:body.ttl_seconds})};
 }
 if (path.endsWith('/template')) return {ok:true,json:async()=>({template:body.template,metadata_only:true})};
 return {ok:!failQuota,json:async()=>({policy:{...body.policy,monthly_limit:body.policy.monthly_limit ?? null}})};
};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const submit=()=>({preventDefault(){}});
'''
        assertions = r'''
(async()=>{
 await tick();
 assert.equal(nodes['tenant-list'].children.length,2);
 // Hostile metadata must still be rendered as literal text, never markup.
 // (quota_mode is no longer summarised, so assert on a field that is shown.)
 assert.ok(nodes['tenant-list'].children[0].textContent.includes('a/x'));
 assert.equal(nodes.policy.value,'disabled'); assert.equal(nodes.limit.disabled,true);
 nodes.ttl.value='60';
 await nodes['invite-form'].submit(submit());
 assert.equal(calls.at(-1).path,'/admin/tenants/a%2Fx%3Cimg%20onerror%3Dbad%3E/invites');
 assert.deepEqual(JSON.parse(calls.at(-1).options.body),{ttl_seconds:60});
 assert.equal(nodes['invite-link'].value,'https://customer.example/?token=secret%2B%2F%3F%26');
 assert.ok(nodes.status.textContent.includes('邀請已產生'));
 // The copy button becomes usable only once a link exists, and copies exactly it.
 assert.equal(nodes['invite-copy'].disabled, false);
 await nodes['invite-copy'].click();
 assert.equal(copied, nodes['invite-link'].value);
 assert.ok(nodes['invite-copy-status'].textContent.includes('已複製'));
 // Reserved and malformed site hosts are rejected before any tenant is created.
 document.body.dataset.mode='live';
 assert.equal(nodes['bootstrap-form'].hidden,true); assert.equal(nodes['site-job-section'].hidden,true);
 assert.equal(nodes['bootstrap-submit'].disabled,true); assert.equal(nodes['site-job-submit'].disabled,true);
 { const before=calls.length; await nodes['bootstrap-form'].submit(submit()); nodes['site-job-submit'].click(); await tick(); assert.equal(calls.length,before); }
 // Template choices come from the server list; names render as text.
 assert.equal(nodes.template.children.length,2);
 assert.equal(nodes.template.children[1].textContent,'Swarm <b>辦公室</b>（預設）');
 assert.equal(nodes.template.value,'swarm');
 assert.ok(nodes['tenant-list'].children[0].textContent.includes('樣板：Swarm'));
 nodes.tenants.value='b'; nodes.tenants.change();
 assert.equal(nodes.template.value,'none');
 nodes.template.value='swarm'; await nodes['template-form'].submit(submit());
 assert.equal(calls.at(-1).path,'/admin/tenants/b/template');
 assert.deepEqual(JSON.parse(calls.at(-1).options.body),{template:'swarm'});
 assert.ok(nodes.status.textContent.includes('樣板已儲存'));
 { const before=calls.length; nodes.template.value='../evil'; await nodes['template-form'].submit(submit()); assert.equal(calls.length,before); }
 nodes.tenants.value='b'; nodes.tenants.change();
 assert.equal(nodes['invite-link'].value,''); assert.equal(nodes.policy.value,'capped');
 assert.equal(nodes.limit.value,'5'); assert.equal(nodes.limit.disabled,false);
 for (const mode of ['disabled','unlimited','capped']) {
  nodes.policy.value=mode; nodes.policy.change(); nodes.limit.value='7';
  await nodes['quota-form'].submit(submit());
  assert.deepEqual(JSON.parse(calls.at(-1).options.body),{policy:mode==='capped'?{mode,monthly_limit:7}:{mode}});
  assert.ok(nodes.status.textContent.includes('已儲存'));
 }
 failQuota=true; nodes.policy.value='unlimited';
 await nodes['quota-form'].submit(submit());
 assert.ok(nodes.status.textContent.includes('未確認成功'));
 assert.ok(nodes['tenant-detail'].textContent.includes('客服額度：capped / 7'));
 // Host leads the summary; the full id stays available but out of the way.
 assert.ok(nodes['tenant-detail'].textContent.startsWith('other.example'));
 assert.ok(nodes['tenant-detail'].textContent.includes('完整 id：b'));
 failQuota=false;
 failInvite=true; const before=calls.length;
 await nodes['invite-form'].submit(submit()); await tick();
 assert.equal(calls.length,before+1); assert.equal(nodes['invite-link'].value,'');
 assert.ok(nodes.status.textContent.includes('請勿盲目重試'));
 failInvite=false;
 for (const value of ['59','86401','60.5','', '1e3']) {
  nodes.ttl.value=value; const count=calls.length; await nodes['invite-form'].submit(submit()); assert.equal(calls.length,count);
 }
 nodes.ttl.value='60'; holdInvite=true;
 const pending=nodes['invite-form'].submit(submit()); await tick();
 const count=calls.length; await nodes['invite-form'].submit(submit()); assert.equal(calls.length,count);
 nodes.tenants.value='a/x<img onerror=bad>'; nodes.tenants.change(); release(); await pending;
 assert.equal(nodes['invite-link'].value,''); assert.equal(nodes.status.textContent,'');
 const leaving=nodes['invite-form'].submit(submit()); await tick();
 events.pagehide(); release(); await leaving;
 assert.equal(nodes['invite-link'].value,''); assert.equal(nodes.status.textContent,'');
 events.pageshow({persisted:true}); holdInvite=false;
 await nodes['invite-form'].submit(submit()); assert.notEqual(nodes['invite-link'].value,'');
 events.pagehide(); assert.equal(nodes['invite-link'].value,'');
})().catch(error=>{process.stderr.write(String(error.stack));process.exitCode=1;});
'''
        result = subprocess.run([os.environ.get('NODE', 'node'), '-e', harness + MANAGEMENT_JS + assertions],
                                capture_output=True, text=True, timeout=20,
                                env={k: v for k, v in os.environ.items() if k != 'NODE_OPTIONS'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')


    def test_live_new_tenant_is_selected_before_invite_or_build(self):
        # Regression: after 「建立客戶」 the previous tenant stayed selected, so the
        # operator's next 「產生邀請」／「排入建站任務」 went to the wrong customer.
        script = r"""
const assert = require('node:assert/strict');
const nodes = {}, calls = [];
function element() { return {value:'', textContent:'', children:[], disabled:false, hidden:false,
 focus() {}, select() {}, addEventListener(k, fn) { this[k] = fn; },
 replaceChildren() { this.children = []; }, append(c) { this.children.push(c); }}; }
for (const id of ['status','invite-link','invite-submit','quota-submit','refresh','limit','policy','tenant-list','tenants','tenant-detail','ttl','invite-form','quota-form','bootstrap-form','bootstrap-host','bootstrap-submit','bootstrap-note','bootstrap-domain','bootstrap-preview','invite-copy','invite-copy-status','site-job-section','site-job-submit','site-job-result','site-job-note','danger-section','delete-confirm','delete-submit','delete-result','template','template-form','template-submit','template-desc','page-note']) nodes[id] = element();
global.document = {getElementById:id=>nodes[id], createElement:element,
  body:{dataset:{mode:'live', siteDomain:'example.com'}}};
global.confirm = () => true;
global.addEventListener = () => {};
global.crypto = {randomUUID: () => '00000000-0000-4000-8000-000000000000'};
const rows = [{id:'t-ab',host:'ab.example.com',enabled:true,metadata_only:true,site_job:'succeeded_provisioned',
  policy:{mode:'disabled',monthly_limit:null},template:'swarm'}];
global.fetch = async (path, options) => {
 calls.push({path, body: options.body ? JSON.parse(options.body) : null});
 if (path === '/admin/site-templates') return {ok:true,json:async()=>({default:'swarm',templates:[{id:'swarm',name:'S',version:1,description:'d'}]})};
 if (path === '/admin/tenants' && !options.method) return {ok:true,json:async()=>structuredClone(rows)};
 if (path === '/admin/tenants') {
   const body = JSON.parse(options.body);
   // Sorted by id, the new tenant is NOT first: selection must follow it anyway.
   rows.push({id:'t-zz',host:body.host,enabled:true,metadata_only:true,site_job:null,
     policy:{mode:'disabled',monthly_limit:null},template:'swarm'});
   return {ok:true,json:async()=>({tenant:{id:'t-zz',host:body.host,enabled:true}})};
 }
 if (path.endsWith('/site-jobs')) { rows[1].site_job = 'queued'; return {ok:true,json:async()=>({job:{status:'queued'}})}; }
 throw Error('unexpected ' + path);
};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
"""
        checks = r"""
(async()=>{
 await tick(); await tick();
 assert.ok(nodes['page-note'].textContent.includes('建立客戶'));
 assert.equal(nodes.tenants.value,'t-ab');
 assert.equal(nodes['site-job-submit'].disabled,true);
 assert.equal(nodes['site-job-submit'].textContent,'ab.example.com 已完成');
 nodes['bootstrap-host'].value='acme';
 await nodes['bootstrap-form'].submit({preventDefault(){}});
 assert.equal(nodes.tenants.value,'t-zz');
 assert.ok(nodes['tenant-detail'].textContent.startsWith('acme.example.com'));
 assert.equal(nodes['site-job-submit'].textContent,'為 acme.example.com 排入建站任務');
 assert.equal(nodes['site-job-submit'].disabled,false);
 assert.equal(nodes['invite-submit'].textContent,'產生 acme.example.com 的邀請');
 assert.equal(nodes['bootstrap-host'].value,'');
 await nodes['site-job-submit'].click(); await tick();
 assert.equal(calls.filter(c=>c.path.endsWith('/site-jobs')).at(-1).path,'/admin/tenants/t-zz/site-jobs');
 // The list is reloaded so the new build state shows, and the selection stays put.
 assert.equal(nodes.tenants.value,'t-zz');
 assert.ok(nodes['tenant-list'].children[1].textContent.includes('已排入'));
 assert.ok(nodes['site-job-result'].textContent.includes('acme.example.com'));
 assert.equal(nodes['site-job-submit'].disabled,true);
})().catch(error=>{process.stderr.write(String(error.stack));process.exitCode=1;});
"""
        result = subprocess.run([os.environ.get('NODE', 'node'), '-e', script + MANAGEMENT_JS + checks],
                                capture_output=True, text=True, timeout=20,
                                env={k: v for k, v in os.environ.items() if k != 'NODE_OPTIONS'})
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()


class ChatPanelScriptTests(unittest.TestCase):
    """Drive CHAT_JS against a fake DOM: overview, thread, reply, mode switch."""

    def test_overview_reply_and_mode(self):
        import json
        import os
        import subprocess
        from support_management_ui import CHAT_JS
        harness = r'''
const assert = require('node:assert/strict');
const nodes = {};
const make = (extra={}) => Object.assign({children:[],hidden:false,value:'',textContent:'',className:'',
  dataset:{},scrollTop:0,scrollHeight:100,clientHeight:100,disabled:false,
  append(...x){this.children.push(...x);},replaceChildren(...x){this.children=x;},
  addEventListener(k,fn){this[k]=fn;},setAttribute(k,v){this['attr:'+k]=v;},focus(){},scrollIntoView(){},
  querySelectorAll(){return this.buttons||[];},
  set innerHTML(x){throw Error('unsafe DOM')}}, extra);
for (const id of ['chat-rooms','chat-overview-status','chat-panel','chat-title','chat-room-mode','chat-modes',
  'chat-thread','chat-reply-form','chat-reply','chat-reply-send','chat-panel-status','chat-section',
  'chat-launcher','chat-close','chat-count','chat-assist']) nodes[id]=make();
nodes['chat-section'].hidden = true;
nodes['chat-modes'].buttons = ['ai','coassist','human'].map(mode => make({dataset:{roomMode:mode}}));
global.document = {getElementById:id=>nodes[id],hidden:false,title:'',addEventListener(k,fn){this['on'+k]=fn;},
  createElement:()=>make(), createTextNode:(t)=>({textContent:t})};
global.location = {hash:''};
let room = {id:'r1',mode:'ai',epoch:0,messages:[{seq:1,sender_kind:'customer',body:'<b>救命</b>',created_at:1800000000}]};
const calls = [];
global.fetch = async (path, options={}) => {
  calls.push({path, body: options.body ? JSON.parse(options.body) : undefined});
  if (path === '/admin/room-overview') return {ok:true,json:async()=>[{id:'r1',site_host:'ab.example.com',mode:room.mode,
    epoch:room.epoch,messages:room.messages.length,waiting:room.messages.at(-1).sender_kind==='customer',
    last:{seq:1,sender_kind:room.messages.at(-1).sender_kind,at:1800000000,preview:'x'}}]};
  if (path === '/admin/rooms/r1') return {ok:true,json:async()=>room};
  if (path === '/admin/rooms/r1/reply') {
    room.messages.push({seq:2,sender_kind:'human',body:options && JSON.parse(options.body).body,created_at:1800000001});
    return {ok:true,json:async()=>({})};
  }
  if (path === '/admin/rooms/r1/assist') {
    room.messages.push({seq:room.messages.length+1,sender_kind:'ai',body:'AI 依指示回覆',created_at:1800000002});
    return {ok:true,json:async()=>({discarded:false})};
  }
  if (path === '/admin/rooms/r1/control') {
    const b = JSON.parse(options.body);
    if (b.expected_epoch !== room.epoch) return {ok:false,status:409};
    room.mode=b.mode; room.epoch++;
    return {ok:true,json:async()=>room};
  }
  throw Error('unexpected ' + path);
};
const tick = () => new Promise(r => setImmediate(r));
'''
        assertions = r'''
(async () => {
  await tick(); await tick();
  // Closed popup: only the launcher badge shows the waiting count; no room is loaded yet.
  assert.equal(nodes['chat-count'].textContent, '1');
  assert.equal(nodes['chat-count'].hidden, false);
  assert.equal(document.title, '(1) 客戶管理');
  assert.equal(calls.some(c => c.path === '/admin/rooms/r1'), false);
  const option = nodes['chat-rooms'].children[0];
  assert.equal(option.value, 'r1');
  assert.equal(option.textContent, '● ab.example.com（1 則，等回覆）');
  // Opening picks the waiting room automatically.
  await nodes['chat-launcher'].click(); for (let i = 0; i < 4; i++) await tick();
  assert.equal(nodes['chat-section'].hidden, false);
  assert.equal(nodes['chat-launcher']['attr:aria-expanded'], 'true');
  assert.equal(nodes['chat-panel'].hidden, false);
  assert.equal(nodes['chat-title'].textContent, '與 ab.example.com 的對話');
  const first = nodes['chat-thread'].children[0];
  assert.equal(first.className, 'customer');
  assert.equal(first.children[1].textContent, '<b>救命</b>', 'customer text stays text');
  assert.equal(nodes['chat-modes'].buttons[0]['attr:aria-pressed'], 'true');
  // Picking from the dropdown switches rooms.
  nodes['chat-rooms'].value = 'r1'; await nodes['chat-rooms'].change(); await tick();
  assert.equal(nodes['chat-title'].textContent, '與 ab.example.com 的對話');
  nodes['chat-reply'].value = '我來幫你';
  // Shift+Enter and IME composition never send; plain Enter does.
  let prevented = 0;
  nodes['chat-reply'].keydown({key:'Enter', shiftKey:true, preventDefault(){prevented++;}});
  nodes['chat-reply'].keydown({key:'Enter', isComposing:true, preventDefault(){prevented++;}});
  assert.equal(prevented, 0);
  assert.equal(calls.some(c => c.path === '/admin/rooms/r1/reply'), false);
  nodes['chat-reply'].keydown({key:'Enter', preventDefault(){prevented++;}});
  assert.equal(prevented, 1);
  for (let i = 0; i < 6; i++) await tick();
  const reply = calls.find(c => c.path === '/admin/rooms/r1/reply');
  assert.equal(reply.body.body, '我來幫你');
  assert.match(reply.body.client_message_id, /^[0-9a-f-]{36}$/);
  assert.equal(nodes['chat-reply'].value, '');
  assert.equal(nodes['chat-thread'].children[1].children[0].textContent, '平台人員');
  // A reply never changes the mode.
  assert.match(nodes['chat-room-mode'].textContent, /AI 自動回覆/);
  assert.equal(nodes['chat-modes'].buttons[0]['attr:aria-pressed'], 'true');
  // 指示 AI: the text goes as a private instruction, not as a message.
  nodes['chat-reply'].value = '說明怎麼申請 OpenAI key';
  await nodes['chat-assist'].click();
  for (let i = 0; i < 6; i++) await tick();
  const assist = calls.find(c => c.path === '/admin/rooms/r1/assist');
  assert.deepEqual(assist.body, {instruction:'說明怎麼申請 OpenAI key'});
  assert.equal(calls.filter(c => c.path === '/admin/rooms/r1/reply').length, 1);
  assert.equal(nodes['chat-reply'].value, '');
  assert.equal(nodes['chat-thread'].children.at(-1).children[0].textContent, 'AI 助手');
  assert.equal(document.title, '客戶管理');
  assert.equal(nodes['chat-count'].hidden, true);
  await nodes['chat-modes'].buttons[1].click();
  for (let i = 0; i < 6; i++) await tick();
  const control = calls.find(c => c.path === '/admin/rooms/r1/control');
  assert.deepEqual(control.body, {mode:'coassist', expected_epoch:0});
  assert.equal(room.mode, 'coassist');
  assert.match(nodes['chat-panel-status'].textContent, /真人接手/);
  document.onkeydown({key:'Escape'});
  assert.equal(nodes['chat-section'].hidden, true);
  nodes['chat-launcher'].click(); nodes['chat-close'].click();
  assert.equal(nodes['chat-section'].hidden, true);
  assert.equal(nodes['chat-launcher']['attr:aria-expanded'], 'false');
})().then(() => process.exit(0), e => { process.stderr.write(String(e.stack)); process.exit(1); });
'''
        result = subprocess.run([os.environ.get('NODE', 'node'), '-e', harness + CHAT_JS + assertions],
                                text=True, capture_output=True,
                                env={k: v for k, v in os.environ.items() if k != 'NODE_OPTIONS'})
        self.assertEqual(result.returncode, 0, result.stderr)
