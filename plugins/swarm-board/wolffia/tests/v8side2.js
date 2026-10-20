// 側欄寬度拖曳（#sideDrag，記在 localStorage wolffia.sideW）＋站台 /board/ 藏「引擎」選單（#engRow）。
// 和 v8think 同方式：node 假 DOM 跑 v8.html（示範資料，make-demo-epic.py）。
const fs=require("fs"),dir=__dirname+"/../",DEMO="/tmp/wolffia-v8-side2-demo.js";
const h=fs.readFileSync(dir+"v8.html","utf8"); const re=/<script>([\s\S]*?)<\/script>/g; let m; const inl=[]; while((m=re.exec(h))) inl.push(m[1]);
require("child_process").execFileSync("python3",[dir+"make-demo-epic.py",DEMO]);
const demoSrc=fs.readFileSync(DEMO,"utf8");
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};
let st={}, LS={}, DL={};
function boot(pathname,keep){
  st={}; DL={}; if(!keep) LS={};
  const ctx=new Proxy({},{get:(_,k)=>k==="createRadialGradient"?(()=>({addColorStop(){}})):()=>{}});
  const cls=()=>{ const S=new Set(); return {add(c){S.add(c)},remove(c){S.delete(c)},contains(c){return S.has(c)}}; };
  const mk=id=>st[id]||(st[id]={id,style:{},classList:cls(),addEventListener(t,f){(this.L=this.L||{})[t]=f},getContext:()=>ctx,clientWidth:1200,clientHeight:720,
    getBoundingClientRect(){return{left:0,top:0,width:1200,height:720}},getAttribute(k){return this[k]},querySelector(){return{onclick:null}},querySelectorAll(){return []},appendChild(){},innerHTML:"",textContent:"",value:"0"});
  global.window={devicePixelRatio:1,innerWidth:1000,open(){return {}},addEventListener(){},matchMedia:()=>({matches:false})};
  global.addEventListener=global.window.addEventListener;
  global.localStorage={getItem:k=>k in LS?LS[k]:null,setItem(k,v){LS[k]=String(v)},removeItem(k){delete LS[k]}}; global.location={search:"?demo=1",pathname};
  global.confirm=()=>true; global.setTimeout=()=>0; global.clearTimeout=()=>0;
  global.XMLHttpRequest=function(){ this.open=()=>{}; this.setRequestHeader=()=>{}; this.send=()=>{}; };
  global.document={hidden:false,addEventListener(t,f){(DL[t]=DL[t]||[]).push(f)},getElementById:mk,querySelector:()=>({onclick:null}),querySelectorAll:()=>[],
    createElement:()=>mk("x"+Math.random()), write(){ new Function(demoSrc)(); }};
  let T=0,F=null; global.performance={now:()=>T}; global.requestAnimationFrame=f=>{F=f};
  new Function('window.PROJECT_INDEX={projects:[],"default":0};')();
  inl.forEach(s=>new Function(s)());
  for(let k=0;k<5;k++){ T+=40; F(T); }
}
const fire=(t,ev)=>(DL[t]||[]).forEach(f=>f(ev||{}));
const drag=x=>{ st.sideDrag.onmousedown({button:0,preventDefault(){}}); fire("mousemove",{clientX:x}); fire("mouseup",{}); };

ok(/<div id="sideDrag"/.test(h)&&/#sideDrag\{[^}]*cursor:col-resize/.test(h),"有 #sideDrag 分隔線，游標 col-resize");
ok(/#wrap\.fold #sideDrag\{display:none\}/.test(h),"側欄收合時不顯示分隔線");
ok(/@media\(max-width:850px\)\{[^\n]*#sideDrag\{display:none\}/.test(h),"窄螢幕（≤850px）不顯示分隔線");

// ── 1 拖曳 ──
boot("/");
ok(!st.side.style.width,"沒存過 → #side 用預設寬度（不設 inline width）");
ok(typeof st.sideDrag.onmousedown==="function"&&typeof st.sideDrag.ondblclick==="function","分隔線有 mousedown／dblclick");
fire("mousemove",{clientX:300}); ok(!st.side.style.width,"沒按住時移動滑鼠 → 不改寬度");
st.sideDrag.onmousedown({button:0,preventDefault(){}}); ok(st.wrap.classList.contains("drag"),"按住 → #wrap.drag");
fire("mousemove",{clientX:500}); ok(st.side.style.width==="500px","拖曳改變 #side 寬度（視窗 1000、滑鼠 500 → 500px）");
ok(LS["wolffia.sideW"]===undefined,"拖曳中還沒存");
fire("mouseup",{}); ok(LS["wolffia.sideW"]==="500","放開 → 寬度存進 wolffia.sideW");
ok(!st.wrap.classList.contains("drag"),"放開 → 取消 drag");
drag(900); ok(st.side.style.width==="260px"&&LS["wolffia.sideW"]==="260","不小於 260");
drag(50); ok(st.side.style.width==="700px","不大於視窗寬 70%");
fire("mousemove",{clientX:400}); ok(st.side.style.width==="700px","放開後再移動 → 不改寬度");

// ── 2 載入時套用 ──
LS["wolffia.sideW"]="420"; boot("/",true); ok(st.side.style.width==="420px","載入時套用存好的寬度");
LS["wolffia.sideW"]="5000"; boot("/",true); ok(st.side.style.width==="700px","存的寬度太大 → 載入時夾到 70%");

// ── 3 雙擊清掉 ──
LS["wolffia.sideW"]="420"; boot("/",true); st.sideDrag.ondblclick({});
ok(!("wolffia.sideW" in LS),"雙擊清掉 wolffia.sideW"); ok(st.side.style.width==="","雙擊回預設寬度");
boot("/",true); ok(!st.side.style.width,"清掉後重新載入仍是預設");

// ── 4 /board/ 藏引擎選單 ──
boot("/board/"); ok(st.engRow.style.display==="none","/board/ 下 #engRow 隱藏");
boot("/board/index.html"); ok(st.engRow.style.display==="none","/board/index.html 下 #engRow 隱藏");
for(const p of ["/","/v8.html","/boardx/"]){ boot(p); ok(document.getElementById("engRow").style.display!=="none","網址 "+p+" 下 #engRow 顯示"); }

console.log(bad?"bad "+bad:"全部通過"); process.exit(bad?1:0);
