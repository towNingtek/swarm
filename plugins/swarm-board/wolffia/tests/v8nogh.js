// 尚未連接 GitHub：頁面在 /board/（站台 DSH）底下時 GET data/status.json；{"github":false}＝站台沒登入 gh、不會抓資料，
// 顯示「尚未連接 GitHub：請在站台的 AI 對話說『幫我連接 GitHub』」，不顯示錯誤；還沒有資料也不能整頁壞掉。
// 和 v8suggest 同方式：node 假 DOM＋假的 XHR 跑 v8.html。
const fs=require("fs"),dir=__dirname+"/../",DEMO="/tmp/wolffia-v8-nogh-demo.js";
const h=fs.readFileSync(dir+"v8.html","utf8"); const re=/<script>([\s\S]*?)<\/script>/g; let m; const inl=[]; while((m=re.exec(h))) inl.push(m[1]);
require("child_process").execFileSync("python3",[dir+"make-demo-epic.py",DEMO]);
const demoSrc=fs.readFileSync(DEMO,"utf8");
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};
let REQ=[], st={};
// data：有沒有看板資料（false＝站台從沒抓過，data/index.js 與 p*.js 都是 404）
function boot(pathname,data){
  REQ=[]; st={};
  const ctx=new Proxy({},{get:(_,k)=>k==="createRadialGradient"?(()=>({addColorStop(){}})):()=>{}});
  const mk=id=>st[id]||(st[id]={id,style:{},classList:{add(){},remove(){},contains(){return false}},addEventListener(t,f){(this.L=this.L||{})[t]=f},getContext:()=>ctx,clientWidth:1200,clientHeight:720,
    getBoundingClientRect(){return{left:0,top:0,width:1200,height:720}},getAttribute(k){return this[k]},querySelector(){return{onclick:null}},querySelectorAll(){return []},appendChild(){},innerHTML:"",textContent:"",value:"0"});
  global.window={devicePixelRatio:1,open(){return {}},addEventListener(){},matchMedia:()=>({matches:false})};
  global.addEventListener=global.window.addEventListener;
  global.localStorage={getItem:()=>null,setItem(){}}; global.location={search:data?"?demo=1":"",pathname};
  global.confirm=()=>true; global.setTimeout=()=>0; global.clearTimeout=()=>0;
  global.XMLHttpRequest=function(){ this.open=(mm,u)=>{this.m=mm;this.u=u}; this.setRequestHeader=(k,v)=>{(this.H=this.H||{})[k]=v}; this.send=b=>{ this.body=b; REQ.push(this); }; };
  global.document={hidden:false,addEventListener(){},getElementById:mk,querySelector:()=>({onclick:null}),querySelectorAll:()=>[],
    createElement:()=>mk("x"+Math.random()), write(){ if(data) new Function(demoSrc)(); }};
  let T=0,F=null; global.performance={now:()=>T}; global.requestAnimationFrame=f=>{F=f};
  if(data) new Function('window.PROJECT_INDEX={projects:[],"default":0};')();
  let err=null;
  try{ inl.forEach(s=>new Function(s)()); for(let k=0;k<20&&F;k++){ T+=40; F(T); } }catch(e){ err=e; }
  return err;
}
const MSG="尚未連接 GitHub：請在站台的 AI 對話說「幫我連接 GitHub」";
const B=()=>st["noGh"]||{style:{}};
const shown=()=>B().style.display==="block"&&B().textContent===MSG;
const toast=()=>st["toast"]&&st["toast"].style.display==="block";
const gets=()=>REQ.filter(x=>x.m==="GET"&&x.u==="data/status.json");
function answer(status,body){ const x=gets()[0]; x.status=status; x.responseText=typeof body==="string"?body:JSON.stringify(body); x.onload(); return x; }

ok(/id="noGh"/.test(h),"頁面有「尚未連接 GitHub」提示區");
ok(h.includes(MSG),"提示文字："+MSG);
ok(/data\/status\.json/.test(h),"讀 data/status.json");

// ── 1 github:false → 顯示「尚未連接 GitHub」，不顯示錯誤 ──
let e=boot("/board/",false); ok(!e,"/board/、還沒有任何資料 → 頁面不丟錯"+(e?"（"+e.message+"）":""));
ok(gets().length===1,"/board/ 下 GET data/status.json（相對路徑＝/board/data/status.json）");
ok(!shown(),"還沒回來前不顯示");
answer(200,{github:false});
ok(shown(),"github:false → 顯示「尚未連接 GitHub」");
ok(!toast(),"github:false → 不顯示錯誤");
ok(!/error|錯誤|HTTP/i.test(B().textContent),"提示裡沒有錯誤字樣");
e=boot("/board/",true); ok(!e,"/board/、有舊資料 → 頁面不丟錯"); answer(200,{github:false}); ok(shown(),"有舊資料但 github:false → 也顯示「尚未連接 GitHub」");
e=boot("/board/index.html",false); ok(!e,"/board/index.html 不丟錯"); answer(200,{github:false}); ok(shown(),"/board/index.html 也顯示");

// ── 2 github:true 或不明 → 不顯示 ──
boot("/board/",true); answer(200,{github:true}); ok(!shown()&&B().style.display!=="block","github:true → 不顯示「尚未連接 GitHub」");
boot("/board/",false); answer(200,{github:true}); ok(B().style.display!=="block","github:true、還沒有資料 → 不顯示");
boot("/board/",true); answer(404,"not found"); ok(B().style.display!=="block","404（舊站台沒有 status.json）→ 不顯示");
boot("/board/",true); answer(200,"不是 JSON"); ok(B().style.display!=="block","壞掉的 JSON → 不顯示");
boot("/board/",true); answer(200,{}); ok(B().style.display!=="block","沒有 github 欄位 → 不顯示");

// ── 3 不在 /board/ 底下 → 不讀也不顯示 ──
for(const p of ["/","/v8.html","/wolffia/v8.html","/boardx/"]){ boot(p,true); ok(gets().length===0&&B().style.display!=="block","網址 "+p+" → 不讀 data/status.json、不顯示"); }
boot(undefined,true); ok(gets().length===0,"沒有 pathname → 不讀");

console.log(bad?"bad "+bad:"全部通過"); process.exit(bad?1:0);
