// 看板認得 GitLab 專案（key "gitlab:<path>"）：?p=gitlab:acme/general 載入 data/g-<sha1(path) 前 12 碼>.js 並選到它，
// 卡片連到 GitLab、拖曳改狀態送出 GitLab key、「📊 分析」送出 GitLab key、GitLab 的卡沒有放行／重新放行／自動合併，token 不在頁面上。
// 資料是真的跑 fetch-project.py --one 產生的：這支測試起一個假的 GitLab REST API（資料在 _fixtures/gitlab/，和 test_gitlab_fetch.py 同一份）。
// 頁面和 v8multiorg 同方式：node 假 DOM＋假的 XHR 跑 v8.html。
const fs=require("fs"),os=require("os"),path=require("path"),http=require("http"),crypto=require("crypto"),cp=require("child_process"),dir=__dirname+"/../";
const h=fs.readFileSync(dir+"v8.html","utf8"); const re=/<script>([\s\S]*?)<\/script>/g; let m; const inl=[]; while((m=re.exec(h))) inl.push(m[1]);
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};
const FIX=__dirname+"/_fixtures/gitlab/", TOKEN="glpat-SECRET-do-not-leak-0123456789", KEY="gitlab:acme/general";
const BASE="g-"+crypto.createHash("sha1").update("acme/general").digest("hex").slice(0,12);
const fix=n=>JSON.parse(fs.readFileSync(FIX+n,"utf8"));

// ── 假的 GitLab（只認 PRIVATE-TOKEN 標頭），跑 fetch-project.py --one gitlab:acme/general 產生資料 ──
function fakeGitLab(){ return http.createServer((q,r)=>{
  const send=(code,body,next)=>{ r.writeHead(code,{"Content-Type":"application/json","X-Next-Page":next||""}); r.end(JSON.stringify(body)); };
  if(q.headers["private-token"]!==TOKEN) return send(401,{message:"401 Unauthorized"});
  const u=new URL(q.url,"http://x"), page=u.searchParams.get("page")||"1", st=u.searchParams.get("state");
  if(u.pathname==="/api/v4/projects/acme%2Fgeneral") return send(200,fix("project.json"));
  if(u.pathname==="/api/v4/projects/42/issues"){ const n="issues-"+st+"-"+page+".json";
    if(!fs.existsSync(FIX+n)) return send(200,[]);
    return send(200,fix(n),fs.existsSync(FIX+"issues-"+st+"-"+(+page+1)+".json")?String(+page+1):""); }
  const p=u.pathname.split("/"), kind={resource_label_events:"label-events.json",resource_state_events:"state-events.json"}[p[7]];
  if(p.length===8&&p[5]==="issues"&&kind) return send(200,page==="1"?(fix(kind)[p[6]]||[]):[]);
  send(404,{message:"404 Not Found"}); }); }

(async function(){
const D=fs.mkdtempSync(path.join(os.tmpdir(),"wolffia-v8gitlab-"))+"/";
const srv=fakeGitLab(); await new Promise(r=>srv.listen(0,"127.0.0.1",r));
fs.writeFileSync(D+"projects.json",JSON.stringify({default:2,projects:[2,KEY]}));
const env=Object.assign({},process.env,{WOLFFIA_ORG:"acme",WOLFFIA_PROJECTS:D+"projects.json",GITLAB_URL:"http://127.0.0.1:"+srv.address().port,GITLAB_TOKEN:TOKEN});
const code=await new Promise(r=>{ const c=cp.spawn("python3",["-B",dir+"fetch-project.py","--one",KEY,D],{env,stdio:["ignore","ignore","pipe"]});
  let err=""; c.stderr.on("data",d=>err+=d); c.on("close",x=>{ if(x) console.log("# fetch-project.py: "+err); r(x); }); });
srv.close();
ok(code===0&&fs.existsSync(D+BASE+".js"),"fetch-project.py 用假 GitLab 產生 data/"+BASE+".js");
if(code!==0){ console.log("bad 1"); process.exit(1); }

// 補一些 label：GitLab 也可能有 label epic 的卡（甚至帶著 swarm:go／swarm:auto-merge），頁面仍不能給 GitHub EPIC 的按鈕
const gl=D+BASE+".js", G={}; new Function("window",fs.readFileSync(gl,"utf8"))(G);
const GP=G.PROJECTS[KEY];
const I1=GP.items.find(i=>i.n===1), I3=GP.items.find(i=>i.n===3), I4=GP.items.find(i=>i.n===4);
I1.labels=["epic","swarm:go","swarm:auto-merge"]; I1.goAt="2026-10-01T00:00:00Z";
I3.labels=["epic"];
I4.parent=I1.u; I4.created="2026-10-25T00:00:00Z";   // 放行後新開的 sub-issue：GitHub 的卡這時會有「↻ 重新放行」
fs.writeFileSync(gl,"window.PROJECTS=window.PROJECTS||{};window.PROJECTS["+JSON.stringify(KEY)+"]="+JSON.stringify(GP)+";\n");
// acme 2（GitHub）：同一頁要能切回來
const gh={num:2,owner:"acme",key:2,cols:GP.cols,title:"TI 二號",url:"https://github.com/orgs/acme/projects/2",fetched:"2026-10-20T00:00:00Z",
  items:[1,2,3].map(i=>({n:200+i,t:"gh #"+i,u:"https://github.com/acme/r/issues/"+(200+i),repo:"r",owner:"acme",state:"OPEN",created:"2026-10-0"+i+"T00:00:00Z",
    closed:null,status:"待辦",since:"2026-10-0"+i+"T00:00:00Z",hist:[],who:[],labels:i===1?["epic","swarm:go"]:[],type:null,parent:null,blockedBy:[],target:null,goAt:i===1?"2026-10-01T00:00:00Z":undefined}))};
gh.items[1].parent=gh.items[0].u; gh.items[1].created="2026-10-25T00:00:00Z";
fs.writeFileSync(D+"p2.js","window.PROJECTS=window.PROJECTS||{};window.PROJECTS[2]="+JSON.stringify(gh)+";\n");
let IXS=fs.readFileSync(D+"index.js","utf8");
if(!/"key": ?2\b/.test(IXS)){ const ix=JSON.parse(IXS.split("=",2)[1].replace(/;\s*$/,"")); ix.projects.unshift({num:2,owner:"acme",key:2,title:"TI 二號",open:3,total:3}); IXS="window.PROJECT_INDEX="+JSON.stringify(ix)+";\n"; }
fs.writeFileSync(D+"index.js",IXS);

let SENT=[], REQ=[], WROTE=[], LS=[];
function boot(search){
  SENT=[]; REQ=[]; WROTE=[]; LS=[];
  const st={}; const ctx=new Proxy({},{get:(o,k)=>k==="createRadialGradient"?(()=>({addColorStop(){}})):()=>{}});
  const mk=id=>st[id]||(st[id]={id,style:{},classList:{add(){},remove(){},contains(){return false}},addEventListener(t,f){(this.L=this.L||{})[t]=f},getContext:()=>ctx,clientWidth:1200,clientHeight:720,
    getBoundingClientRect(){return{left:0,top:0,width:1200,height:720}},getAttribute(k){return this[k]},querySelector(){return{onclick:null}},querySelectorAll(){return []},appendChild(){},innerHTML:"",textContent:"",value:"0"});
  const QS={};
  const qs=sel=>{ const mm=sel.match(/^#(\w+) \.(\w+)$/); if(mm&&!new RegExp('class="'+mm[2]+'"').test(mk(mm[1]).innerHTML)) return null;
    return QS[sel]||(QS[sel]={onclick:null}); };
  const WL={}, WA={}; global.window={devicePixelRatio:1,open(){return {}},matchMedia:()=>({matches:false}),addEventListener(t,f){(WA[t]=WA[t]||[]).push(f); WL[t]=e=>WA[t].forEach(g=>g(e));}}; global.addEventListener=global.window.addEventListener;
  global.localStorage={getItem:()=>null,setItem(k,v){LS.push(k+"="+v);}}; global.setTimeout=()=>0; global.clearTimeout=()=>0; global.location={search,pathname:"/board/"};
  global.confirm=()=>true;
  global.XMLHttpRequest=function(){ this.open=(mm,u)=>{this.m=mm;this.u=u}; this.setRequestHeader=()=>{};
    this.send=b=>{ this.body=b; REQ.push(this);
      if(this.m==="POST"&&this.u==="api/status"){ SENT.push(JSON.parse(b)); this.status=200; this.responseText='{"ok":true,"by":"t"}'; this.onload&&this.onload(); }
      else if(this.u==="data/status.json"){ this.status=200; this.responseText='{"github":false}'; this.onload&&this.onload(); } }; };
  global.document={hidden:false,addEventListener(){},getElementById:mk,querySelector:qs,querySelectorAll:()=>[],createElement:()=>mk("x"+Math.random()),
    write(s){ const f=s.match(/data\/([\w-]+\.js)/)[1]; WROTE.push(f); if(fs.existsSync(D+f)) new Function(fs.readFileSync(D+f,"utf8"))(); }};
  let T=0,F=null; global.performance={now:()=>T}; global.requestAnimationFrame=f=>{F=f};
  new Function(fs.readFileSync(D+"index.js","utf8"))();
  inl.forEach((s,i)=>{ if(i===inl.length-1) s+=";global.__R={ITEMS,COLS,RING,CX,CY,SX,PJ,openNeb,closeNeb};"; new Function(s)(); });
  const tick=n=>{for(let k=0;k<n;k++){T+=40;F(T);}}; tick(40);
  return {R:global.__R,st,QS,WL,tick};
}
const ev=(x,y)=>({clientX:x,clientY:y,preventDefault(){}});
function dragTo(b,it,c){ const {R,st,WL,tick}=b, r=R.RING[c], at=rr=>[R.CX+Math.cos(it.ang)*rr*R.SX,R.CY+Math.sin(it.ang)*rr];
  SENT=[]; st.cv.L.mousedown(ev(it.px,it.py)); const r0=Math.hypot((it.px-R.CX)/R.SX,it.py-R.CY);
  for(let k=1;k<=8;k++){ const [x,y]=at(r0+(r-r0)*k/8); WL.mousemove(ev(x,y)); } WL.mouseup(); tick(10); return SENT[0]; }

// ── 1 ?p=gitlab:acme/general → 載入 g-<12 碼>.js、選到它 ──
for(const q of ["?p=gitlab:acme/general","?p=gitlab%3Aacme%2Fgeneral","?p=GitLab:Acme/General"]){
  const R=boot(q).R;
  ok(window.PROJECT_NUM===KEY&&R.PJ.key===KEY&&R.PJ.title==="Acme / general",q+" → 選到 "+KEY+"（"+R.PJ.title+"，PROJECT_NUM="+window.PROJECT_NUM+"）");
  ok(WROTE.join()===BASE+".js"&&/^g-[0-9a-f]{12}\.js$/.test(WROTE[0]),q+" 載入 data/"+WROTE.join());
}
// 不是合法 GitLab 路徑 → 預設 acme 2
for(const q of ["?p=gitlab:acme","?p=gitlab:../x","?p=gitlab:acme/other"]){
  const R=boot(q).R; ok(window.PROJECT_NUM===2&&WROTE.join()==="p2.js",q+" → 預設 acme 2（data/"+WROTE.join()+"）"); }

// ── 2 卡片連到 GitLab ──
let b=boot("?p="+KEY), R=b.R;
ok(R.ITEMS.length===7&&R.ITEMS.every(i=>/^https:\/\/gitlab\.example\.invalid\/acme\/general\/-\/issues\/\d+$/.test(i.u)),"卡片連結都是 GitLab 網址（"+R.ITEMS[0].u+"）");
ok(/href="https:\/\/gitlab\.example\.invalid\/acme\/general"/.test(b.st.proj.innerHTML),"project 連結是 GitLab 專案網址");
ok(/value="gitlab:acme\/general" selected/.test(b.st.pSel.innerHTML)&&/value="2">/.test(b.st.pSel.innerHTML)&&/（GitLab）/.test(b.st.pSel.innerHTML),"project 選單：GitLab 專案被選中、標（GitLab），也有 acme 2");
ok(!(b.st.noGh&&b.st.noGh.style.display==="block"),"GitLab 專案不顯示「尚未連接 GitHub」");

// ── 3 拖到「進行中」→ api/status 帶 GitLab key ──
{ const it=R.ITEMS.find(i=>i.vis&&!i.epic&&i.st&&i.st.col==="待辦"), s=dragTo(b,it,"進行中 (7 天一輪)");
  ok(!!s&&s.project===KEY&&s.url===it.u&&s.status==="進行中 (7 天一輪)","拖到「進行中」→ api/status "+JSON.stringify(s));
  ok(/寫入 GitLab|已寫入 GitLab/.test(b.st.toast.innerHTML),"提示寫的是 GitLab"); }

// ── 4 GitLab 的卡沒有放行／重新放行／自動合併 ──
{ const E1=R.ITEMS.find(i=>i.n===1), E3=R.ITEMS.find(i=>i.n===3);
  ok(E1.epic&&E3.epic,"label epic 的 GitLab 卡是 EPIC");
  R.openNeb(E1); const H1=b.st.nebH.innerHTML;
  ok(!/class="(go|rego|am|amk)"/.test(H1)&&!/放行|自動合併/.test(H1),"已帶 swarm:go＋auto-merge 的 GitLab EPIC：沒有重新放行、自動合併");
  R.closeNeb(); R.openNeb(E3); const H3=b.st.nebH.innerHTML;
  ok(!/class="go"/.test(H3)&&!/🚦/.test(H3)&&!b.QS["#nebH .go"],"未放行的 GitLab EPIC：沒有「🚦 放行」");
  R.closeNeb(); }
// 對照：GitHub 的 project 照舊有這些按鈕
{ const b2=boot("?p=2"), R2=b2.R, E=R2.ITEMS.find(i=>i.n===201);
  ok(window.PROJECT_NUM===2&&WROTE.join()==="p2.js"&&R2.PJ.title==="TI 二號","同一頁切回 ?p=2 → acme 2（data/"+WROTE.join()+"）");
  R2.openNeb(E); const H=b2.st.nebH.innerHTML;
  ok(/class="rego"/.test(H)&&/class="am"/.test(H)&&/🚦 已放行/.test(H),"GitHub EPIC 照舊有「🚦 已放行」「↻ 重新放行」「⚡ 自動合併」");
  const it=R2.ITEMS.find(i=>i.vis&&!i.epic&&i.st&&i.st.col==="待辦"), s=dragTo(b2,it,"進行中 (7 天一輪)");
  ok(!!s&&s.project===2,"GitHub project 拖曳 → project=2");
  ok(b2.st.noGh.style.display==="block","GitHub project 照舊提示「尚未連接 GitHub」"); }

// ── 5 「📊 分析」送出 GitLab key ──
b=boot("?p="+KEY); R=b.R;
b.st.chatIn.value="哪些卡卡住了？"; b.st.thinkScope.value="one"; b.st.thinkB.onclick();
{ const x=REQ.filter(x=>x.m==="POST"&&x.u==="api/think")[0], body=x&&JSON.parse(x.body);
  ok(!!body&&body.project===KEY,"📊 分析 → api/think project="+JSON.stringify(body&&body.project)); }

// ── 6 token 不在頁面任何地方 ──
{ const all=[h,fs.readFileSync(gl,"utf8"),fs.readFileSync(D+"index.js","utf8"),JSON.stringify(window.PROJECT),JSON.stringify(window.PROJECT_INDEX),
    ...Object.values(b.st).map(e=>String(e.innerHTML)+String(e.textContent)+String(e.value)),...REQ.map(x=>x.u+" "+(x.body||"")),...WROTE,...LS].join("\n");
  ok(all.length>1000&&all.indexOf(TOKEN)<0&&all.indexOf("glpat-")<0,"GitLab token 不在頁面、資料檔、送出的請求、localStorage 任何地方"); }

// ── 7 檔名：和 fetch-project.py 一樣的 sha1（長路徑、非 ASCII 也對） ──
{ const ps=["acme/general","a/b","deep/group/sub/"+"x".repeat(120),"中文/專案"];
  ok(ps.every(p=>window.pFile("gitlab:"+p)==="g-"+crypto.createHash("sha1").update(p).digest("hex").slice(0,12)),"pFile(gitlab:…) = g-<sha1(path) 前 12 碼>（含長路徑、UTF-8）"); }

fs.rmSync(D,{recursive:true,force:true});
console.log(bad?"bad "+bad:"全部通過");
process.exit(bad?1:0);
})();
