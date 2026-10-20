// 💡 建議：頁面在 /board/（站台 DSH）底下時，側欄 GET api/suggestions（站台每日聚合的結果），
// 有建議就多一個「💡 建議」區：標題、縱向／橫向、相關 issue 連結、理由、產生時間；沒有建議或不在 /board/ 底下就不顯示。
// 和 v8think 同方式：node 假 DOM＋假的 XHR 跑 v8.html（示範資料）。
const fs=require("fs"),dir=__dirname+"/../",DEMO="/tmp/wolffia-v8-suggest-demo.js";
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
const shown=()=>st["sugSec"].style.display==="block";
const gets=()=>REQ.filter(x=>x.m==="GET"&&x.u==="api/suggestions");
const html=()=>st["sug"].innerHTML;
const SUG={generatedAt:"2026-10-19T01:02:00Z",sessionId:"g1",suggestions:[
  {title:"看板 × 想 收成一個 EPIC",kind:"vertical",issues:["towNingtek/swarm#520","towNingtek/swarm#522"],why:"兩張都在做 /board/ 的「想」"},
  {title:"各站共用登入",kind:"horizontal",issues:["towNingtek/a#3","other-org/b.site#14"],why:"兩個 project 都要 <登入>"}]};
function answer(status,body){ const x=gets()[0]; x.status=status; x.responseText=typeof body==="string"?body:JSON.stringify(body); x.onload(); return x; }

ok(/id="sugSec"[\s\S]*💡 建議/.test(h),"側欄有「💡 建議」區");
ok(/api\/suggestions/.test(h),"讀 api/suggestions");

// ── 1 不在 /board/ 底下 → 不讀也不顯示 ──
for(const p of ["/","/v8.html","/wolffia/v8.html","/boardx/","/share/abc/"]){ boot(p); ok(!shown()&&gets().length===0,"網址 "+p+" → 不顯示「💡 建議」、不讀 api/suggestions"); }
boot(undefined); ok(!shown(),"沒有 pathname → 不顯示");

// ── 2 /board/ 底下、有建議 → 顯示 ──
boot("/board/"); ok(gets().length===1,"/board/ 下 GET api/suggestions（相對路徑＝/board/api/suggestions）");
ok(!shown(),"還沒回來前不顯示");
answer(200,SUG);
ok(shown(),"有建議 → 顯示「💡 建議」");
ok(/看板 × 想 收成一個 EPIC/.test(html())&&/各站共用登入/.test(html()),"列出建議標題");
ok(/縱向/.test(html())&&/橫向/.test(html()),"標出縱向／橫向");
ok(html().includes('href="https://github.com/towNingtek/swarm/issues/520"')&&html().includes('href="https://github.com/towNingtek/swarm/issues/522"'),"相關 issue 連結（towNingtek/swarm#520、#522）");
ok(html().includes('href="https://github.com/other-org/b.site/issues/14"'),"跨 project 的 issue 連結（other-org/b.site#14）");
ok(/target="_blank"/.test(html()),"issue 連結開新分頁");
ok(/兩張都在做/.test(html()),"顯示理由");
ok(/&lt;登入&gt;/.test(html())&&!/<登入>/.test(html()),"理由有跳脫 HTML");
ok(/產生於 2026-10-\d\d \d\d:\d\d/.test(html()),"顯示產生時間");
boot("/board/index.html"); answer(200,SUG); ok(shown(),"/board/index.html 也顯示");

// ── 3 沒有建議 → 不顯示 ──
boot("/board/"); answer(200,{suggestions:[]}); ok(!shown(),"沒有建議（空清單）→ 不顯示");
boot("/board/"); answer(200,{generatedAt:"2026-10-19T01:02:00Z",sessionId:"g1",suggestions:[]}); ok(!shown(),"有產生時間但沒有建議 → 不顯示");
boot("/board/"); answer(404,"not found"); ok(!shown(),"404（舊站台沒有這個 API）→ 不顯示");
boot("/board/"); answer(200,"不是 JSON"); ok(!shown(),"壞掉的 JSON → 不顯示");
boot("/board/"); gets()[0].onerror(); ok(!shown(),"連不上 → 不顯示");

// ── 4 不影響「想」 ──
boot("/board/"); ok(st["thinkRow"].style.display==="flex","「🧠 想」照常顯示");
ok(!REQ.some(r=>r.m==="POST"),"讀建議不送任何 POST（不開卡、不改狀態）");

console.log(bad?"bad "+bad:"全部通過"); process.exit(bad?1:0);
