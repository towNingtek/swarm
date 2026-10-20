// 拖曳改狀態記在 localStorage（wolffia.moves.<project>）：重新整理時資料還沒追上，就以剛做的改動為準；追上就刪、超過 15 分鐘丟掉。
// 和 v8running 同方式：node 假 DOM＋假的 XHR＋假的 localStorage 跑 v8.html；用示範資料（make-demo-epic.py，改成非示範才會寫入）。
const fs=require("fs"),dir=__dirname+"/../",DEMO="/tmp/wolffia-v8-moves-demo.js";
const h=fs.readFileSync(dir+"v8.html","utf8"); const re=/<script>([\s\S]*?)<\/script>/g; let m; const inl=[]; while((m=re.exec(h))) inl.push(m[1]);
require("child_process").execFileSync("python3",[dir+"make-demo-epic.py",DEMO]);
const demoSrc=fs.readFileSync(DEMO,"utf8").replace('"demo": true','"demo": false');
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};
const iso=t=>new Date(t).toISOString().replace(/\.\d+Z$/,"Z");
const src=fetched=>demoSrc.replace(/"fetched": "[^"]*"/,'"fetched": "'+iso(fetched)+'"');
let REQ=[];
function boot(o){
  o=o||{}; REQ=[];
  const st={}; const ctx=new Proxy({},{get:(_,k)=>k==="createRadialGradient"?(()=>({addColorStop(){}})):()=>{}});
  const mk=id=>st[id]||(st[id]={id,style:{},classList:{add(){},remove(){},contains(){return false}},addEventListener(t,f){(this.L=this.L||{})[t]=f},getContext:()=>ctx,clientWidth:1200,clientHeight:720,
    getBoundingClientRect(){return{left:0,top:0,width:1200,height:720}},getAttribute(k){return this[k]},querySelector(){return{onclick:null}},querySelectorAll(){return []},appendChild(){},innerHTML:"",textContent:"",value:"0"});
  global.window={devicePixelRatio:1,open(){return {}},addEventListener(){},matchMedia:()=>({matches:false})};
  global.addEventListener=global.window.addEventListener;
  if(o.ls===null) delete global.localStorage; else global.localStorage=o.ls||{getItem:()=>null,setItem(){}};
  global.location={search:"?demo=1"}; global.confirm=()=>true;
  global.setTimeout=()=>0; global.clearTimeout=()=>{};
  global.XMLHttpRequest=function(){ this.open=(mm,u)=>{this.m=mm;this.u=u}; this.setRequestHeader=()=>{}; this.send=b=>{ this.body=b; REQ.push(this); }; };
  const s=o.src||src(Date.now()-5*60e3);
  global.document={hidden:false,addEventListener(){},getElementById:mk,querySelector:()=>({onclick:null}),querySelectorAll:()=>[],createElement:()=>mk("x"+Math.random()),
    write(){ new Function(s)(); }};
  let T=0,F=null; global.performance={now:()=>T}; global.requestAnimationFrame=f=>{F=f};
  new Function('window.PROJECT_INDEX={projects:[],"default":0};')();
  inl.forEach((x,i)=>{ if(i===inl.length-1) x+=";global.__R={ITEMS,setStatus,refreshData,NOW,DONE};"; new Function(x)(); });
  for(let k=0;k<20;k++){ T+=40; F(T); }
  return global.__R;
}
const reply=(x,status,body)=>{ x.status=status; x.responseText=typeof body==="string"?body:JSON.stringify(body); x.onload(); };
const lastPost=()=>REQ.filter(x=>x.m==="POST"&&x.u==="api/status").slice(-1)[0];
const mkLS=store=>({getItem:k=>k in store?store[k]:null,setItem:(k,v)=>{store[k]=String(v);},removeItem:k=>{delete store[k];}});
const MK="wolffia.moves.demo", J=store=>JSON.parse(store[MK]||"{}");
const DOING="進行中 (7 天一輪)", REVIEW="審核後請關閉";
let LS={}, R=boot({ls:mkLS(LS)}); const I=n=>R.ITEMS.find(i=>i.n===n);
const U41=I(41).u, U42=I(42).u, U44=I(44).u, U48=I(48).u;
ok(I(41).st.col==="待辦"&&I(42).st.col===DOING,"（示範資料：#41 在待辦、#42 在進行中）");

// ── 1 改成功才寫進 localStorage ──
let t0=Date.now(); R.setStatus(I(41),DOING); reply(lastPost(),200,{ok:true,by:"t"});
ok(J(LS)[U41]&&J(LS)[U41].s===DOING&&J(LS)[U41].t>=t0&&J(LS)[U41].t<=Date.now(),"改動寫進 localStorage（"+MK+"）：#41 →「"+DOING+"」＋時間");
R.setStatus(I(42),REVIEW); reply(lastPost(),502,{error:"GitHub 更新失敗"});
ok(!J(LS)[U42]&&I(42).st.col===DOING,"改失敗 → 不寫進 localStorage，畫面退回原欄");
R.setStatus(I(44),R.DONE); reply(lastPost(),200,{ok:true,by:"t"});
ok(J(LS)[U44]&&J(LS)[U44].s===R.DONE,"拖進星核（完成）也記下：#44 →「完成」");

// ── 2 重新整理：資料比改動舊 → 以改動為準 ──
R=boot({ls:mkLS(LS)});
ok(I(41).st.col===DOING,"資料較舊（fetched 早於改動）時以改動為準：#41 顯示在「"+I(41).st.col+"」");
ok(I(44).st.col==="closed"&&!I(44).vis,"#44 顯示為完成（不在軌道上）");
ok(!!J(LS)[U41]&&!!J(LS)[U44],"資料還沒追上 → 改動留著");
ok(I(42).st.col===DOING&&I(48).st.col==="待辦","沒改過的照資料顯示");

// ── 3 資料追上就刪除 ──
R=boot({ls:mkLS(LS),src:src(Date.now()+60e3)});
ok(I(41).st.col==="待辦"&&!J(LS)[U41]&&!J(LS)[U44],"資料追上（fetched 晚於改動）→ 照資料顯示、改動從 localStorage 刪除");
LS={}; LS[MK]=JSON.stringify({[U42]:{s:DOING,t:Date.now()-60e3},[U41]:{s:REVIEW,t:Date.now()-60e3}});
R=boot({ls:mkLS(LS)});
ok(!J(LS)[U42]&&!!J(LS)[U41]&&I(41).st.col===REVIEW,"資料較舊但狀態已一致（#42 已在進行中）→ 刪掉那筆；#41 不一致的留著、照改動顯示");
// 之後重抓到的新資料追上了 → 也刪
R.refreshData(); const dr=REQ.filter(x=>/^data\//.test(x.u)).slice(-1)[0];
reply(dr,200,src(Date.now()+60e3));
ok(!J(LS)[U41],"重抓到的資料 fetched 晚於改動 → 刪除");

// ── 4 超過 15 分鐘丟掉 ──
LS={}; LS[MK]=JSON.stringify({[U41]:{s:DOING,t:Date.now()-16*60e3},[U48]:{s:REVIEW,t:Date.now()-14*60e3}});
R=boot({ls:mkLS(LS),src:src(Date.now()-30*60e3)});
ok(I(41).st.col==="待辦"&&!J(LS)[U41],"超過 15 分鐘的改動丟掉（#41 照資料在待辦、localStorage 清掉）");
ok(I(48).st.col===REVIEW&&!!J(LS)[U48],"14 分鐘的留著（#48 在「"+I(48).st.col+"」）");
R.setStatus(I(42),REVIEW); reply(lastPost(),200,{ok:true,by:"t"});
ok(!!J(LS)[U42]&&!!J(LS)[U48]&&!J(LS)[U41],"寫入新改動時也順便清掉過期的");

// ── 5 壞掉的內容／localStorage 不能用 ──
LS={}; LS[MK]="{oops"; let threw=null;
try{ R=boot({ls:mkLS(LS)}); }catch(e){ threw=e; }
ok(!threw&&I(41).st.col==="待辦","localStorage 內容壞掉 → 當作空的，不出錯");
LS={}; LS[MK]=JSON.stringify({[U41]:"x",[U42]:{s:5,t:"y"},[U48]:{s:"沒有這欄",t:Date.now()}});
threw=null; try{ R=boot({ls:mkLS(LS)}); }catch(e){ threw=e; }
ok(!threw&&I(41).st.col==="待辦"&&I(48).st.col==="待辦"&&JSON.stringify(J(LS))==="{}","格式不對的項目丟掉，不出錯");
const boom={getItem(){throw new Error("SecurityError");},setItem(){throw new Error("QuotaExceededError");}};
for(const [name,ls] of [["拋錯",boom],["不存在",null]]){
  threw=null; let col=null;
  try{ R=boot({ls}); R.setStatus(I(41),DOING); reply(lastPost(),200,{ok:true,by:"t"}); col=I(41).st.col; R=boot({ls}); }catch(e){ threw=e; }
  ok(!threw,"localStorage "+name+"時不出錯"+(threw?"："+threw.message:""));
  ok(!threw&&col===DOING&&I(41).st.col==="待辦","localStorage "+name+"時照原本行為：拖曳照常改、重新整理照資料顯示");
}
console.log(bad?"bad "+bad:"全部通過"); process.exit(bad?1:0);
