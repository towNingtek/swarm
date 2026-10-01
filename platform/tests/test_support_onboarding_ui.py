"""Executable fake DOM checks; not a claim of real browser acceptance."""
import json
import os
import shutil
import subprocess
import unittest
from support_onboarding_ui import JS, PAGE


class OnboardingUITests(unittest.TestCase):
    @unittest.skipUnless(shutil.which(os.environ.get('NODE', 'node')), 'Node unavailable')
    def test_page_shares_the_landing_visual_identity(self):
        from support_onboarding_ui import CSS
        # Same stylesheet and brand as the landing page, then page-specific rules.
        self.assertIn('/customer/ui/landing.css', PAGE)
        self.assertIn('/customer/ui/onboarding.css', PAGE)
        self.assertIn('class="mark-outer"', PAGE)
        self.assertIn('Enter 送出', PAGE)
        # One row; it grows with the text and the conversation, not the
        # input, is what scrolls.
        self.assertIn('id="chat-input" maxlength="4000" rows="1"', PAGE)
        self.assertIn('#copilot > #chat-log { flex: 1 1 auto', CSS)
        # No inline script, style attribute or handler: the platform CSP forbids them.
        import re
        self.assertIsNone(re.search(r'<script(?![^>]*\bsrc=)', PAGE))
        self.assertIsNone(re.search(r'\sstyle=|\son[a-z]+=', PAGE))
        self.assertIn('#copilot-launcher', CSS)

    def test_render_ack_replay_errors_and_recovery(self):
        harness = r'''
const assert = require('node:assert/strict');
const nodes = {};
for (const id of ['steps','rules','acknowledge','reload','status','site-job','site-card','office','office-meta','office-note','hives','office-empty','model-meta','model-current','starter-box','starter-bar','starter-usage','model-howto','rules-box','rules-state','site-ready','site-link','site-enter','site-secret','auto-note','copilot','copilot-message','copilot-act','copilot-status','chat-log','chat-mode','chat-ask-ai','chat-form','chat-input','chat-send','chat-status','copilot-launcher','copilot-close','site-password','site-password-form','site-password-new','site-password-reuse','site-password-new-fields','site-password-reuse-fields','site-password-input','site-password-confirm','site-password-platform','site-password-save','site-password-status','sched-form','sched-title','sched-freq','sched-dow','sched-dow-wrap','sched-time','sched-time-wrap','sched-minute','sched-minute-wrap','sched-cron','sched-cron-wrap','sched-role','sched-skill','sched-name','sched-context','sched-save','sched-cancel','sched-status','sched-home','sched-preview','offers','offers-meta','offer-list','offers-waiting','runs-box','runs-meta','run-list','schedule-note-text']) nodes[id] = {
 children:[],disabled:false,hidden:false,value:'',textContent:'',append(x){this.children.push(x);},
 replaceChildren(...x){this.children=x;},addEventListener(k,fn){this[k]=fn;},
 setAttribute(k,v){this['attr:'+k]=v;}, focus(){}, dataset:{}, scrollTop:0, scrollHeight:480
};
global.crypto = {randomUUID:()=>'cid-'+Math.random().toString(16).slice(2)};
global.addEventListener = () => {};   // page-level key handler (Escape to close)
global.document = {getElementById:id=>nodes[id],hidden:false,addEventListener(){},
 createElement:()=>({value:'',textContent:'',href:'',className:'',children:[],append(...x){this.children.push(...x);},addEventListener(k,fn){this[k]=fn;},setAttribute(k,v){this['attr:'+k]=v;},set innerHTML(x){throw Error('unsafe DOM')}})};
global.setTimeout = globalThis.setTimeout; global.clearTimeout = globalThis.clearTimeout;
global.location = {replace(){}};
const initial = {rules_version:'onboarding-rules-v1',rules:['不要貼 key'],rules_acknowledged:false,next_step:'rules',ready_for_work:false,
 steps:[{id:'activation',status:'complete'},{id:'rules',status:'pending'},{id:'site_preparation',status:'blocked'},{id:'office',status:'blocked',reason:'site_not_ready'},{id:'own_model',status:'blocked',reason:'site_not_ready'}]};
let mode='ok', calls=[], ackResolve;
let jobStatus='not_requested', modelState='unconfigured', revision=0, selection=null, probeAvailable=false;
let siteHost=null, nextStep='rules', sitePasswordReply=200; const sitePasswordBodies=[];
const performed=[]; const thread=[]; let roomMode='ai';
let office=null, starter={mode:'capped',used:1500,limit:10000};
let copilotStep={message:'你還沒確認使用與支援說明。',
 action:{kind:'acknowledge_rules',label:'幫我確認說明',token:'signed-token',
         arguments:{rules_version:'onboarding-rules-v1'}}};
const officeCalls=[]; let officeReply=null;
global.fetch = async (path,options) => {
 calls.push({path,options});
 if(mode==='network') throw Error('network');
 if(mode==='badjson') return {ok:true,json:async()=>{throw Error('json')}};
 if(mode===401 || mode===409) return {ok:false,status:mode};
 if(path==='/customer/rooms') return {ok:true,json:async()=>[{id:'room-1'}]};
 if(path==='/customer/rooms/room-1/messages') {
  const b=JSON.parse(options.body);
  thread.push({sender_kind:'customer',body:b.body});
  return {ok:true,json:async()=>({})};
 }
 if(path==='/customer/rooms/room-1/respond') {
  thread.push({sender_kind:'ai',body:'TEST ONLY 回覆'});
  // A reply may carry the next allowed action.
  return {ok:true,json:async()=>({suggestion:copilotStep})};
 }
 if(path==='/customer/rooms/room-1') return {ok:true,json:async()=>({mode:roomMode,messages:thread})};
 if(path.startsWith('/customer/office/')) {
  officeCalls.push({path, body: JSON.parse(options.body)});
  if(officeReply) return officeReply;
  return {ok:true,json:async()=>({})};
 }
 if(path==='/customer/onboarding/copilot') {
  return {ok:true,json:async()=>copilotStep};
 }
 if(path==='/customer/onboarding/copilot/perform') {
  const b=JSON.parse(options.body);
  performed.push(b);
  // The page must return exactly what was proposed, token included.
  assert.equal(b.kind,'acknowledge_rules');
  assert.equal(b.token,'signed-token');
  assert.deepEqual(b.arguments,{rules_version:'onboarding-rules-v1'});
  copilotStep={message:'說明已確認。',action:null};
  return {ok:true,json:async()=>copilotStep};
 }
 if(path==='/customer/site-entry') {
  assert.equal(options.method,'POST');
  return {ok:true,json:async()=>({url:'https://acme.example/auth/enter?ticket=t1'})};
 }
 if(path==='/customer/site-password' && !options.method) return {ok:true,json:async()=>({available:true})};
 if(path==='/customer/site-password') {
  assert.equal(options.method,'POST');
  const body=JSON.parse(options.body);
  sitePasswordBodies.push(body);
  if(sitePasswordReply!==200) return {ok:false,status:sitePasswordReply};
  return {ok:true,json:async()=>({site_host:siteHost,username:'admin'})};
 }
 if(path.startsWith('/customer/model-settings')) throw Error('the page must not use the old model form');
 if(path.endsWith('/acknowledge')) {
  assert.equal(options.method,'POST');
  assert.deepEqual(JSON.parse(options.body),{rules_version:'onboarding-rules-v1'});
  await new Promise(resolve=>{ackResolve=resolve;});
  nextStep='site_preparation';
  return {ok:true,json:async()=>({...initial,rules_acknowledged:true,next_step:nextStep})};
 }
 return {ok:true,json:async()=>({...initial,next_step:nextStep,site_job:{status:jobStatus,simulated:jobStatus==='succeeded_simulated',provisioned:jobStatus==='succeeded_provisioned',...(siteHost?{site_host:siteHost}:{})},office,starter})};
};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
'''
        assertions = r'''
(async()=>{
 await tick();
 assert.equal(nodes.steps.children.length,5);
 const stepText = i => nodes.steps.children[i].children.map(c=>c.textContent).join(' ');
 assert.match(stepText(3),/建立辦公室.*站台建立後/);
 assert.equal(nodes['rules-box'].open,true,'unread rules start open');
 assert.match(nodes['office-note'].textContent,/站台建立後/);
 assert.match(nodes['model-current'].textContent,/站台建立後/);
 assert.equal(nodes.acknowledge.disabled,false);
 assert.match(nodes['site-job'].textContent,/等待管理員建立/);
 jobStatus='reconciliation_required'; nodes.reload.click(); await tick();
 assert.match(nodes['site-job'].textContent,/不會自動重建/);
 jobStatus='succeeded_simulated'; nodes.reload.click(); await tick();
 assert.match(nodes['site-job'].textContent,/不代表已建立真實站台/);
 // Site-ready panel stays hidden until real provisioning reports success.
 assert.equal(nodes['site-ready'].hidden,true);
 nextStep='site_preparation';
 jobStatus='succeeded_provisioned'; siteHost='acme.example'; nodes.reload.click(); await tick();
 assert.equal(nodes['site-ready'].hidden,false);
 assert.match(nodes['site-link'].textContent,/https:\/\/acme\.example\//);
 assert.match(nodes['site-card'].className,/ready/);
 // Office unreadable: said so, nothing invented.
 assert.match(nodes['office-note'].textContent,/讀不到/);
 // Empty swarm office: the how-to shows; starter model with its meter.
 office={template:'swarm',registry:'ok',hives:[],default_model:{provider:'platform-starter',model:'cloud-fast',starter:true}};
 nodes.reload.click(); await tick();
 assert.equal(nodes['office-empty'].hidden,false);
 assert.match(PAGE,/排程要掛在公司底下/);
 assert.equal(nodes.hives.children.length,0);
 assert.match(nodes['model-current'].textContent,/起步模型（cloud-fast）/);
 assert.equal(nodes['model-howto'].hidden,false);
 assert.match(nodes['starter-usage'].textContent,/2k \/ 10k/);
 // Hives render as text only, with roles, schedules and honest scheduling state.
 office={template:'swarm',registry:'ok',default_model:{provider:'openrouter',model:'x/y',starter:false},hives:[
  {id:'shop',name:'<b>小店</b>',description:null,enabled:false,exists:true,scm:'github',channels:['discord'],
   roles:[{id:'pm',model:null,enabled:true,schedules:[]},{id:'rd',model:'opus',enabled:true,schedules:[]}],
   schedules:[{role:'pm',name:'digest',cron:'0 9 * * mon',skill:null,context:'<i>整理</i>',state:'disabled'},
    {role:'pm',name:'lonely',cron:'0 8 * * *',skill:null,context:'',state:'missing_definition'},
    {role:null,name:'untimed',cron:null,skill:'calendar',context:'',state:'missing_time'},
    {role:'rd',name:'odd',cron:'*/5 1-3 * * *',skill:null,context:'x',state:'ready'}]},
  {id:'gone',name:'gone',enabled:false,exists:false,scm:'none',channels:[],roles:[],schedules:[]}]};
 nodes.reload.click(); await tick();
 assert.equal(nodes['office-empty'].hidden,true);
 assert.equal(nodes.hives.children.length,2);
 const flat = n => [n.textContent, ...(n.children||[]).map(flat)].join(' ');
 const card = flat(nodes.hives.children[0]);
 assert.match(card,/<b>小店<\/b>/, 'names are text, never markup');
 assert.match(card,/排程關閉/); assert.match(card,/09:00/); assert.match(card,/<i>整理<\/i>/);
 assert.match(card,/已停用/); assert.match(card,/缺少任務內容/); assert.match(card,/缺少時間/);
 assert.match(card,/未設定時間/); assert.match(card,/每天/); assert.match(card,/08:00/); assert.match(card,/\*\/5 1-3 \* \* \*/);
 assert.match(card,/會執行（待執行器上線）/); assert.match(card,/排程關閉/);
 assert.match(card,/每週一/); assert.match(card,/（沒有任務內容）/);
 assert.match(card,/GitHub/); assert.match(card,/Discord/);
 assert.match(card,/opus/); assert.match(card,/預設模型/);
 assert.match(flat(nodes.hives.children[1]),/找不到這個資料夾/);
 assert.equal(nodes['office-meta'].textContent,'2 間公司');
 assert.match(nodes['model-current'].textContent,/openrouter \/ x\/y/);
 assert.equal(nodes['model-howto'].hidden,true);
 assert.equal(nodes['starter-box'].hidden,true);
 // Schedule editing: the page builds the cron and sends only the customer's
 // choices plus the revision it saw; the server decides everything else.
 office.revision='rev-1'; office.skills=['calendar'];
 nodes.reload.click(); await tick();
 const findBtn = (n, label) => { if (n.textContent===label && n.click) return n;
   for (const c of n.children||[]) { const f=findBtn(c,label); if (f) return f; } return null; };
 const shopCard = nodes.hives.children[0];
 global.confirm = () => true;
 findBtn(shopCard,'＋ 新增排程').click({});
 assert.equal(nodes['sched-form'].hidden,false);
 assert.equal(nodes['sched-freq'].value,'daily');
 nodes['sched-freq'].value='weekly'; nodes['sched-freq'].change();
 assert.equal(nodes['sched-dow-wrap'].hidden,false);
 nodes['sched-dow'].value='wed'; nodes['sched-time'].value='09:05';
 nodes['sched-role'].value='rd'; nodes['sched-skill'].value='calendar';
 nodes['sched-name'].value='Bad Name';
 await nodes['sched-form'].submit({preventDefault(){}});
 assert.equal(officeCalls.length,0); assert.match(nodes['sched-status'].textContent,/英文小寫/);
 nodes['sched-name'].value='weekly_sync'; nodes['sched-context'].value='週會';
 nodes['sched-freq'].change();
 assert.match(nodes['sched-preview'].textContent,/每週三 09:05/);
 assert.match(nodes['sched-preview'].textContent,/工程/);
 await nodes['sched-form'].submit({preventDefault(){}}); await tick();
 assert.deepEqual(officeCalls.pop(), {path:'/customer/office/schedule', body:{revision:'rev-1', hive:'shop',
   original:'', name:'weekly_sync', role:'rd', cron:'5 9 * * wed', skill:'calendar', context:'週會'}});
 // Name is optional: a free task_N is picked when left blank.
 findBtn(nodes.hives.children[0],'＋ 新增排程').click({});
 nodes['sched-name'].value=''; nodes['sched-context'].value='x';
 await nodes['sched-form'].submit({preventDefault(){}}); await tick();
 assert.equal(officeCalls.pop().body.name,'task_5');
 assert.equal(nodes['sched-form'].hidden,true);
 // Editing an existing one pre-fills the friendly controls from its cron.
 findBtn(nodes.hives.children[0],'編輯').click({});
 assert.equal(nodes['sched-freq'].value,'weekly'); assert.equal(nodes['sched-dow'].value,'mon');
 assert.equal(nodes['sched-time'].value,'09:00'); assert.equal(nodes['sched-name'].value,'digest');
 // A refusal keeps the form open and shows the server's own message.
 officeReply={ok:false,status:422,json:async()=>({error:'refused',message:'星期請用 mon…sun'})};
 await nodes['sched-form'].submit({preventDefault(){}}); await tick();
 assert.equal(officeCalls.pop().body.original,'digest');
 assert.equal(nodes['sched-form'].hidden,false);
 assert.match(nodes['sched-status'].textContent,/星期請用/);
 officeReply=null; nodes['sched-cancel'].click();
 assert.equal(nodes['sched-form'].hidden,true);
 findBtn(nodes.hives.children[0],'刪除').click({}); await tick(); await tick();
 assert.deepEqual(officeCalls.pop().body, {revision:'rev-1', hive:'shop', name:'digest'});
 const findSwitch = n => { if (n.className && /\bswitch\b/.test(n.className)) return n;
   for (const c of n.children||[]) { const f=findSwitch(c); if (f) return f; } return null; };
 const sw = findSwitch(nodes.hives.children[0]);
 assert.equal(sw['attr:role'],'switch'); assert.equal(sw['attr:aria-checked'],'false');
 assert.equal(sw['attr:aria-label'],'開啟這間公司的排程');
 sw.click({}); await tick(); await tick();
 assert.deepEqual(officeCalls.pop(), {path:'/customer/office/hive-enabled', body:{revision:'rev-1', hive:'shop', enabled:'true'}});
 office={template:'swarm',registry:'unreadable',hives:[],default_model:null};
 nodes.reload.click(); await tick();
 assert.match(nodes['office-note'].textContent,/格式有誤/);
 office=null;
 // Entering the site needs no password: a one-time URL is fetched and followed.
 assert.match(nodes['site-link'].textContent,/acme\.example/);
 let navigated=null; global.location={replace(u){navigated=u;}};
 nodes['site-enter'].click(); await tick(); await tick();
 assert.equal(navigated,'https://acme.example/auth/enter?ticket=t1');
 assert.ok(!nodes['site-secret'].textContent.includes('密碼'));
 // The form is revealed only when the platform confirms the route exists.
 assert.match(PAGE,/<details id="site-password" class="sitepw" hidden>/);
 assert.equal(nodes['site-password'].hidden,false);
 // Site password: validated locally, sent once, never echoed, fields cleared.
 const spSubmit = () => nodes['site-password-form'].submit({preventDefault(){}});
 nodes['site-password-new'].checked=true; nodes['site-password-reuse'].checked=false;
 nodes['site-password-input'].value='short'; nodes['site-password-confirm'].value='short';
 spSubmit(); await tick();
 assert.equal(sitePasswordBodies.length,0,'too short is refused before sending');
 assert.match(nodes['site-password-status'].textContent,/12/);
 nodes['site-password-input'].value='a-long-site-pass'; nodes['site-password-confirm'].value='a-long-site-pasX';
 spSubmit(); await tick();
 assert.equal(sitePasswordBodies.length,0,'mismatch is refused before sending');
 assert.match(nodes['site-password-status'].textContent,/不一致/);
 nodes['site-password-confirm'].value='a-long-site-pass';
 spSubmit(); await tick(); await tick(); await tick();
 assert.deepEqual(sitePasswordBodies[0],{password:'a-long-site-pass'});
 assert.equal(nodes['site-password-input'].value,'');
 assert.equal(nodes['site-password-confirm'].value,'');
 assert.match(nodes['site-password-status'].textContent,/acme\.example\/auth\/login.*admin/);
 assert.ok(!nodes['site-password-status'].textContent.includes('a-long-site-pass'));
 // Reuse sends only the platform password field; errors are explained.
 nodes['site-password-new'].checked=false; nodes['site-password-reuse'].checked=true;
 nodes['site-password-reuse'].change();
 assert.equal(nodes['site-password-new-fields'].hidden,true);
 assert.equal(nodes['site-password-reuse-fields'].hidden,false);
 sitePasswordReply=401; nodes['site-password-platform'].value='platform-pass-123';
 spSubmit(); await tick(); await tick(); await tick();
 assert.deepEqual(sitePasswordBodies[1],{platform_password:'platform-pass-123'});
 assert.match(nodes['site-password-status'].textContent,/平台密碼不正確/);
 assert.equal(nodes['site-password-save'].disabled,false);
 sitePasswordReply=502; spSubmit(); await tick(); await tick(); await tick();
 assert.match(nodes['site-password-status'].textContent,/已還原/);
 sitePasswordReply=200;
 jobStatus='succeeded_simulated'; siteHost=null; nodes.reload.click(); await tick();
 assert.equal(nodes['site-ready'].hidden,true);
 nextStep='rules';
 // The assistant floats and starts collapsed, so it cannot cover the page.
 // The real page declares hidden in markup; the fake node defaults to visible.
 assert.match(PAGE,/<aside id="copilot" hidden/);
 nodes.copilot.hidden = true;
 nodes['copilot-launcher'].click(); await tick();
 assert.equal(nodes.copilot.hidden,false);
 nodes['copilot-close'].click(); await tick();
 assert.equal(nodes.copilot.hidden,true);
 nodes['copilot-launcher'].click(); await tick();
 // The room is a real conversation: customer, assistant (and operators).
 nodes['chat-input'].value='這兩個角色差在哪？';
 await nodes['chat-form'].submit({preventDefault(){}});
 await tick(); await tick(); await tick(); await tick();
 assert.equal(nodes['chat-input'].value,'');
 assert.match(nodes['chat-log'].children[0].textContent,/你：這兩個角色/);
 assert.match(nodes['chat-log'].children[1].textContent,/助手：TEST ONLY/);
 // Messages carry a sender class for styling; never raw HTML.
 assert.equal(nodes['chat-log'].children[0].className,'msg customer');
 // The conversation is pinned to its newest message.
 assert.equal(nodes['chat-log'].scrollTop, nodes['chat-log'].scrollHeight);
 assert.equal(nodes['chat-log'].children[1].className,'msg ai');
 // Enter sends; Shift+Enter and IME composition (Enter commits a candidate) do not.
 const posts = () => calls.filter(c => c.path==='/customer/rooms/room-1/messages').length;
 const before = posts();
 const key = (extra) => { let prevented=false;
   nodes['chat-input'].keydown(Object.assign({key:'Enter',shiftKey:false,isComposing:false,keyCode:13,
     preventDefault(){prevented=true;}}, extra)); return prevented; };
 nodes['chat-input'].value='換行不送出';
 assert.equal(key({shiftKey:true}),false);
 assert.equal(key({isComposing:true}),false);
 assert.equal(key({keyCode:229}),false);
 assert.equal(key({key:'a'}),false);
 await tick(); await tick();
 assert.equal(posts(),before);
 assert.equal(key({}),true);
 await tick(); await tick(); await tick(); await tick();
 assert.equal(posts(),before+1);
 assert.equal(nodes['chat-input'].value,'');
 assert.match(nodes['chat-mode'].textContent,/AI 助手回覆/);
 assert.equal(nodes['chat-ask-ai'].hidden,true);
 // Staff replies are stored as sender_kind 'human' and must read as 平台人員.
 // Once staff joined (coassist) a new message does not auto-ask the AI;
 // the customer asks explicitly.
 thread.push({sender_kind:'human',body:'我是真人，來幫你'}); roomMode='coassist';
 const responds = () => calls.filter(c => c.path==='/customer/rooms/room-1/respond').length;
 const respondsBefore = responds();
 nodes['chat-input'].value='謝謝';
 await nodes['chat-form'].submit({preventDefault(){}});
 await tick(); await tick(); await tick(); await tick();
 const staffRow = nodes['chat-log'].children.find(r => /我是真人/.test(r.textContent));
 assert.equal(staffRow.textContent,'平台人員：我是真人，來幫你');
 assert.equal(staffRow.className,'msg staff');
 assert.equal(responds(),respondsBefore,'coassist: no automatic AI answer');
 assert.match(nodes['chat-mode'].textContent,/平台人員已接手/);
 assert.equal(nodes['chat-ask-ai'].hidden,false);
 // Nothing new since... the customer spoke last, so asking is allowed.
 assert.equal(nodes['chat-ask-ai'].disabled,false);
 // Typed text is sent FIRST, then the AI answers it (not an older message).
 const postsBeforeAsk = posts();
 nodes['chat-input'].value='幫我問怎麼申請 openai key';
 nodes['chat-input'].input();
 await nodes['chat-ask-ai'].click(); for (let i = 0; i < 6; i++) await tick();
 assert.equal(posts(),postsBeforeAsk+1);
 assert.equal(thread.at(-2).body,'幫我問怎麼申請 openai key');
 assert.equal(thread.at(-1).sender_kind,'ai');
 assert.equal(responds(),respondsBefore+1);
 assert.equal(nodes['chat-input'].value,'');
 // Right after the AI answered, an empty ask would only repeat it: disabled.
 assert.equal(nodes['chat-ask-ai'].disabled,true);
 nodes['chat-input'].value='x'; nodes['chat-input'].input();
 assert.equal(nodes['chat-ask-ai'].disabled,false);
 nodes['chat-input'].value=''; nodes['chat-input'].input();
 roomMode='ai';
 nodes['chat-input'].value='再問一次'; await nodes['chat-form'].submit({preventDefault(){}});
 await tick(); await tick(); await tick(); await tick();
 // Copilot: it states the real next step and offers exactly that action.
 assert.match(nodes['copilot-message'].textContent,/確認使用與支援說明/);
 assert.equal(nodes['copilot-act'].hidden,false);
 assert.equal(nodes['copilot-act'].textContent,'幫我確認說明');
 nodes['copilot-act'].click(); await tick(); await tick(); await tick();
 assert.equal(performed.length,1);
 assert.match(nodes['copilot-message'].textContent,/說明已確認/);
 assert.equal(nodes['copilot-act'].hidden,true,'no action left to offer');
 const count=calls.length;
 nodes.acknowledge.click(); nodes.acknowledge.click();
 assert.equal(calls.length,count+1);
 ackResolve(); await tick();
 assert.equal(nodes.acknowledge.disabled,true);
 assert.equal(nodes['rules-box'].open,false,'read rules fold away');
 assert.equal(nodes['rules-state'].textContent,'已閱讀');
 // The build-status line still reports each state in the customer's words.
 jobStatus='queued'; nodes.reload.click(); await tick();
 assert.match(nodes['site-job'].textContent,/幾分鐘/);
 assert.match(nodes['auto-note'].textContent,/自動更新/);
 jobStatus='reconciliation_required'; nodes.reload.click(); await tick();
 assert.match(nodes['site-job'].textContent,/不會自動重建/);
 assert.equal(nodes['auto-note'].textContent,'');
 jobStatus='not_requested'; nodes.reload.click(); await tick();
 for(const error of [401,409,'network','badjson']) {
  mode=error; nodes.reload.click(); await tick();
  assert.ok(nodes.status.textContent);
  assert.equal(nodes.acknowledge.disabled,true);
  assert.equal(nodes.reload.disabled,false);
  mode='ok'; nodes.reload.click(); await tick();
  assert.equal(nodes.acknowledge.disabled,false);
 }
})().then(()=>process.exit(0),e=>{process.stderr.write(String(e.stack));process.exit(1);});
'''
        # Expose the real markup so assertions can check what the page itself
        # declares (e.g. the widget starts hidden), not just the fake DOM.
        harness += '\nconst PAGE = ' + json.dumps(PAGE) + ';\n'
        result = subprocess.run([os.environ.get('NODE', 'node'), '-e', harness+JS+assertions],
                                text=True, capture_output=True,
                                env={k:v for k,v in os.environ.items() if k != 'NODE_OPTIONS'})
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
