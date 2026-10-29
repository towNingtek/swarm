// 🧠 想：頁面在 /board/（站台 DSH）底下時，Copilot 面板多一個「🧠 想」＋「這個 project／全部 project」；
// 按下去把 project（或 *）、選取的卡、問題、看板脈絡 POST 到 api/think，成功後等站台 harness 回覆並顯示在這裡（api/think/reply）。不在 /board/ 底下不顯示。
// 和 v8rego 同方式：node 假 DOM＋假的 XHR 跑 v8.html；用示範資料（make-demo-epic.py），把 demo 旗標關掉才會真的送 XHR。
const fs=require("fs"),dir=__dirname+"/../",DEMO="/tmp/wolffia-v8-think-demo.js";
const h=fs.readFileSync(dir+"v8.html","utf8"); const re=/<script>([\s\S]*?)<\/script>/g; let m; const inl=[]; while((m=re.exec(h))) inl.push(m[1]);
require("child_process").execFileSync("python3",[dir+"make-demo-epic.py",DEMO]);
const demoSrc=fs.readFileSync(DEMO,"utf8");
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};
let REQ=[], st={}, MADE=[];
function boot(pathname){
  REQ=[]; st={}; MADE=[];
  const ctx=new Proxy({},{get:(_,k)=>k==="createRadialGradient"?(()=>({addColorStop(){}})):()=>{}});
  const mk=id=>st[id]||(st[id]={id,style:{},classList:{add(){},remove(){},contains(){return false}},addEventListener(t,f){(this.L=this.L||{})[t]=f},getContext:()=>ctx,clientWidth:1200,clientHeight:720,
    getBoundingClientRect(){return{left:0,top:0,width:1200,height:720}},getAttribute(k){return this[k]},querySelector(){return{onclick:null}},querySelectorAll(){return []},appendChild(){},innerHTML:"",textContent:"",value:"0"});
  global.window={devicePixelRatio:1,open(){return {}},addEventListener(){},matchMedia:()=>({matches:false})};
  global.addEventListener=global.window.addEventListener;
  global.localStorage={getItem:()=>null,setItem(){}}; global.location={search:"?demo=1",pathname};
  global.confirm=()=>true; global.setTimeout=()=>0; global.clearTimeout=()=>0;
  global.XMLHttpRequest=function(){ this.open=(mm,u)=>{this.m=mm;this.u=u}; this.setRequestHeader=(k,v)=>{(this.H=this.H||{})[k]=v}; this.send=b=>{ this.body=b; REQ.push(this); }; };
  global.document={hidden:false,addEventListener(){},getElementById:mk,querySelector:()=>({onclick:null}),querySelectorAll:()=>[],
    createElement:()=>{ const e=mk("x"+Math.random()); MADE.push(e); return e; }, write(){ new Function(demoSrc)(); }};
  let T=0,F=null; global.performance={now:()=>T}; global.requestAnimationFrame=f=>{F=f};
  new Function('window.PROJECT_INDEX={projects:[],"default":0};')();
  inl.forEach((s,i)=>{ if(i===inl.length-1) s+=";global.__R={ITEMS,PJ,setSel};"; new Function(s)(); });
  for(let k=0;k<20;k++){ T+=40; F(T); }
  return global.__R;
}
const shown=()=>st["thinkRow"].style.display==="flex";
const posts=()=>REQ.filter(x=>x.m==="POST"&&x.u==="api/think");
const last=()=>MADE[MADE.length-1].innerHTML;

ok(/id="thinkB"[^>]*>📊 分析</.test(h)&&/id="thinkScope"[\s\S]*這個 project[\s\S]*全部 project/.test(h),"Copilot 面板有「🧠 想」按鈕和「這個 project／全部 project」切換");

// ── 1 不在 /board/ 底下 → 不顯示 ──
for(const p of ["/","/v8.html","/wolffia/v8.html","/boardx/","/share/abc/"]){ boot(p); ok(!shown(),"網址 "+p+" → 不顯示「想」"); }
boot(undefined); ok(!shown(),"沒有 pathname → 不顯示");

// ── 2 /board/ 底下 → 顯示 ──
let R=boot("/board/"); ok(shown(),"/board/ 下顯示「分析」按鈕");
ok(typeof st["thinkB"].onclick==="function","按鈕有 onclick");
boot("/board/index.html"); ok(shown(),"/board/index.html 也顯示");

// ── 2b /board/ 底下按 Enter／送出 → 走「想」（站台沒有 api/chat），不打 api/chat ──
R=boot("/board/"); R.PJ.demo=false;
st["chatIn"].value="送出鍵也要能用"; st["thinkScope"].value="one";
st["chatF"].onsubmit({preventDefault(){}});
ok(posts().length===1&&!REQ.some(r=>r.u==="api/chat"),"/board/ 下「送出」→ POST api/think，不呼叫 api/chat");
boot("/"); st["chatIn"].value="hi"; st["chatF"].onsubmit({preventDefault(){}});
ok(REQ.some(r=>r.u==="api/chat")&&posts().length===0,"不在 /board/ → 送出照舊走 api/chat");

// ── 3 送出到 api/think：這個 project ──
R=boot("/board/"); R.PJ.demo=false;
st["chatIn"].value="";
st["thinkB"].onclick(); ok(posts().length===0&&/寫下要分析的問題/.test(last()),"沒寫問題 → 不送、提示先寫問題");
const pick=R.ITEMS.filter(it=>it.vis).slice(0,2); R.setSel(pick,"set");
st["chatIn"].value="我選的這兩張卡為什麼卡住？"; st["thinkScope"].value="one";
st["thinkB"].onclick(); let x=posts()[0];
ok(x&&x.u==="api/think"&&x.m==="POST","送出到 POST api/think（相對路徑＝/board/api/think）");
ok(x&&/json/.test(x.H["Content-Type"]),"Content-Type JSON");
let b=x?JSON.parse(x.body):{};
ok(b.project===String(R.PJ.num),"「這個 project」→ project 是目前的 project 編號（"+b.project+"）");
ok(b.question==="我選的這兩張卡為什麼卡住？","帶問題");
ok(JSON.stringify(b.selection)===JSON.stringify(pick.map(it=>it.n))&&pick.length===2,"帶選取的卡 "+JSON.stringify(b.selection));
ok(b.context&&b.context.project&&Array.isArray(b.context.issues)&&b.context.issues.length>0,"帶看板脈絡（project、issues）");
ok(Buffer.byteLength(x.body)<=64*1024,"內容在 64KB 以內");
ok(st["chatIn"].value==="","送出後清空輸入框");
st["thinkB"].onclick(); ok(posts().length===1,"還沒回來前再按 → 不重複送");
x.status=200; x.responseText=JSON.stringify({sessionId:"s1",created:true,after:-1}); x.onload();
const done=MADE.filter(e=>/正在分析…/.test(e.innerHTML)).pop();
ok(!!done,"成功 → 顯示「正在分析…」");
// 回覆：問 api/think/reply，還沒好 → 繼續等；好了 → 顯示 harness 的回答＋到站台接著聊的連結
const rq=()=>REQ.filter(r=>r.m==="GET"&&/^api\/think\/reply\?/.test(r.u));
let y=rq().pop();
ok(y&&/session=s1/.test(y.u)&&/after=-1/.test(y.u)&&/q=/.test(y.u),"送出後問 GET api/think/reply（session、after、q）");
y.status=200; y.responseText=JSON.stringify({done:false}); y.onload();
ok(/正在分析/.test(done.innerHTML),"還沒回覆 → 仍顯示正在分析");
y.status=200; y.responseText=JSON.stringify({done:true,text:"建議合成 **兩個 EPIC**"}); y.onload();
ok(/建議合成 <b>兩個 EPIC<\/b>/.test(done.innerHTML),"回覆好了 → 顯示 harness 的回答（粗體轉換）");
ok(/<a class="dim" href="\/\?swarm-session=s1" target="_blank"/.test(done.innerHTML),"回答下面的「在站台對話接著聊」直接打開這個 session（/?swarm-session=s1）");

// ── 4 全部 project → project:"*" ──
st["chatIn"].value="跨 project 有沒有重複的工作？"; st["thinkScope"].value="all"; st["thinkB"].onclick();
x=posts()[1]; b=x?JSON.parse(x.body):{};
ok(b.project==="*","「全部 project」→ project 是 *");
x.status=200; x.responseText=JSON.stringify({sessionId:"s2",created:false,after:12}); x.onload();
ok(/正在分析…（全部 project/.test(last()),"全部 project 成功提示");
ok(/after=12/.test((rq().pop()||{}).u||""),"續用的對話 → 只等這次之後的回覆（after=12）");

// ── 5 失敗 → 顯示錯誤 ──
st["chatIn"].value="再問一次"; st["thinkScope"].value="one"; st["thinkB"].onclick();
x=posts()[2]; x.status=502; x.responseText=JSON.stringify({error:"think/gateway",message:"送不進站台 DSH：gateway/internal"}); x.onload();
ok(/送不進站台 DSH/.test(last())&&!/已送進/.test(last()),"失敗 → 顯示伺服器的錯誤訊息");
st["chatIn"].value="連線斷了"; st["thinkB"].onclick(); x=posts()[3]; x.onerror(); ok(/連不到伺服器/.test(last()),"連不上 → 提示");
st["chatIn"].value="恢復了嗎"; st["thinkB"].onclick(); ok(posts().length===5,"失敗後可以再送");

// ── 5b 其他組織的 project → 送它自己的 key（"組織/編號"），不和預設組織同編號的混在一起 ──
{ const pending=posts().pop(); pending.status=502; pending.responseText="{}"; pending.onload(); }   // 上一步送出的還在等，先讓它結束
const before=posts().length;
R.PJ.key="bright-ideass/1"; st["chatIn"].value="這個 EPIC 還有在動嗎？"; st["thinkScope"].value="one"; st["thinkB"].onclick();
ok(posts().length===before+1,"其他組織的 project 也會送出");
x=posts().pop(); b=x?JSON.parse(x.body):{};
ok(b.project==="bright-ideass/1","其他組織的 project → project 是 \"bright-ideass/1\"（"+b.project+"）");
// 原本的分析對話被封存 → 伺服器開了新的，頁面說明
x.status=200; x.responseText=JSON.stringify({sessionId:"s9",created:true,after:-1,replaced:true}); x.onload();
const rep=MADE.filter(e=>/正在分析…/.test(e.innerHTML)).pop();
ok(rep&&/已封存/.test(rep.innerHTML),"原本的分析對話已封存 → 提示開了新的對話");
// DSH 沒有執行這個對話（blocked）→ 顯示原因，不再一直轉圈
y=rq().pop(); y.status=200; y.responseText=JSON.stringify({done:true,error:"blocked",text:"",message:"DSH 沒有執行這個對話（它可能已被封存）。"}); y.onload();
ok(/DSH 沒有執行這個對話/.test(rep.innerHTML)&&!/正在分析/.test(rep.innerHTML),"回覆 blocked → 顯示原因，不再顯示正在分析");
const nAfter=rq().length; ok(nAfter>0&&rq().length===nAfter,"blocked 之後不再輪詢");
R.PJ.key=undefined;

// ── 6 示範資料不送 ──
R.PJ.demo=true; const n0=posts().length; st["chatIn"].value="示範"; st["thinkB"].onclick();
ok(posts().length===n0,"示範資料 → 不呼叫 api/think");
ok(!REQ.some(r=>r.u==="api/chat"&&r.m==="POST"),"「想」不會走 api/chat");

console.log(bad?"bad "+bad:"全部通過"); process.exit(bad?1:0);
