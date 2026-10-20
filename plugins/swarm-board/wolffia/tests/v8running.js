// 運行動畫：每 15 秒 GET api/running（頁面隱藏時暫停）→ 工作中的星有 🐝 繞行＋「引擎 · 已 N 分」、EPIC 標題列「🐝 N 隻工作中」、完成時滑進最內圈。
// 新出現的工作中 issue 移到「進行中」；蜂消失後 EPIC 的 sub-issue 全關 → EPIC 移到「審核後請關閉」（還沒改就 30 秒後再重抓，最多 4 次）。
// 和 v8epic 同方式：node 假 DOM＋假的 XHR 跑 v8.html；用示範資料（make-demo-epic.py），不讀線上快取。
const fs=require("fs"),dir=__dirname+"/../",DEMO="/tmp/wolffia-v8-running-demo.js";
const h=fs.readFileSync(dir+"v8.html","utf8"); const re=/<script>([\s\S]*?)<\/script>/g; let m; const inl=[]; while((m=re.exec(h))) inl.push(m[1]);
require("child_process").execFileSync("python3",[dir+"make-demo-epic.py",DEMO]);
const demoSrc=fs.readFileSync(DEMO,"utf8");
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};
let REQ=[], TM=[], TEXT=[];
function boot(o){
  o=o||{}; REQ=[]; TM=[]; TEXT=[];
  const st={}; const ctx=new Proxy({},{get:(_,k)=>k==="createRadialGradient"?(()=>({addColorStop(){}})):k==="fillText"?((s,x,y)=>TEXT.push({s:String(s),x,y})):()=>{}});
  const mk=id=>st[id]||(st[id]={id,style:{},classList:{add(){},remove(){},contains(){return false}},addEventListener(t,f){(this.L=this.L||{})[t]=f},getContext:()=>ctx,clientWidth:1200,clientHeight:720,
    getBoundingClientRect(){return{left:0,top:0,width:1200,height:720}},getAttribute(k){return this[k]},querySelector(){return{onclick:null}},querySelectorAll(){return []},appendChild(){},innerHTML:"",textContent:"",value:"0"});
  const WL={}, DL={};
  global.window={devicePixelRatio:1,open(){return {}},addEventListener(t,f){WL[t]=f},matchMedia:q=>({matches:!!o.reduce&&/prefers-reduced-motion:\s*reduce/.test(q)})};
  global.addEventListener=global.window.addEventListener;
  if(o.ls===null) delete global.localStorage; else global.localStorage=o.ls||{getItem:()=>null,setItem(){}}; global.location={search:"?demo=1"}; global.confirm=()=>true;
  global.setTimeout=(f,ms)=>{TM.push({f,ms}); return TM.length;}; global.clearTimeout=id=>{ if(TM[id-1]) TM[id-1].cleared=true; };
  global.XMLHttpRequest=function(){ this.open=(mm,u)=>{this.m=mm;this.u=u}; this.setRequestHeader=()=>{}; this.send=b=>{ REQ.push(this); }; };
  global.document={hidden:false,addEventListener(t,f){DL[t]=f},getElementById:mk,querySelector:()=>({onclick:null}),querySelectorAll:()=>[],createElement:()=>mk("x"+Math.random()),
    write(s){ new Function(demoSrc)(); }};
  let T=0,F=null; global.performance={now:()=>T}; global.requestAnimationFrame=f=>{F=f};
  new Function('window.PROJECT_INDEX={projects:[],"default":0};')();
  inl.forEach((s,i)=>{ if(i===inl.length-1) s+=";global.__R={ITEMS,openNeb,closeNeb,kidsOf,isOpenAt,NOW,NG,RING,CX,CY,SX,get RUN(){return RUN},get FIN(){return FIN},get PEND(){return PEND}};"; new Function(s)(); });
  const tick=n=>{for(let k=0;k<n;k++){T+=40;F(T);}};
  tick(40);
  return {R:global.__R,st,DL,tick};
}
const reply=(x,status,body)=>{ x.status=status; x.responseText=typeof body==="string"?body:JSON.stringify(body); x.onload(); };
const runReqs=()=>REQ.filter(x=>x.m==="GET"&&x.u==="api/running");
const lastRun=()=>runReqs().slice(-1)[0];
const ago=min=>new Date(Date.now()-min*60e3-5e3).toISOString();
const timers=()=>TM.filter(t=>t.ms===15000&&!t.cleared);
const fire=()=>{ const t=timers().slice(-1)[0]; t.cleared=true; t.f(); };

// ── 1 會呼叫 api/running，之後每 15 秒一次 ──
let {R,st,DL,tick}=boot(); const I=n=>R.ITEMS.find(i=>i.n===n);
ok(runReqs().length===1,"載入時呼叫 GET api/running");
ok(!REQ.some(x=>/^data\//.test(x.u)),"沒有等待中的項目 → 載入時不重抓資料");
const E=I(101), open=R.kidsOf(E).filter(k=>R.isOpenAt(k,R.NOW)), A=open[0], B=open[1];
ok(!!A&&!!B,"示範 EPIC #101 底下有未完成的子 issue：#"+(A&&A.n)+"、#"+(B&&B.n));
const repo=u=>u.match(/github\.com\/([^/]+\/[^/]+)\/issues/)[1];
const item=(k,engine,min)=>({repo:repo(k.u),number:k.n,title:k.t,engine,since:ago(min),epic:101});
reply(lastRun(),200,{at:new Date().toISOString(),items:[item(A,"codex",12),item(B,"claude",3)]});
ok(timers().length===1,"回應後排下一次（15 秒）");
fire(); ok(runReqs().length===2,"15 秒到 → 再呼叫一次 api/running");

// ── 2 工作中的星：🐝 繞行＋「引擎 · 已 N 分」 ──
reply(lastRun(),200,{items:[item(A,"codex",12),item(B,"claude",3)]});
ok(!!R.RUN[A.u]&&!!R.RUN[B.u]&&Object.keys(R.RUN).length===2,"RUN 記下 2 張工作中的 issue");
R.openNeb(E); TEXT=[]; tick(3);
ok(TEXT.filter(t=>t.s==="🐝").length>=2*3,"EPIC 星雲：兩張工作中的子 issue 每一格都畫了 🐝");
ok(TEXT.some(t=>t.s==="codex · 已 12 分")&&TEXT.some(t=>t.s==="claude · 已 3 分"),"標籤：「codex · 已 12 分」「claude · 已 3 分」");
ok(TEXT.filter(t=>t.s==="🐝").length===(2+1)*3,"沒在跑的子 issue 沒有 🐝（每格 2 張子 issue＋主星系 EPIC #101 本身＝3 隻）");
ok(TEXT.some(t=>t.s==="🐝 2 隻工作中"),"主星系 EPIC #101 的標籤寫「🐝 2 隻工作中」");

// ── 3 EPIC 詳情標題列「🐝 N 隻工作中」；0 隻不顯示 ──
ok(/🐝 2 隻工作中/.test(st.nebH.innerHTML),"#101 標題列顯示「🐝 2 隻工作中」");
R.openNeb(I(103)); ok(!/隻工作中/.test(st.nebH.innerHTML),"#103 底下沒有在跑的：標題列不顯示");
R.openNeb(E); fire(); reply(lastRun(),200,{items:[item(B,"claude",4)]});
ok(/🐝 1 隻工作中/.test(st.nebH.innerHTML),"下一次資料只剩 1 張 → 標題列即時改成「🐝 1 隻工作中」");

// ── 4 從工作中變成已完成：重抓資料確認 closed → 沿軌道滑進最內圈（約 1 秒） ──
const dataReq=REQ.filter(x=>/^data\/demo-epic\.js/.test(x.u)).slice(-1)[0];
ok(!!R.PEND[A.u]&&!!dataReq,"#"+A.n+" 離開 running → 重抓 project 資料確認狀態");
const closedSrc=demoSrc.replace(new RegExp('("u": "'+A.u.replace(/[/.]/g,"\\$&")+'"[^}]*?"closed": )null'),'$1"2026-10-18T16:00:00Z"');
ok(closedSrc!==demoSrc,"（測試資料：把 #"+A.n+" 改成 closed）");
tick(1); const from={x:A.nx,y:A.ny}; reply(dataReq,200,closedSrc);
ok(!R.isOpenAt(A,R.NOW)&&!!R.FIN[A.u],"資料顯示 closed → #"+A.n+" 改成完成、開始滑動");
tick(12); const mid={x:A.nx,y:A.ny}, rr=p=>Math.hypot((p.x-R.NG.cx)/R.NG.sx,p.y-R.NG.cy);
tick(20); const end={x:A.nx,y:A.ny};
ok(rr(from)>rr(mid)&&rr(mid)>rr(end)+1,"半徑一路往內縮："+rr(from).toFixed(0)+" → "+rr(mid).toFixed(0)+" → "+rr(end).toFixed(0));
ok(!R.FIN[A.u]&&A.ncol==="done"&&Math.abs(rr(end)-R.NG.Rd)<1,"約 1 秒後停在最內圈（完成），動畫結束");
// 沒關的（例如 swarm:stuck）不滑
fire(); reply(lastRun(),200,{items:[]}); const dr2=REQ.filter(x=>/^data\/demo-epic\.js/.test(x.u)).slice(-1)[0];
reply(dr2,200,demoSrc); ok(R.isOpenAt(B,R.NOW)&&!R.FIN[B.u],"#"+B.n+" 離開 running 但資料沒 closed → 不動");
ok(Object.keys(R.RUN).length===0&&!/隻工作中/.test(st.nebH.innerHTML),"0 隻：標題列不顯示");
TEXT=[]; tick(3); ok(!TEXT.some(t=>t.s==="🐝"),"0 隻：畫面上沒有 🐝");

// ── 5 頁面隱藏時暫停，回來馬上補一次 ──
document.hidden=true; DL.visibilitychange(); let n0=runReqs().length;
ok(timers().length===0,"頁面隱藏 → 取消排程");
document.hidden=false; DL.visibilitychange(); ok(runReqs().length===n0+1,"回到頁面 → 立刻呼叫一次");
document.hidden=true; reply(lastRun(),200,{items:[]}); ok(timers().length===0,"回應時頁面又隱藏了 → 不排下一次");

// ── 6 prefers-reduced-motion：不繞行、不滑動，只顯示標示 ──
const beeXs=()=>{ TEXT=[]; tick(1); return TEXT.filter(t=>t.s==="🐝").map(t=>t.x.toFixed(2)).join(); };
({R,st,DL,tick}=boot());
reply(lastRun(),200,{items:[item(A,"codex",12)]}); R.openNeb(E);
const m1=beeXs(); tick(10); ok(m1!==beeXs(),"一般情況：🐝 會繞行（位置隨時間變）");
({R,st,DL,tick}=boot({reduce:true}));
reply(lastRun(),200,{items:[item(A,"codex",12),item(B,"claude",3)]}); R.openNeb(E);
const r1=beeXs(); tick(10); const r2=beeXs();
ok(r1&&r1===r2,"reduced-motion：🐝 標示還在，但不繞行（位置不變）");
ok(TEXT.some(t=>t.s==="codex · 已 12 分")&&/🐝 2 隻工作中/.test(st.nebH.innerHTML),"reduced-motion：標籤與標題列照常顯示");
fire(); reply(lastRun(),200,{items:[item(B,"claude",3)]});
reply(REQ.filter(x=>/^data\/demo-epic\.js/.test(x.u)).slice(-1)[0],200,closedSrc);
ok(!R.isOpenAt(A,R.NOW)&&!R.FIN[A.u],"reduced-motion：完成時直接到最內圈，不做滑動");

// ── 7 既有功能：選取、放行、關閉面板不受影響 ──
({R,st,DL,tick}=boot()); reply(lastRun(),200,{items:[item(A,"codex",12)]});
R.openNeb(E); tick(2); ok(R.NG&&/🐝 1 隻工作中/.test(st.nebH.innerHTML)&&/放行/.test(st.nebH.innerHTML),"標題列同時保留 🚦 放行");
R.closeNeb(); TEXT=[]; tick(2); ok(st.neb.style.display==="none"&&TEXT.filter(t=>t.s==="🐝").length===2,"關閉面板後照常繪製（主星系 EPIC 仍有 🐝）");
// ── 8 等待關閉的清單存進 localStorage：重新整理後仍會滑進最內圈 ──
const mkLS=store=>({getItem:k=>k in store?store[k]:null,setItem:(k,v)=>{store[k]=String(v);},removeItem:k=>{delete store[k];}});
const PK="wolffia.pend.demo", RK="wolffia.running.demo", J=(store,k)=>JSON.parse(store[k]||"{}");
const dataReqs=()=>REQ.filter(x=>/^data\/demo-epic\.js/.test(x.u)), lastData=()=>dataReqs().slice(-1)[0];
let LS={}; ({R,st,DL,tick}=boot({ls:mkLS(LS)}));
reply(lastRun(),200,{items:[item(A,"codex",12),item(B,"claude",3)]});
ok(!!J(LS,RK)[A.u]&&!!J(LS,RK)[B.u],"每次看到的 running 清單寫進 localStorage（"+RK+"）");
fire(); reply(lastRun(),200,{items:[item(B,"claude",4)]});
ok(!!J(LS,PK)[A.u]&&!J(LS,PK)[B.u],"等待清單寫進 localStorage（"+PK+"）：#"+A.n);
ok(!J(LS,RK)[A.u]&&!!J(LS,RK)[B.u],"running 清單跟著更新（只剩 #"+B.n+"）");
// 資料回來之前就重新整理
({R,st,DL,tick}=boot({ls:mkLS(LS)}));
ok(!!R.PEND[A.u],"重新載入後讀回等待清單：#"+A.n);
ok(!!R.PEND[B.u],"上次還在 running 的 #"+B.n+" 也先放進等待清單");
ok(runReqs().length===1&&dataReqs().length===1,"讀回後有等待中的 → 立刻呼叫 api/running 並重抓資料（不等 15 秒）");
const d0=lastData(); reply(lastRun(),200,{items:[item(B,"claude",5)]});
ok(!R.PEND[B.u]&&!J(LS,PK)[B.u],"api/running 說 #"+B.n+" 還在跑 → 移出等待清單");
R.openNeb(E); tick(1); reply(d0,200,closedSrc);
ok(!R.isOpenAt(A,R.NOW)&&!!R.FIN[A.u],"讀回後資料已關閉 → #"+A.n+" 改成完成、觸發滑進最內圈");
ok(!J(LS,PK)[A.u],"滑動後從 localStorage 的等待清單移除");
tick(40); ok(!R.FIN[A.u]&&A.ncol==="done"&&Math.abs(Math.hypot((A.nx-R.NG.cx)/R.NG.sx,A.ny-R.NG.cy)-R.NG.Rd)<1,"滑完停在最內圈");
// 上次在 running、關掉頁面時還沒離開：這次不在且資料已關閉 → 也滑
LS={}; LS[RK]=JSON.stringify({[A.u]:Date.now()-60e3});
({R,st,DL,tick}=boot({ls:mkLS(LS)}));
ok(!!R.PEND[A.u]&&dataReqs().length===1,"上次在 running 的 #"+A.n+" → 載入時列入等待、立刻重抓資料");
reply(lastRun(),200,{items:[]}); tick(1); reply(dataReqs()[0],200,closedSrc);
ok(!R.isOpenAt(A,R.NOW)&&!!R.FIN[A.u],"這次已不在 running 且資料已關閉 → 觸發滑進最內圈");
// 沒關的不動
LS={}; LS[PK]=JSON.stringify({[B.u]:Date.now()-60e3});
({R,st,DL,tick}=boot({ls:mkLS(LS)})); reply(lastRun(),200,{items:[]}); reply(dataReqs()[0],200,demoSrc);
ok(R.isOpenAt(B,R.NOW)&&!R.FIN[B.u]&&!!R.PEND[B.u],"讀回後資料沒 closed → 不動，繼續等");
// 超過 30 分鐘丟掉
LS={}; LS[PK]=JSON.stringify({[A.u]:Date.now()-31*60e3,[B.u]:Date.now()-5*60e3}); LS[RK]=JSON.stringify({[E.u]:Date.now()-40*60e3});
({R,st,DL,tick}=boot({ls:mkLS(LS)}));
ok(!R.PEND[A.u]&&!!R.PEND[B.u]&&!R.PEND[E.u],"超過 30 分鐘的項目載入時丟掉（#"+A.n+"、running 清單裡的 #"+E.n+"），5 分鐘的 #"+B.n+" 留著");
ok(!J(LS,PK)[A.u]&&!!J(LS,PK)[B.u],"丟掉的也從 localStorage 清掉");
LS={}; LS[PK]=JSON.stringify({[A.u]:Date.now()-31*60e3});
({R,st,DL,tick}=boot({ls:mkLS(LS)}));
ok(Object.keys(R.PEND).length===0&&dataReqs().length===0,"全部過期 → 不重抓資料");
// 壞掉的內容
LS={}; LS[PK]="{oops"; LS[RK]="5";
let threw=null; try{ ({R,st,DL,tick}=boot({ls:mkLS(LS)})); }catch(e){ threw=e; }
ok(!threw&&Object.keys(R.PEND).length===0,"localStorage 內容壞掉 → 當作空的，不出錯");
// localStorage 拋錯／不存在（隱私模式）：退回原本行為
const boom={getItem(){throw new Error("SecurityError");},setItem(){throw new Error("QuotaExceededError");}};
for(const [name,ls] of [["拋錯",boom],["不存在",null]]){
  threw=null; try{
    ({R,st,DL,tick}=boot({ls}));
    reply(lastRun(),200,{items:[item(A,"codex",12)]}); fire(); reply(lastRun(),200,{items:[]});
    tick(1); reply(lastData(),200,closedSrc); var slid=!!R.FIN[A.u]; tick(40);
  }catch(e){ threw=e; }
  ok(!threw,"localStorage "+name+"時不出錯"+(threw?"："+threw.message:""));
  ok(!threw&&slid&&!R.isOpenAt(A,R.NOW)&&!R.FIN[A.u],"localStorage "+name+"時照原本行為：離開 running＋資料 closed → 滑進最內圈");
}
// ── 9 新出現的工作中 issue：還不在「進行中」→ 沿軌道移過去，並立刻重抓資料（30 秒內只一次） ──
const DOING="進行中 (7 天一輪)", REVIEW="審核後請關閉", hasLab=(it,l)=>it.labels.indexOf(l)>=0;
const rM=it=>Math.hypot((it.px-R.CX)/R.SX,it.py-R.CY);
({R,st,DL,tick}=boot());
let q0=dataReqs().length, r0=rM(I(41));
ok(I(41).st.col==="待辦"&&I(42).st.col===DOING&&Math.abs(r0-R.RING["待辦"])<1,"（示範資料：#41 在待辦、#42 在進行中）");
reply(lastRun(),200,{items:[item(I(41),"codex",1),item(I(42),"claude",1)]});
ok(I(41).st.col===DOING&&!!R.FIN[I(41).u],"新出現的工作中 issue #41 會移到「進行中」，開始沿軌道滑動");
ok(I(42).st.col===DOING&&!R.FIN[I(42).u],"已在「進行中」的 #42 不動");
ok(dataReqs().length===q0+1,"移過去後立刻重抓一次資料");
tick(12); const m41=rM(I(41)); tick(20); const e41=rM(I(41));
ok(r0>m41&&m41>e41&&Math.abs(e41-R.RING[DOING])<1&&!R.FIN[I(41).u],"#41 半徑 "+r0.toFixed(0)+" → "+m41.toFixed(0)+" → "+e41.toFixed(0)+"，約 1 秒後停在「進行中」那圈");
fire(); reply(lastRun(),200,{items:[item(I(41),"codex",1),item(I(42),"claude",1),item(I(44),"codex",0)]});
ok(!R.FIN[I(41).u]&&I(44).st.col===DOING&&!!R.FIN[I(44).u],"還在跑的 #41 不再動；又新出現的 #44 移到「進行中」");
ok(dataReqs().length===q0+1,"30 秒內不重複重抓（15 秒後又有新的 → 不再抓）");
const realNow=Date.now; Date.now=()=>realNow()+31e3;
fire(); reply(lastRun(),200,{items:[item(I(41),"codex",1),item(I(46),"claude",0)]});
Date.now=realNow;
ok(I(46).st.col===DOING&&dataReqs().length===q0+2,"過了 30 秒再出現新的 #46 → 移過去並再重抓一次");
({R,st,DL,tick}=boot({reduce:true})); q0=dataReqs().length;
reply(lastRun(),200,{items:[item(I(41),"codex",1)]});
ok(I(41).st.col===DOING&&!R.FIN[I(41).u]&&dataReqs().length===q0+1,"reduced-motion：新的工作中 issue 直接移到「進行中」，不滑動");
// 子 issue 也會移（EPIC 星雲裡）
({R,st,DL,tick}=boot()); R.openNeb(I(103)); tick(2); const k131=I(131), nr0=Math.hypot((k131.nx-R.NG.cx)/R.NG.sx,k131.ny-R.NG.cy);
reply(lastRun(),200,{items:[item(k131,"codex",1)]}); tick(12); const nrm=Math.hypot((k131.nx-R.NG.cx)/R.NG.sx,k131.ny-R.NG.cy); tick(20);
const nre=Math.hypot((k131.nx-R.NG.cx)/R.NG.sx,k131.ny-R.NG.cy);
ok(k131.st.col===DOING&&nr0>nrm&&nrm>nre&&Math.abs(nre-R.NG.R[DOING])<1,"星雲裡的子 issue #131 沿軌道滑到「進行中」那圈："+nr0.toFixed(0)+" → "+nrm.toFixed(0)+" → "+nre.toFixed(0));

// ── 10 EPIC：蜂消失後重抓資料，sub-issue 全部已關閉 ──
const PRE="window.PROJECTS=window.PROJECTS||{};window.PROJECTS.demo=";
const edit=fn=>{ const p=JSON.parse(demoSrc.slice(demoSrc.indexOf("PROJECTS.demo=")+14).trim().replace(/;$/,"")); p.items.forEach(fn); return PRE+JSON.stringify(p)+";"; };
const allClosed=(status,labels)=>edit(x=>{ if(x.n>=131&&x.n<=133){ x.closed=new Date().toISOString(); x.status="完成"; x.state="CLOSED"; }
  if(x.n===103){ x.status=status; if(labels) x.labels=labels; } });
const epT=()=>TM.filter(t=>t.ms===30000&&!t.cleared);
const beeGone=o=>{ ({R,st,DL,tick}=boot(o)); reply(lastRun(),200,{items:[item(I(131),"codex",5)]}); fire(); reply(lastRun(),200,{items:[]}); };
beeGone(); let er0=rM(I(103));
ok(!!R.PEND[I(131).u]&&Math.abs(er0-R.RING["待辦"])<1,"（EPIC #103 在待辦；它底下的 #131 蜂消失 → 重抓資料）");
reply(lastData(),200,allClosed(REVIEW));
ok(I(103).st.col===REVIEW&&!!R.FIN[I(103).u],"EPIC：sub-issue 全部已關閉、EPIC 在資料裡已是「審核後請關閉」→ 移過去，開始沿軌道滑動");
tick(12); const em=rM(I(103)); tick(20); const ee=rM(I(103));
ok(er0>em&&em>ee&&Math.abs(ee-R.RING[REVIEW])<1&&!R.FIN[I(103).u],"EPIC #103 半徑 "+er0.toFixed(0)+" → "+em.toFixed(0)+" → "+ee.toFixed(0)+"，停在「審核後請關閉」那圈");
ok(epT().length===0,"EPIC 已移過去 → 不再排重抓");
beeGone(); reply(lastData(),200,allClosed("待辦",["epic","epic:review"]));
ok(I(103).st.col===REVIEW&&I(103).labels.indexOf("epic:review")>=0,"EPIC 帶 epic:review（狀態欄還沒改）→ 也移到「審核後請關閉」");
beeGone({reduce:true}); reply(lastData(),200,allClosed(REVIEW));
ok(I(103).st.col===REVIEW&&!R.FIN[I(103).u],"EPIC：reduced-motion 時直接移到「審核後請關閉」，不滑動");
// 還沒改 → 30 秒後再重抓，最多 4 次
beeGone(); reply(lastData(),200,allClosed("待辦"));
ok(I(103).st.col==="待辦"&&!R.FIN[I(103).u]&&epT().length===1,"EPIC 還沒改成「審核後請關閉」→ 先不動，30 秒後再重抓");
let tries=0;
while(epT().length&&tries<10){ const t=epT()[0], n=dataReqs().length; t.cleared=true; t.f(); if(dataReqs().length!==n+1) break; tries++; reply(lastData(),200,allClosed("待辦")); }
ok(tries===4&&epT().length===0&&I(103).st.col==="待辦","EPIC 一直沒改：30 秒重抓一次，最多重試 4 次（實際 "+tries+" 次）後停止");
beeGone(); reply(lastData(),200,allClosed("待辦"));
for(let k=0;k<2;k++){ const t=epT()[0]; t.cleared=true; t.f(); reply(lastData(),200,allClosed(k?REVIEW:"待辦")); }
ok(I(103).st.col===REVIEW&&!!R.FIN[I(103).u]&&epT().length===0,"EPIC 第 2 次重抓時已改成「審核後請關閉」→ 移過去，不再重抓");
beeGone(); reply(lastData(),200,demoSrc);
ok(I(103).st.col==="待辦"&&!R.FIN[I(103).u]&&epT().length===0,"EPIC 底下還有沒關的 sub-issue → 不動、不排重抓");
// #490 重現（2026-10-19）：EPIC、最後一張 sub 都在待辦 → running 出現 sub → running 消失 → 第一次重抓拿到舊資料 → 第二次重抓才是新資料
const F0r=Date.parse(demoSrc.match(/"fetched": "([^"]+)"/)[1]), isoR=ms=>new Date(ms).toISOString().replace(/\.\d+Z$/,"Z");
const at490=(src,ms)=>src.replace(/"fetched":\s*"[^"]+"/,'"fetched":"'+isoR(ms)+'"');
const rep490=(o,second)=>{ ({R,st,DL,tick}=boot(o)); const ep=I(103), k=I(131); R.openNeb(ep); tick(2);
  reply(lastRun(),200,{items:[item(k,"codex",1)]}); reply(lastData(),200,demoSrc);          // 開工：sub 移到進行中、重抓（舊資料）
  fire(); reply(lastRun(),200,{items:[]}); reply(lastData(),200,demoSrc);                     // running 消失 → 第一次重抓：舊資料
  const mid=ep.st.col;
  fire(); reply(lastRun(),200,{items:[],data_at:{demo:isoR(F0r+60e3)}}); reply(lastData(),200,at490(second,F0r+60e3));   // 第二次重抓：新資料
  return {ep,k,mid}; };
let P=rep490(undefined,allClosed(REVIEW,["epic","epic:review"]));
ok(P.mid==="待辦"&&P.ep.st.col===REVIEW&&!!R.FIN[P.ep.u]&&!R.isOpenAt(P.k,R.NOW),"#490 順序：第一次重抓舊資料、第二次新資料（sub 已關、EPIC 已審核）→ EPIC 移到「審核後請關閉」、開始沿軌道滑動");
tick(40); ok(Math.abs(rM(P.ep)-R.RING[REVIEW])<1&&!R.FIN[P.ep.u],"#490 順序：EPIC 停在「審核後請關閉」那圈");
// 實際原因：後端在 sub 關閉、running 消失時立刻重抓，那份資料 sub 已關、EPIC 還沒加 epic:review（orchestrator 晚十幾秒才改）。
// 之後 2 分鐘的重試都拿到同一份（後端要等下一輪 cron 才再抓）→ 重試用完；下一份資料來了 EPIC 也要移過去
const notYet=allClosed("待辦",["epic"]);
const rep490b=o=>{ P=rep490(o,notYet); const sl=!R.isOpenAt(P.k,R.NOW), stay=P.ep.st.col;
  let n=0; while(epT().length&&n<10){ const t=epT()[0]; t.cleared=true; t.f(); reply(lastData(),200,at490(notYet,F0r+60e3)); n++; }
  const gaveUp=P.ep.st.col==="待辦"&&epT().length===0;
  fire(); reply(lastRun(),200,{items:[],data_at:{demo:isoR(F0r+600e3)}}); reply(lastData(),200,at490(allClosed(REVIEW,["epic","epic:review"]),F0r+600e3));
  return {sl,stay,n,gaveUp}; };
let Q=rep490b();
ok(Q.sl&&Q.stay==="待辦"&&Q.n===4&&Q.gaveUp,"#490 實況：第二次重抓 sub 已關、EPIC 還沒改 → sub 滑進完成，EPIC 重試 "+Q.n+" 次都還沒改");
ok(P.ep.st.col===REVIEW&&!!R.FIN[P.ep.u]&&hasLab(P.ep,"epic:review"),"#490 實況：重試用完後下一份資料（data_at）已是審核 → EPIC 移到「審核後請關閉」（實際 "+P.ep.st.col+"）");
tick(40); ok(Math.abs(rM(P.ep)-R.RING[REVIEW])<1&&!R.FIN[P.ep.u],"#490 實況：EPIC 沿軌道停在「審核後請關閉」那圈");
Q=rep490b({reduce:true});
ok(P.ep.st.col===REVIEW&&!R.FIN[P.ep.u],"#490 實況（reduced-motion）：EPIC 直接移到「審核後請關閉」，不滑動");
// 剛拖曳改過（資料還沒追上）的 EPIC 以拖曳為準，不被舊資料拉回「審核後請關閉」
{ const store={}; ({R,st,DL,tick}=boot({ls:mkLS(store)})); const ep=I(103);
  store["wolffia.moves.demo"]=JSON.stringify({[ep.u]:{s:"待辦",t:Math.max(Date.now(),F0r+120e3)}});   // 載入後才拖曳（setStatus 存的）
  reply(lastRun(),200,{items:[],data_at:{demo:isoR(F0r+60e3)}}); reply(lastData(),200,at490(allClosed(REVIEW,["epic","epic:review"]),F0r+60e3));
  ok(ep.st.col==="待辦"&&!R.FIN[ep.u],"剛拖曳到待辦、資料比拖曳舊 → 不拉回「審核後請關閉」"); }
// 沒有蜂消失、sub 還沒全關：資料裡 EPIC 帶 epic:review 也不動
({R,st,DL,tick}=boot()); reply(lastRun(),200,{items:[],data_at:{demo:isoR(F0r+60e3)}});
reply(lastData(),200,at490(edit(x=>{ if(x.n===103){ x.status=REVIEW; x.labels=["epic","epic:review"]; } }),F0r+60e3));
ok(I(103).st.col==="待辦"&&!R.FIN[I(103).u],"EPIC 底下還有沒關的 sub-issue → 資料帶 epic:review 也不動");

// ── 11 後端重抓過（data_at 比頁面資料新）→ 頁面自己重抓；資料裡已關閉的 EPIC 沿軌道移進「完成」 ──
const F0=Date.parse(demoSrc.match(/"fetched": "([^"]+)"/)[1]), iso=ms=>new Date(ms).toISOString().replace(/\.\d+Z$/,"Z");
({R,st,DL,tick}=boot()); q0=dataReqs().length;
reply(lastRun(),200,{items:[],data_at:{demo:iso(F0)}});
ok(dataReqs().length===q0,"data_at 和頁面資料一樣新 → 不重抓");
fire(); reply(lastRun(),200,{items:[],data_at:{demo:iso(F0-60e3)}});
ok(dataReqs().length===q0,"data_at 比頁面資料舊 → 不重抓");
fire(); reply(lastRun(),200,{items:[],data_at:{"7":iso(F0+600e3)}});
ok(dataReqs().length===q0,"data_at 只有別的 project 較新 → 不重抓");
fire(); reply(lastRun(),200,{items:[]});
ok(dataReqs().length===q0,"回應沒有 data_at（舊後端）→ 不重抓、不出錯");
fire(); reply(lastRun(),200,{items:[],data_at:{demo:iso(F0+60e3)}});
ok(dataReqs().length===q0+1,"data_at 比頁面資料的 fetched 晚 → 呼叫一次 refreshData()");
fire(); reply(lastRun(),200,{items:[],data_at:{demo:iso(F0+60e3)}});
ok(dataReqs().length===q0+1,"同一份 data_at 再來一次 → 不重複重抓");
fire(); reply(lastRun(),200,{items:[],data_at:{demo:iso(F0+120e3)}});
ok(dataReqs().length===q0+2,"之後後端又重抓（data_at 更新）→ 再重抓一次");
// 資料裡 EPIC 已關閉（在 GitHub 上結案），畫面上還沒
const epicClosed=(n,f)=>edit(x=>{ if(x.n===n){ x.closed=new Date().toISOString(); x.status="完成"; x.state="CLOSED"; } }).replace(/"fetched":"[^"]+"/,'"fetched":"'+iso(f)+'"');
({R,st,DL,tick}=boot()); let ep=I(103), ep0=rM(ep);
ok(ep.epic&&R.isOpenAt(ep,R.NOW)&&!R.PEND[ep.u],"（EPIC #103 在畫面上未完成、不在等待清單）");
reply(lastRun(),200,{items:[],data_at:{demo:iso(F0+60e3)}}); reply(lastData(),200,epicClosed(103,F0+60e3));
ok(!R.isOpenAt(ep,R.NOW)&&!!R.FIN[ep.u],"資料裡已關閉的 EPIC #103 → 改成完成、開始沿軌道滑動");
tick(12); const epm=rM(ep); tick(20); const epe=rM(ep);
ok(ep0>epm&&epm>epe&&epe<R.RING["待辦"]&&!R.FIN[ep.u],"EPIC #103 半徑 "+ep0.toFixed(0)+" → "+epm.toFixed(0)+" → "+epe.toFixed(0)+"，約 1 秒後停在星核（完成）");
fire(); reply(lastRun(),200,{items:[],data_at:{demo:iso(F0+60e3)}});
ok(dataReqs().length===1,"重抓回來的資料 fetched 已追上 data_at → 不再重抓");
({R,st,DL,tick}=boot({reduce:true})); ep=I(103);
reply(lastRun(),200,{items:[],data_at:{demo:iso(F0+60e3)}}); reply(lastData(),200,epicClosed(103,F0+60e3));
ok(!R.isOpenAt(ep,R.NOW)&&!R.FIN[ep.u],"reduced-motion：資料裡已關閉的 EPIC 直接移進完成，不滑動");
// 非 EPIC、不在等待清單的不受影響（沿用原本：只有離開 running 的才確認）
({R,st,DL,tick}=boot()); const k41=I(41);
reply(lastRun(),200,{items:[],data_at:{demo:iso(F0+60e3)}}); reply(lastData(),200,epicClosed(41,F0+60e3));
ok(!k41.epic&&R.isOpenAt(k41,R.NOW)&&!R.FIN[k41.u],"不是 EPIC、也不在等待清單的 #41 → 不動");
console.log(bad?"bad "+bad:"全部通過"); process.exit(bad?1:0);
