// 看板認得其他組織的 project：?p=組織/編號 選到那個 project（資料檔 data/p-組織-編號.js、window.PROJECTS["組織/編號"]），
// ?p=N 照舊是 acme 的 N（data/pN.js），沒有 ?p 用 index.js 的預設。acme 和別的組織同編號也分得開。
// 和 v8move 同方式：node 假 DOM＋假的 XHR 跑 v8.html；資料是這支測試自己產生的假資料，不需要 /var/www。
const fs=require("fs"),os=require("os"),path=require("path"),dir=__dirname+"/../";
const h=fs.readFileSync(dir+"v8.html","utf8"); const re=/<script>([\s\S]*?)<\/script>/g; let m; const inl=[]; while((m=re.exec(h))) inl.push(m[1]);
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};

// 假資料：acme 2、acme 1、other 1（和 acme 1 同編號）
const D=fs.mkdtempSync(path.join(os.tmpdir(),"wolffia-v8multiorg-"))+"/";
const NOWS="2026-10-20T00:00:00Z";
function board(owner,num,key,title){
  const items=[1,2,3].map(i=>({n:num*100+i,t:title+" #"+i,u:"https://github.com/"+owner+"/r/issues/"+(num*100+i),repo:"r",owner,
    state:"OPEN",created:"2026-10-0"+i+"T00:00:00Z",closed:null,status:"待辦",since:"2026-10-0"+i+"T00:00:00Z",hist:[],who:[],labels:[],
    type:null,parent:null,blockedBy:[],target:null}));
  return {num,owner,key,cols:["待辦","進行中 (7 天一輪)","審核後請關閉","完成"],title,url:"https://github.com/orgs/"+owner+"/projects/"+num,fetched:NOWS,items};
}
const B={2:board("acme",2,2,"TI 二號"),1:board("acme",1,1,"TI 一號"),"other/1":board("other",1,"other/1","Bright 一號")};
const FILES={"p2.js":2,"p1.js":1,"p-other-1.js":"other/1"};
for(const f in FILES) fs.writeFileSync(D+f,"window.PROJECTS=window.PROJECTS||{};window.PROJECTS["+JSON.stringify(FILES[f])+"]="+JSON.stringify(B[FILES[f]])+";\n");
const row=(k)=>({num:B[k].num,owner:B[k].owner,key:B[k].key,title:B[k].title,open:3,total:3});
fs.writeFileSync(D+"index.js","window.PROJECT_INDEX="+JSON.stringify({org:"acme",default:2,projects:[row(2),row(1),row("other/1")]})+";\n");

let SENT=[], GETS=[], WROTE=[];
function boot(search,index){
  SENT=[]; GETS=[]; WROTE=[];
  const st={}; const ctx=new Proxy({},{get:(o,k)=>k==="createRadialGradient"?(()=>({addColorStop(){}})):()=>{}});
  const mk=id=>st[id]||(st[id]={id,style:{},classList:{add(){},remove(){},contains(){return false}},addEventListener(t,f){(this.L=this.L||{})[t]=f},getContext:()=>ctx,clientWidth:1200,clientHeight:720,
    getBoundingClientRect(){return{left:0,top:0,width:1200,height:720}},getAttribute(k){return this[k]},querySelector(){return{onclick:null}},querySelectorAll(){return []},appendChild(){},innerHTML:"",textContent:"",value:"0"});
  const WL={}, WA={}; global.window={devicePixelRatio:1,open(){return {}},addEventListener(t,f){(WA[t]=WA[t]||[]).push(f); WL[t]=e=>WA[t].forEach(g=>g(e));}}; global.addEventListener=global.window.addEventListener;
  global.localStorage={getItem:()=>null,setItem(){}}; global.setTimeout=()=>0; global.clearTimeout=()=>0; global.location={search,pathname:"/board/"};
  global.confirm=()=>true;
  global.XMLHttpRequest=function(){ this.open=(mm,u)=>{this.m=mm;this.u=u}; this.setRequestHeader=()=>{};
    this.send=b=>{ if(this.m==="POST"&&this.u==="api/status"){ SENT.push(JSON.parse(b)); this.status=200; this.responseText='{"ok":true,"by":"t"}'; this.onload&&this.onload(); } else GETS.push(this.u); }; };
  global.document={hidden:false,addEventListener(){},getElementById:mk,querySelector:()=>({onclick:null}),querySelectorAll:()=>[],createElement:()=>mk("x"+Math.random()),
    write(s){ const f=s.match(/data\/([\w-]+\.js)/)[1]; WROTE.push(f); if(fs.existsSync(D+f)) new Function(fs.readFileSync(D+f,"utf8"))(); }};
  let T=0,F=null; global.performance={now:()=>T}; global.requestAnimationFrame=f=>{F=f};
  if(index) new Function(index)(); else new Function(fs.readFileSync(D+"index.js","utf8"))();
  inl.forEach((s,i)=>{ if(i===inl.length-1) s+=";global.__R={ITEMS,COLS,RING,CX,CY,SX,PJ,act:window.__act};"; new Function(s)(); });
  const tick=n=>{for(let k=0;k<n;k++){T+=40;F(T);}}; tick(40);
  return {R:global.__R,st,WL,tick};
}
const ev=(x,y)=>({clientX:x,clientY:y,preventDefault(){}});
function dragOne(b){ const {R,st,WL,tick}=b, it=R.ITEMS.find(i=>i.vis&&i.st&&i.st.col!=="closed"&&!i.epic), c=R.COLS.find(c=>c!==it.st.col);
  const r=R.RING[c], at=rr=>[R.CX+Math.cos(it.ang)*rr*R.SX,R.CY+Math.sin(it.ang)*rr];
  SENT=[]; st.cv.L.mousedown(ev(it.px,it.py)); const r0=Math.hypot((it.px-R.CX)/R.SX,it.py-R.CY);
  for(let k=1;k<=8;k++){ const [x,y]=at(r0+(r-r0)*k/8); WL.mousemove(ev(x,y)); } WL.mouseup(); tick(10); return SENT[0]; }

// ?p=組織/編號
for(const q of ["?p=other/1","?p=other%2F1","?p=Other/1"]){
  const b=boot(q), R=b.R;
  ok(window.PROJECT_NUM==="other/1"&&R.PJ.owner==="other"&&R.PJ.num===1&&R.PJ.title==="Bright 一號",
     q+" → other 的 1 號（"+R.PJ.title+"，PROJECT_NUM="+window.PROJECT_NUM+"）");
  ok(WROTE.join()==="p-other-1.js",q+" 載入 data/"+WROTE.join());
  ok(R.ITEMS.length>0&&R.ITEMS.every(i=>i.u.indexOf("/other/")>0),q+" 畫面上的卡都是 other 的");
}
{ const b=boot("?p=other/1"), s=dragOne(b);
  ok(!!s&&s.project==="other/1","other 1 號拖曳改狀態 → api/status 的 project="+JSON.stringify(s&&s.project));
  const sel=b.st.pSel.innerHTML; ok(/value="other\/1" selected/.test(sel)&&/value="1">/.test(sel)&&/value="2">/.test(sel),"project 選單：value 是 key，選中的是 other/1"); }

// ?p=2 照舊是 acme 2；?p=1 是 acme 1，不是 other 1
for(const [q,n,t] of [["?p=2",2,"TI 二號"],["?p=1",1,"TI 一號"],["?p=acme/1",1,"TI 一號"]]){
  const b=boot(q), R=b.R;
  ok(window.PROJECT_NUM===n&&R.PJ.title===t&&R.PJ.owner==="acme",q+" → acme "+n+"（"+R.PJ.title+"）");
  ok(WROTE.join()==="p"+n+".js",q+" 載入 data/"+WROTE.join());
  const s=dragOne(b); ok(!!s&&s.project===n,q+" 拖曳改狀態 → project="+JSON.stringify(s&&s.project));
}

// 沒有 ?p、或不在清單裡 → 預設
for(const q of ["","?p=9","?p=other-org/2","?p=../../x"]){
  const R=boot(q).R;
  ok(window.PROJECT_NUM===2&&R.PJ.title==="TI 二號"&&WROTE.join()==="p2.js",JSON.stringify(q)+" → 預設 acme 2（data/"+WROTE.join()+"）");
}
// 預設也可以是別的組織的 project
{ const R=boot("",fs.readFileSync(D+"index.js","utf8").replace('"default":2','"default":"other/1"')).R;
  ok(window.PROJECT_NUM==="other/1"&&R.PJ.title==="Bright 一號","預設是 \"other/1\" → 沒有 ?p 時選到它"); }
// 舊的 index.js（沒有 key／owner）：?p=2 照舊
{ const R=boot("?p=3",'window.PROJECT_INDEX={"default":2,"projects":[{"num":2,"title":"TI 二號","open":3,"total":3},{"num":1,"title":"TI 一號","open":3,"total":3}]};').R;
  ok(window.PROJECT_NUM===2&&R.PJ.title==="TI 二號","舊 index.js（沒有 key）→ 預設 2");
  const R2=boot("?p=1",'window.PROJECT_INDEX={"default":2,"projects":[{"num":2,"title":"TI 二號","open":3,"total":3},{"num":1,"title":"TI 一號","open":3,"total":3}]};').R;
  ok(window.PROJECT_NUM===1&&R2.PJ.title==="TI 一號","舊 index.js（沒有 key）→ ?p=1 照舊"); }

fs.rmSync(D,{recursive:true,force:true});
console.log(bad?"bad "+bad:"全部通過");
process.exit(bad?1:0);
