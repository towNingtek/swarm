// 重新放行：已放行（swarm:go）的 EPIC，放行時間（goAt）之後有 sub-issue 新開或內文被改 → 標題列「🚦 已放行」旁出現「↻ 重新放行」；
// 按了先確認，再 POST api/epic/rego {url}。沒有變動、沒放行、已關閉 → 不顯示。
// 和 v8running 同方式：node 假 DOM＋假的 XHR 跑 v8.html；用示範資料（make-demo-epic.py），不讀線上快取。
const fs=require("fs"),dir=__dirname+"/../",DEMO="/tmp/wolffia-v8-rego-demo.js";
const h=fs.readFileSync(dir+"v8.html","utf8"); const re=/<script>([\s\S]*?)<\/script>/g; let m; const inl=[]; while((m=re.exec(h))) inl.push(m[1]);
require("child_process").execFileSync("python3",[dir+"make-demo-epic.py",DEMO]);
const demoSrc=fs.readFileSync(DEMO,"utf8");
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};
let REQ=[], CONFIRM=[], ANSWER=true, st={}, Q={};
function boot(){
  REQ=[]; CONFIRM=[]; st={}; Q={};
  const ctx=new Proxy({},{get:(_,k)=>k==="createRadialGradient"?(()=>({addColorStop(){}})):()=>{}});
  const mk=id=>st[id]||(st[id]={id,style:{},classList:{add(){},remove(){},contains(){return false}},addEventListener(t,f){(this.L=this.L||{})[t]=f},getContext:()=>ctx,clientWidth:1200,clientHeight:720,
    getBoundingClientRect(){return{left:0,top:0,width:1200,height:720}},getAttribute(k){return this[k]},querySelector(){return{onclick:null}},querySelectorAll(){return []},appendChild(){},innerHTML:"",textContent:"",value:"0"});
  // #nebH 裡的按鈕：只有 innerHTML 真的有那個 class 才找得到
  const qs=sel=>{ const b=sel.match(/^#nebH \.([\w-]+)$/); if(!b) return {onclick:null};
    if(!new RegExp('class="'+b[1]+'"').test(mk("nebH").innerHTML)) return null; return Q[sel]=Q[sel]||{onclick:null}; };
  global.window={devicePixelRatio:1,open(){return {}},addEventListener(){},matchMedia:()=>({matches:false})};
  global.addEventListener=global.window.addEventListener;
  global.localStorage={getItem:()=>null,setItem(){}}; global.location={search:"?demo=1"};
  global.confirm=s=>{ CONFIRM.push(s); return ANSWER; };
  global.setTimeout=()=>0; global.clearTimeout=()=>0;
  global.XMLHttpRequest=function(){ this.open=(mm,u)=>{this.m=mm;this.u=u}; this.setRequestHeader=(k,v)=>{(this.H=this.H||{})[k]=v}; this.send=b=>{ this.body=b; REQ.push(this); }; };
  global.document={hidden:false,addEventListener(){},getElementById:mk,querySelector:qs,querySelectorAll:()=>[],createElement:()=>mk("x"+Math.random()),
    write(){ new Function(demoSrc)(); }};
  let T=0,F=null; global.performance={now:()=>T}; global.requestAnimationFrame=f=>{F=f};
  new Function('window.PROJECT_INDEX={projects:[],"default":0};')();
  inl.forEach((s,i)=>{ if(i===inl.length-1) s+=";global.__R={ITEMS,PJ,openNeb,closeNeb,kidsOf,paintNebH,needRego};"; new Function(s)(); });
  for(let k=0;k<40;k++){ T+=40; F(T); }
  return global.__R;
}
const H=()=>st["nebH"].innerHTML, hasBtn=()=>/class="rego"/.test(H());
const posts=()=>REQ.filter(x=>x.m==="POST");

let R=boot(); const I=n=>R.ITEMS.find(i=>i.n===n);
const E=I(101), K=R.kidsOf(E); ok(K.length>0,"#101 有 sub-issue");
const last=Math.max(...K.map(k=>k.created));
const go=t=>{ if(E.labels.indexOf("swarm:go")<0) E.labels.push("swarm:go"); E.goAt=t; K.forEach(k=>{ k.edited=null; }); };

// ── 1 沒有變動 → 不顯示 ──
go(last+60e3); R.openNeb(E);
ok(/已放行/.test(H())&&!hasBtn(),"已放行、之後沒有新增或修改的 sub-issue → 不顯示重新放行");
// 改過的時間早於放行 → 也不算
K[0].edited=last+30e3; R.paintNebH(); ok(!hasBtn(),"sub-issue 在放行前改過 → 不顯示");

// ── 2 放行後新增 sub-issue → 顯示 ──
go(last-60e3); R.paintNebH();
ok(/已放行/.test(H())&&hasBtn()&&/↻ 重新放行/.test(H()),"放行後有新增的 sub-issue → 「🚦 已放行」旁顯示「↻ 重新放行」");
ok(H().indexOf("已放行")<H().indexOf("重新放行"),"按鈕在「已放行」之後");
// 放行後內文被改 → 顯示
go(last+60e3); K[0].edited=last+120e3; R.paintNebH(); ok(hasBtn(),"放行後有 sub-issue 內文被改過 → 顯示");

// ── 3 不該顯示的情況 ──
E.goAt=null; R.paintNebH(); ok(!hasBtn(),"不知道放行時間 → 不顯示");
E.goAt=last-60e3; E.labels=E.labels.filter(l=>l!=="swarm:go"); R.paintNebH();
ok(!hasBtn()&&/class="go"/.test(H()),"沒有 swarm:go → 只有「🚦 放行」，沒有重新放行");
go(last-60e3); E.closed=Date.now(); R.paintNebH(); ok(!hasBtn(),"EPIC 已關閉 → 不顯示"); E.closed=null;

// ── 4 按鈕：先確認；取消就不送 ──
go(last-60e3); R.paintNebH(); R.PJ.demo=false;
const btn=()=>document.querySelector("#nebH .rego");
ok(btn()&&typeof btn().onclick==="function","按鈕有 onclick");
ANSWER=false; btn().onclick();
ok(CONFIRM.length===1&&/重新放行/.test(CONFIRM[0])&&/#101/.test(CONFIRM[0]),"按下會跳確認框（寫明 EPIC 編號）");
ok(posts().length===0,"確認框按取消 → 不呼叫 API");

// ── 5 確認 → POST api/epic/rego ──
ANSWER=true; btn().onclick(); const x=posts()[0];
ok(x&&x.u==="api/epic/rego","確認後呼叫 POST api/epic/rego");
ok(x&&JSON.parse(x.body).url===E.u&&/json/.test(x.H["Content-Type"]),"送出 {url: EPIC 網址}、Content-Type JSON");
ok(!REQ.some(r=>r.u==="api/epic/go"),"不會誤打 api/epic/go");
x.status=200; x.responseText=JSON.stringify({ok:true,by:"t@example.com"}); x.onload();
ok(E.goAt>last&&!hasBtn(),"成功後放行時間更新為現在 → 按鈕消失");
ok(/重新放行/.test(st["toast"].innerHTML),"成功有提示");
// 失敗
go(last-60e3); R.paintNebH(); btn().onclick(); const y=posts().slice(-1)[0];
y.status=400; y.responseText=JSON.stringify({error:"這個 EPIC 還沒放行過"}); y.onload();
ok(hasBtn()&&/還沒放行過/.test(st["toast"].innerHTML),"失敗 → 顯示錯誤、按鈕留著");

// ── 6 示範資料不寫 GitHub ──
R.PJ.demo=true; const n0=posts().length; btn().onclick(); ok(posts().length===n0,"示範資料 → 不呼叫 API");

console.log(bad?"bad "+bad:"全部通過"); process.exit(bad?1:0);
