// GitLab 專案端到端（EPIC #42）：假的 GitLab 伺服器 → 外掛的抓資料（fetch-project.py）寫出 data/g-<12 碼>.js
// → 外掛的 /board/ 與 /board/data/ 提供頁面和資料 → 頁面（node 假 DOM 跑 v8.html）顯示四圈數量
// → 在頁面拖曳改狀態，XHR 經外掛的 /board/api/status 送到假 GitLab（換 label、關閉）→ 外掛重抓 → 頁面顯示新位置。
// 假 GitLab 會記住改過的 label／狀態、補上 label／state 事件，和真的一樣；只認 PRIVATE-TOKEN 標頭。
// 不連網、不用 gh；token 只放在 token 檔，最後檢查輸出檔、回應、紀錄裡都找不到它。
const fs=require("fs"),os=require("os"),path=require("path"),http=require("http"),crypto=require("crypto"),cp=require("child_process"),dir=__dirname+"/../";
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};
const TOKEN="glpat-E2E-SECRET-do-not-leak-9876543210", KEY="gitlab:acme/general";
const BASE="g-"+crypto.createHash("sha1").update("acme/general").digest("hex").slice(0,12);
const T0=s=>s.replace(/\.\d+Z$/,"Z");                       // GitLab 時間：到秒
const nowIso=()=>T0(new Date().toISOString());
const daysAgo=d=>T0(new Date(Date.now()-d*864e5).toISOString());

// ── 假的 GitLab：一個專案（id 42）、四張 issue，GET 清單／單張／事件，PUT 改 label 與狀態 ──
function fakeGitLab(){
  const S={base:"",issues:{},labelEv:{},stateEv:{},puts:[],unauth:0,reqs:[]};
  const issue=(iid,title,state,labels,created,closed)=>{ S.issues[iid]={id:1000+iid,iid,project_id:42,title,state,created_at:created,updated_at:closed||created,
    closed_at:closed||null,labels,assignees:[],due_date:null,get web_url(){ return S.base+"/acme/general/-/issues/"+iid; }};
    S.labelEv[iid]=[]; S.stateEv[iid]=[]; };
  issue(1,"待辦的卡","opened",["bug"],daysAgo(9));
  issue(2,"進行中的卡","opened",["進行中"],daysAgo(8)); S.labelEv[2].push({id:1,created_at:daysAgo(7),action:"add",label:{id:9,name:"進行中"}});
  issue(3,"審核中的卡","opened",["審核後請關閉"],daysAgo(8)); S.labelEv[3].push({id:2,created_at:daysAgo(6),action:"add",label:{id:10,name:"審核後請關閉"}});
  issue(4,"已關閉的卡","closed",[],daysAgo(9),daysAgo(5)); S.stateEv[4].push({id:3,created_at:daysAgo(5),state:"closed"});
  let evId=10;
  const srv=http.createServer((q,r)=>{
    let raw=""; q.on("data",d=>raw+=d); q.on("end",()=>{
      const send=(code,body,next)=>{ r.writeHead(code,{"Content-Type":"application/json","X-Next-Page":next||""}); r.end(JSON.stringify(body)); };
      S.reqs.push(q.method+" "+q.url);
      if(q.headers["private-token"]!==TOKEN){ S.unauth++; return send(401,{message:"401 Unauthorized"}); }
      const u=new URL(q.url,"http://x"), p=u.pathname.split("/");     // ['', 'api', 'v4', 'projects', ref, 'issues', iid, kind]
      if(p[1]!=="api"||p[2]!=="v4"||p[3]!=="projects"||(p[4]!=="42"&&p[4]!=="acme%2Fgeneral")) return send(404,{message:"404 Not Found"});
      if(p.length===5&&q.method==="GET") return send(200,{id:42,name:"general",name_with_namespace:"Acme / general",path_with_namespace:"acme/general",web_url:S.base+"/acme/general"});
      if(p.length===6&&p[5]==="issues"&&q.method==="GET"){
        if((u.searchParams.get("page")||"1")!=="1") return send(200,[]);
        return send(200,Object.values(S.issues).filter(i=>i.state===u.searchParams.get("state")).map(i=>JSON.parse(JSON.stringify(i)))); }
      const it=p[5]==="issues"&&S.issues[p[6]];
      if(!it) return send(404,{message:"404 Not Found"});
      if(p.length===8&&q.method==="GET"){ const ev={resource_label_events:S.labelEv,resource_state_events:S.stateEv}[p[7]];
        if(!ev) return send(404,{message:"404 Not Found"});
        return send(200,(u.searchParams.get("page")||"1")==="1"?ev[it.iid]:[]); }
      if(p.length!==7) return send(404,{message:"404 Not Found"});
      if(q.method==="PUT"){ let b; try{ b=JSON.parse(raw); }catch(e){ return send(400,{message:"bad json"}); }
        S.puts.push({iid:it.iid,body:b}); const t=nowIso();
        const add=b.add_labels?b.add_labels.split(","):[], rm=b.remove_labels?b.remove_labels.split(","):[];
        rm.forEach(l=>{ if(it.labels.includes(l)){ it.labels=it.labels.filter(x=>x!==l); S.labelEv[it.iid].push({id:++evId,created_at:t,action:"remove",label:{id:evId,name:l}}); } });
        add.forEach(l=>{ if(!it.labels.includes(l)){ it.labels.push(l); S.labelEv[it.iid].push({id:++evId,created_at:t,action:"add",label:{id:evId,name:l}}); } });
        if(b.state_event==="close"&&it.state!=="closed"){ it.state="closed"; it.closed_at=t; S.stateEv[it.iid].push({id:++evId,created_at:t,state:"closed"}); }
        if(b.state_event==="reopen"&&it.state==="closed"){ it.state="opened"; it.closed_at=null; S.stateEv[it.iid].push({id:++evId,created_at:t,state:"reopened"}); }
        it.updated_at=t; }
      else if(q.method!=="GET") return send(405,{message:"405 Method Not Allowed"});
      send(200,JSON.parse(JSON.stringify(it))); }); });
  return {srv,S};
}

// ── 外掛的 HTTP 路由：假 req/res，回應全部留下來（之後找 token） ──
const RESP=[];
function call(route,method,url,body){
  return new Promise((resolve,reject)=>{
    const raw=body===undefined?null:Buffer.from(typeof body==="string"?body:JSON.stringify(body));
    const req={method,url,headers:raw?{"content-type":"application/json",host:"site.test",origin:"https://site.test"}:{host:"site.test"},
      on(ev,fn){ if(ev==="data"&&raw) fn(raw); if(ev==="end") fn(); return this; }};
    const res={status:0,headers:{},body:"",headersSent:false,
      writeHead(s,h){ this.status=s; this.headers=h||{}; this.headersSent=true; return this; },
      setHeader(k,v){ this.headers[k]=v; }, end(b){ this.body=b===undefined?"":String(b); RESP.push(method+" "+url+" "+this.status+" "+this.body); resolve(this); },
      destroy(){ resolve(this); }};
    Promise.resolve(route.handler(req,res)).catch(reject);
  });
}

(async function(){
const ROOT=fs.mkdtempSync(path.join(os.tmpdir(),"wolffia-v8gitlab-e2e-"))+"/", DATA=ROOT+"data/", TOKFILE=ROOT+"keys/gitlab.token";
fs.mkdirSync(path.dirname(TOKFILE)); fs.writeFileSync(TOKFILE,TOKEN+"\n");
const {srv,S}=fakeGitLab(); await new Promise(r=>srv.listen(0,"127.0.0.1",r));
S.base="http://127.0.0.1:"+srv.address().port;

// 外掛：真的 fetcher（fetch-project.py），只換掉執行方式（記下每一次執行，好等它跑完）與時鐘（避開 60 秒內不重抓）
const plugin=await import(dir+"site-plugin/lib/index.js"), {startFetcher}=await import(dir+"site-plugin/lib/fetcher.js");
let clock=Date.now(); const RUNS=[], OUT=[], LOGS=[], disposers=[], routes=[];
const run=(file,args,opts)=>{ const p=new Promise(resolve=>{
    cp.execFile(file,["-B",...args],{timeout:120000,env:Object.assign({},process.env,opts&&opts.env)},(err,stdout,stderr)=>{
      OUT.push(String(stdout)+String(stderr)); if(err) console.log("# fetch-project.py "+args.join(" ")+": "+String(stderr).trim().split("\n").pop()); resolve({args,env:opts&&opts.env,code:err?err.code||1:0}); }); });
  RUNS.push(p); return p; };
const lg=(...a)=>LOGS.push(a.join(" "));
plugin.apply({logger:()=>({warn:lg,error:lg,info:lg}),effect:fn=>{ const d=fn(); if(typeof d==="function") disposers.push(d); },connection:{},typertGateway:{},
    webServer:{register:r=>{ routes.push(r); return ()=>{}; }}},
  {dataDir:DATA,projects:[KEY],org:"acme",gitlabUrl:S.base,gitlabTokenFile:TOKFILE,fetchEveryMs:3600000,githubRecheckMs:0,digestEveryMs:0,siteName:"e2e-site"},
  {hasGitHub:()=>false,skillsDir:"",installSkill:()=>"unchanged",running:async()=>({at:nowIso(),items:[]}),
   startFetcher:o=>startFetcher(Object.assign({},o,{run,now:()=>clock}))});
const route=routes[0];
async function idle(){ for(let n=-1;n!==RUNS.length;){ n=RUNS.length; await Promise.all(RUNS); await new Promise(r=>setImmediate(r)); } }
const readData=()=>{ const G={}; new Function("window",fs.readFileSync(DATA+BASE+".js","utf8"))(G); return G.PROJECTS[KEY]; };

// ── 1 外掛看到 token 檔就開始抓：fetch-project.py 用假 GitLab 寫出 data/g-<12 碼>.js ──
await idle();
const r0=await RUNS[0];
ok(RUNS.length===1&&r0.code===0&&fs.existsSync(DATA+BASE+".js")&&fs.existsSync(DATA+"index.js")&&r0.args.slice(1).join(" ")==="--one "+KEY+" "+DATA
  &&r0.env.GITLAB_URL===S.base&&S.unauth===0,"外掛啟動 → fetch-project.py --one "+KEY+" 用假 GitLab 寫出 data/"+BASE+".js（"+S.reqs.length+" 個請求，沒有 401）");
if(!fs.existsSync(DATA+BASE+".js")){ console.log("bad 拿不到資料檔，停止"); process.exit(1); }
{ const d=readData(), st=Object.fromEntries(d.items.map(i=>[i.n,i.status]));
  ok(d.key===KEY&&d.items.length===4&&st[1]==="待辦"&&st[2]==="進行中 (7 天一輪)"&&st[3]==="審核後請關閉"&&st[4]==="完成","資料檔：#1 待辦、#2 進行中、#3 審核後請關閉、#4 完成（"+JSON.stringify(st)+"）"); }

// ── 頁面：HTML 與資料都經外掛 /board/ 取得；XHR 也送進外掛 ──
let SENT=[], PEND=[], WROTE=[], LS=[];
async function boot(search){
  SENT=[]; PEND=[]; WROTE=[]; LS=[];
  const page=await call(route,"GET","/board/"); const inl=[]; { const re=/<script>([\s\S]*?)<\/script>/g; let m; while((m=re.exec(page.body))) inl.push(m[1]); }
  const files={};
  for(const f of ["index.js",BASE+".js"]){ const x=await call(route,"GET","/board/data/"+f); if(x.status===200) files[f]=x.body; }
  const st={}; const ctx=new Proxy({},{get:(o,k)=>k==="createRadialGradient"?(()=>({addColorStop(){}})):()=>{}});
  const mk=id=>st[id]||(st[id]={id,style:{},classList:{add(){},remove(){},contains(){return false}},addEventListener(t,f){(this.L=this.L||{})[t]=f},getContext:()=>ctx,clientWidth:1200,clientHeight:720,
    getBoundingClientRect(){return{left:0,top:0,width:1200,height:720}},getAttribute(k){return this[k]},querySelector(){return{onclick:null}},querySelectorAll(){return []},appendChild(){},innerHTML:"",textContent:"",value:"0"});
  const QS={};
  const qs=sel=>{ const mm=sel.match(/^#(\w+) \.(\w+)$/); if(mm&&!new RegExp('class="'+mm[2]+'"').test(mk(mm[1]).innerHTML)) return null;
    return QS[sel]||(QS[sel]={onclick:null}); };
  const WL={}, WA={}; global.window={devicePixelRatio:1,open(){return {}},matchMedia:()=>({matches:false}),addEventListener(t,f){(WA[t]=WA[t]||[]).push(f); WL[t]=e=>WA[t].forEach(g=>g(e));}}; global.addEventListener=global.window.addEventListener;
  global.localStorage={getItem:()=>null,setItem(k,v){LS.push(k+"="+v);}}; global.location={search,pathname:"/board/"};
  global.confirm=()=>true;
  global.XMLHttpRequest=function(){ this.open=(mm,u)=>{this.m=mm;this.u=u}; this.setRequestHeader=()=>{};
    this.send=b=>{ const x=this; if(x.m==="POST"&&x.u==="api/status") SENT.push(JSON.parse(b));
      PEND.push(call(route,x.m,"/board/"+x.u,b===undefined||b===null?undefined:b).then(res=>{ x.status=res.status; x.responseText=res.body; x.onload&&x.onload(); })); }; };
  global.document={hidden:false,addEventListener(){},getElementById:mk,querySelector:qs,querySelectorAll:()=>[],createElement:()=>mk("x"+Math.random()),
    write(s){ const f=s.match(/data\/([\w-]+\.js)/)[1]; WROTE.push(f); if(files[f]!==undefined) new Function(files[f])(); }};
  // 頁面的時鐘與計時器只給頁面（換掉全域的會弄壞外掛用的 fetch）
  let T=0,F=null; const PG=[{now:()=>T},()=>0,()=>0,f=>{F=f}];
  new Function(files["index.js"])();
  inl.forEach((s,i)=>{ if(i===inl.length-1) s+=";global.__R={ITEMS,COLS,RING,CX,CY,SX,PJ};"; new Function("performance","setTimeout","clearTimeout","requestAnimationFrame",s)(...PG); });
  const tick=n=>{for(let k=0;k<n;k++){T+=40;F(T);}}; tick(40);
  return {R:global.__R,st,WL,tick};
}
const settle=async()=>{ while(PEND.length) await PEND.shift(); };
const ev=(x,y)=>({clientX:x,clientY:y,preventDefault(){}});
function dragTo(b,it,r){ const {R,st,WL,tick}=b, at=rr=>[R.CX+Math.cos(it.ang)*rr*R.SX,R.CY+Math.sin(it.ang)*rr];
  SENT=[]; st.cv.L.mousedown(ev(it.px,it.py)); const r0=Math.hypot((it.px-R.CX)/R.SX,it.py-R.CY);
  for(let k=1;k<=8;k++){ const [x,y]=at(r0+(r-r0)*k/8); WL.mousemove(ev(x,y)); } WL.mouseup(); tick(10); return SENT[0]; }
const ring=(R,n)=>{ const it=R.ITEMS.find(i=>i.n===n); return it&&it.st?(it.st.col==="closed"?"完成":it.st.col):null; };
const counts=R=>{ const c={}; ["待辦","進行中 (7 天一輪)","審核後請關閉","完成"].forEach(k=>c[k]=0); R.ITEMS.forEach(i=>{ const k=i.st&&(i.st.col==="closed"?"完成":i.st.col); if(k in c) c[k]++; }); return c; };

// ── 2 頁面顯示四圈各 1 張 ──
let b=await boot("?p="+KEY), R=b.R;
ok(window.PROJECT_NUM===KEY&&R.PJ.key===KEY&&WROTE.join()===BASE+".js","頁面 ?p="+KEY+" 經外掛 /board/data/ 載入 "+WROTE.join());
{ const c=counts(R);
  ok(R.ITEMS.length===4&&Object.values(c).every(n=>n===1)&&ring(R,1)==="待辦"&&ring(R,2)==="進行中 (7 天一輪)"&&ring(R,3)==="審核後請關閉"&&ring(R,4)==="完成",
    "四圈各 1 張："+JSON.stringify(c)); }

// ── 3 #2 從「進行中」拖到「審核後請關閉」→ 假 GitLab 收到換 label 的 PUT ──
clock+=61000;
{ const it=R.ITEMS.find(i=>i.n===2), s=dragTo(b,it,R.RING["審核後請關閉"]); await settle(); await idle();
  ok(!!s&&s.project===KEY&&s.url===S.base+"/acme/general/-/issues/2"&&s.status==="審核後請關閉","頁面送出 api/status "+JSON.stringify(s));
  const put=S.puts.filter(x=>x.iid===2);
  ok(put.length===1&&JSON.stringify(put[0].body)===JSON.stringify({add_labels:"審核後請關閉",remove_labels:"進行中"})&&JSON.stringify(S.issues[2].labels)==='["審核後請關閉"]'&&S.issues[2].state==="opened",
    "假 GitLab 收到 #2 換 label："+JSON.stringify(put.map(x=>x.body)));
  ok(/已移到「審核後請關閉」/.test(b.st.toast.innerHTML)&&/e2e-site/.test(b.st.toast.innerHTML),"頁面提示寫入成功（"+b.st.toast.innerHTML.replace(/<[^>]+>/g," ").slice(0,60)+"…）"); }

// ── 4 #1 從「待辦」拖進星核（完成）→ 假 GitLab 收到關閉請求 ──
clock+=61000;
{ const it=R.ITEMS.find(i=>i.n===1), s=dragTo(b,it,10); await settle(); await idle();
  ok(!!s&&s.project===KEY&&s.url===S.base+"/acme/general/-/issues/1"&&s.status==="完成","拖進星核 → api/status "+JSON.stringify(s));
  const put=S.puts.filter(x=>x.iid===1);
  ok(put.length===1&&JSON.stringify(put[0].body)===JSON.stringify({state_event:"close"})&&S.issues[1].state==="closed"&&JSON.stringify(S.issues[1].labels)==='["bug"]',
    "假 GitLab 收到 #1 關閉："+JSON.stringify(put.map(x=>x.body)));
  ok(S.puts.length===2,"假 GitLab 總共只收到 2 個 PUT（"+S.puts.length+"）"); }

// ── 5 外掛改完就重抓；重新開頁面 → 新位置 ──
ok(RUNS.length===3&&(await Promise.all(RUNS)).every(x=>x.code===0&&x.args[2]===KEY),"每次改狀態後外掛都重抓 "+KEY+"（共 "+RUNS.length+" 次 fetch-project.py）");
{ const d=readData(), st=Object.fromEntries(d.items.map(i=>[i.n,i.status]));
  ok(st[1]==="完成"&&st[2]==="審核後請關閉"&&st[3]==="審核後請關閉"&&st[4]==="完成","重抓後的資料檔："+JSON.stringify(st));
  const h2=d.items.find(i=>i.n===2).hist;
  ok(h2.length&&h2[h2.length-1][1]==="審核後請關閉"&&d.items.find(i=>i.n===1).hist.some(x=>x[1]==="closed"),"變更紀錄有新的 label／關閉事件"); }
b=await boot("?p="+KEY); R=b.R;
{ const c=counts(R);
  ok(ring(R,1)==="完成"&&ring(R,2)==="審核後請關閉"&&ring(R,3)==="審核後請關閉"&&ring(R,4)==="完成"&&c["待辦"]===0&&c["進行中 (7 天一輪)"]===0&&c["審核後請關閉"]===2&&c["完成"]===2,
    "重新開頁面：#1 在完成、#2 在審核後請關閉 "+JSON.stringify(c)); }

// ── 6 token 不在任何輸出檔、回應、紀錄裡（只在 token 檔，和送給 GitLab 的標頭） ──
{ const walk=d=>fs.readdirSync(d,{withFileTypes:true}).flatMap(e=>e.isDirectory()?walk(d+e.name+"/"):[d+e.name]);
  const files=walk(ROOT).filter(f=>f!==TOKFILE);
  const all=[...files.map(f=>fs.readFileSync(f,"utf8")),...RESP,...OUT,...LOGS,...LS,...S.reqs,JSON.stringify(window.PROJECT),JSON.stringify(window.PROJECT_INDEX),
    ...Object.values(b.st).map(e=>String(e.innerHTML)+String(e.textContent)+String(e.value))].join("\n");
  ok(files.some(f=>f.endsWith(BASE+".js"))&&files.some(f=>f.endsWith("projects.json"))&&RESP.length>=8&&all.indexOf(TOKEN)<0&&all.indexOf("glpat-")<0,
    "token 不在輸出檔（"+files.map(f=>path.relative(ROOT,f)).join("、")+"）、"+RESP.length+" 個回應、fetch 輸出、紀錄裡"); }

disposers.forEach(d=>{ try{ d(); }catch(e){} }); srv.close();
fs.rmSync(ROOT,{recursive:true,force:true});
console.log(bad?"bad "+bad:"全部通過");
process.exit(bad?1:0);
})().catch(e=>{ console.log("bad 例外："+(e&&e.stack||e)); process.exit(1); });
