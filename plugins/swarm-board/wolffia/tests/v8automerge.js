// 自動合併（swarm:auto-merge）：放行對話框的勾選、已放行 EPIC 的「⚡ 自動合併：開／關」切換、⚡ 小標示。
// 和 v8running 同方式：node 假 DOM＋假的 XHR 跑 v8.html；用示範資料（make-demo-epic.py），但把 demo 旗標關掉，才會真的送 XHR。
const fs=require("fs"),dir=__dirname+"/../",DEMO="/tmp/wolffia-v8-automerge-demo.js";
const h=fs.readFileSync(dir+"v8.html","utf8"); const re=/<script>([\s\S]*?)<\/script>/g; let m; const inl=[]; while((m=re.exec(h))) inl.push(m[1]);
require("child_process").execFileSync("python3",[dir+"make-demo-epic.py",DEMO]);
const demoSrc=fs.readFileSync(DEMO,"utf8");
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};
let REQ=[], TEXT=[], CONFIRM=[], ANSWER=true;
function boot(){
  REQ=[]; TEXT=[]; CONFIRM=[];
  const st={}; const ctx=new Proxy({},{get:(_,k)=>k==="createRadialGradient"?(()=>({addColorStop(){}})):k==="fillText"?((s,x,y)=>TEXT.push(String(s))):()=>{}});
  const mk=id=>st[id]||(st[id]={id,style:{},classList:{add(){},remove(){},contains(){return false}},addEventListener(t,f){(this.L=this.L||{})[t]=f},getContext:()=>ctx,clientWidth:1200,clientHeight:720,
    getBoundingClientRect(){return{left:0,top:0,width:1200,height:720}},getAttribute(k){return this[k]},querySelector(){return{onclick:null}},querySelectorAll(){return []},appendChild(){},innerHTML:"",textContent:"",value:"0"});
  // 標題列的按鈕：innerHTML 裡有那個 class 才找得到（同一個選擇器回傳同一個物件，測試才能按它）
  const QS={};
  const qs=sel=>{ const mm=sel.match(/^#(\w+) \.(\w+)$/); if(mm&&!new RegExp('class="'+mm[2]+'"').test(mk(mm[1]).innerHTML)) return null;
    return QS[sel]||(QS[sel]={onclick:null}); };
  // 對話框：像真的 DOM 一樣，設 innerHTML 時重建裡面帶 id 的元素（勾選框回到 HTML 寫的狀態）
  let dh=""; Object.defineProperty(mk("goDlg"),"innerHTML",{get:()=>dh,set:v=>{ dh=v; (v.match(/id="\w+"/g)||[]).forEach(t=>{ const id=t.slice(4,-1); delete st[id]; mk(id).checked=/checked/.test(v); }); }});
  const WL={};
  global.window={devicePixelRatio:1,open(){return {}},addEventListener(t,f){(WL[t]=WL[t]||[]).push(f)},matchMedia:()=>({matches:false})};
  global.addEventListener=global.window.addEventListener;
  global.localStorage={getItem:()=>null,setItem(){}}; global.location={search:"?demo=1"};
  global.confirm=q=>{ CONFIRM.push(q); return ANSWER; };
  global.setTimeout=()=>0; global.clearTimeout=()=>0;
  global.XMLHttpRequest=function(){ this.open=(mm,u)=>{this.m=mm;this.u=u}; this.setRequestHeader=()=>{}; this.send=b=>{ this.body=b; REQ.push(this); }; };
  global.document={hidden:false,addEventListener(){},getElementById:mk,querySelector:qs,querySelectorAll:()=>[],createElement:()=>mk("x"+Math.random()),
    write(s){ new Function(demoSrc)(); }};
  let T=0,F=null; global.performance={now:()=>T}; global.requestAnimationFrame=f=>{F=f};
  new Function('window.PROJECT_INDEX={projects:[],"default":0};')();
  inl.forEach((s,i)=>{ if(i===inl.length-1) s+=";global.__R={ITEMS,openNeb,closeNeb,get NEB(){return NEB}};"; new Function(s)(); });
  global.window.PROJECT.demo=false;   // 示範資料不寫 GitHub；這裡要測送出的內容
  const tick=n=>{for(let k=0;k<n;k++){T+=40;F(T);}};
  tick(10);
  return {R:global.__R,st,QS,WL,tick};
}
const reply=(x,status,body)=>{ x.status=status; x.responseText=JSON.stringify(body); x.onload(); };
const posts=u=>REQ.filter(x=>x.m==="POST"&&x.u===u);
const esc=ev=>{ (W.WL.keydown||[]).forEach(f=>f(Object.assign({key:"Escape",target:{tagName:"BODY"},preventDefault(){},stopPropagation(){}},ev))); };

let W=boot(), {R,st,QS}=W; const I=n=>R.ITEMS.find(i=>i.n===n);
const A=I(101), B=I(103), C=I(102);

// ── 1 放行對話框：自訂對話框（不是 confirm）、有勾選、預設不勾 ──
R.openNeb(A);
ok(!!QS["#nebH .go"]&&/🚦 放行/.test(st.nebH.innerHTML),"未放行的 EPIC #101 標題列有「🚦 放行」");
ok(!/class="am"/.test(st.nebH.innerHTML),"未放行的 EPIC 沒有自動合併切換");
QS["#nebH .go"].onclick();
ok(CONFIRM.length===0,"放行不再用 confirm()");
ok(st.goDlg.style.display==="flex","按放行 → 打開自訂對話框");
ok(/type="checkbox" id="goAM"/.test(st.goDlg.innerHTML),"對話框裡有勾選框");
ok(/低風險：判準全過、只改範圍內檔案時，自動合併總 PR（swarm:auto-merge）/.test(st.goDlg.innerHTML),"勾選文字：「低風險：判準全過、只改範圍內檔案時，自動合併總 PR（swarm:auto-merge）」");
ok(!/checked/.test(st.goDlg.innerHTML)&&!st.goAM.checked,"勾選預設不勾");
ok(posts("api/epic/go").length===0,"只打開對話框，還沒送出");

// 取消／Esc 不送
st.goNo.onclick(); ok(st.goDlg.style.display==="none"&&posts("api/epic/go").length===0,"按取消 → 關閉對話框，不送出");
QS["#nebH .go"].onclick(); esc(); ok(st.goDlg.style.display==="none"&&posts("api/epic/go").length===0&&R.NEB,"Esc → 只關對話框（EPIC 面板還開著），不送出");

// ── 2 不勾 → 只送 url ──
QS["#nebH .go"].onclick(); st.goOk.onclick();
let x=posts("api/epic/go")[0], body=x&&JSON.parse(x.body);
ok(!!x&&body.url===A.u&&!("auto_merge" in body),"不勾 → POST api/epic/go 只帶 url："+(x&&x.body));
ok(st.goDlg.style.display==="none","送出後關閉對話框");
reply(x,200,{ok:true,by:"a@b.c",already:false});
ok(A.labels.indexOf("swarm:go")>=0&&A.labels.indexOf("swarm:auto-merge")<0,"成功 → 加上 swarm:go，沒有 swarm:auto-merge");
ok(/🚦 已放行/.test(st.nebH.innerHTML),"標題列變成「🚦 已放行」");
ok(!!QS["#nebH .am"]&&/⚡ 自動合併：關/.test(st.nebH.innerHTML),"已放行 EPIC 標題列顯示「⚡ 自動合併：關」切換");
ok(!/class="amk"/.test(st.nebH.innerHTML),"沒有 swarm:auto-merge → 沒有 ⚡ 小標示");

// ── 3 勾選 → 送出 auto_merge:true ──
R.openNeb(B); QS["#nebH .go"].onclick();
ok(!/checked/.test(st.goDlg.innerHTML),"重新打開對話框：勾選仍是預設不勾");
st.goAM.checked=true; st.goOk.onclick();
x=posts("api/epic/go")[1]; body=x&&JSON.parse(x.body);
ok(!!x&&body.url===B.u&&body.auto_merge===true,"勾選 → POST api/epic/go 帶 auto_merge:true："+(x&&x.body));
reply(x,200,{ok:true,by:"a@b.c",already:false,auto_merge:true});
ok(B.labels.indexOf("swarm:go")>=0&&B.labels.indexOf("swarm:auto-merge")>=0,"成功 → 加上 swarm:go 和 swarm:auto-merge");
ok(/⚡ 自動合併：開/.test(st.nebH.innerHTML),"標題列顯示「⚡ 自動合併：開」");
ok(/class="amk"[^>]*>⚡</.test(st.nebH.innerHTML),"EPIC 面板：帶 swarm:auto-merge 的 EPIC 有 ⚡ 小標示");

// 失敗不改 label
R.openNeb(C); st.goAM.checked=false; QS["#nebH .go"].onclick(); st.goOk.onclick();
reply(posts("api/epic/go")[2],403,{error:"x 不在允許名單"});
ok(C.labels.indexOf("swarm:go")<0,"放行失敗（403）→ 不加 label");

// ── 4 自動合併切換：有確認、呼叫 api/epic/automerge ──
R.openNeb(B);
ANSWER=false; QS["#nebH .am"].onclick();
ok(CONFIRM.length===1&&/關閉/.test(CONFIRM[0])&&/swarm:auto-merge/.test(CONFIRM[0]),"按「⚡ 自動合併：開」→ 先確認要關閉");
ok(posts("api/epic/automerge").length===0,"確認按取消 → 不送出");
ANSWER=true; QS["#nebH .am"].onclick();
x=posts("api/epic/automerge")[0]; body=x&&JSON.parse(x.body);
ok(!!x&&body.url===B.u&&body.on===false,"確認 → POST api/epic/automerge {on:false}："+(x&&x.body));
reply(x,200,{ok:true,by:"a@b.c",on:false,already:false});
ok(B.labels.indexOf("swarm:auto-merge")<0&&/⚡ 自動合併：關/.test(st.nebH.innerHTML)&&!/class="amk"/.test(st.nebH.innerHTML),"成功 → 移除 label，切換變「關」、⚡ 小標示消失");
QS["#nebH .am"].onclick();
x=posts("api/epic/automerge")[1]; body=x&&JSON.parse(x.body);
ok(/開啟/.test(CONFIRM.slice(-1)[0])&&!!x&&body.on===true,"再按 → 確認開啟，送出 {on:true}");
reply(x,500,{error:"GitHub 更新失敗"});
ok(B.labels.indexOf("swarm:auto-merge")<0&&/⚡ 自動合併：關/.test(st.nebH.innerHTML),"失敗 → label 不變，仍是「關」");
QS["#nebH .am"].onclick(); reply(posts("api/epic/automerge")[2],200,{ok:true,by:"a@b.c",on:true});
ok(B.labels.indexOf("swarm:auto-merge")>=0&&/⚡ 自動合併：開/.test(st.nebH.innerHTML),"成功 → 加回 label，切換變「開」");

// ── 5 主星系：帶 swarm:auto-merge 的 EPIC 有 ⚡ 小標示 ──
R.closeNeb(); TEXT=[]; W.tick(2);
const lab=n=>TEXT.filter(s=>s.indexOf("#"+n+" · EPIC")===0);
ok(lab(103).length>0&&lab(103).every(s=>/⚡/.test(s)),"主星系：#103（帶 swarm:auto-merge）標籤有 ⚡："+lab(103)[0]);
ok(lab(101).length>0&&lab(101).every(s=>!/⚡/.test(s)),"主星系：#101（沒有）標籤沒有 ⚡："+lab(101)[0]);

// ── 6 未放行但已經帶 label（例如在 GitHub 手動加的）：只有小標示，沒有切換 ──
({R,st,QS}=W=boot()); const C2=R.ITEMS.find(i=>i.n===102); C2.labels.push("swarm:auto-merge");
R.openNeb(C2);
ok(/class="amk"/.test(st.nebH.innerHTML)&&!/class="am"/.test(st.nebH.innerHTML)&&!!QS["#nebH .go"],"未放行、帶 swarm:auto-merge → 有 ⚡ 小標示、仍是「🚦 放行」按鈕，沒有切換");

console.log(bad?"bad "+bad:"all ok"); process.exit(bad?1:0);
