// 舊分析回答收合＋「⤢ 放大」：新的分析回答出現後，之前的回答加 class fold（.m.a.fold，內容不刪）收成一行（問題前 40 字＋▸ 展開），
// 點收合列展開／再點收合；最新一則保持展開。每則回答右上角 button.mdExpand「⤢」打開蓋在畫面上的大視窗（#mdBig），Esc 或點背景關閉。
// 和 v8think 同方式：node 假 DOM＋假的 XHR 跑 v8.html（/board/ 底下、示範資料，把 demo 旗標關掉才會真的送 XHR）。
const fs=require("fs"),dir=__dirname+"/../",DEMO="/tmp/wolffia-v8-fold-demo.js";
const h=fs.readFileSync(dir+"v8.html","utf8"); const re=/<script>([\s\S]*?)<\/script>/g; let m; const inl=[]; while((m=re.exec(h))) inl.push(m[1]);
require("child_process").execFileSync("python3",[dir+"make-demo-epic.py",DEMO]);
const demoSrc=fs.readFileSync(DEMO,"utf8");
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};
let REQ=[], st={}, MADE=[], KEYS=[];
// 假元素：classList 是真的集合；querySelector 依選擇器回傳每個元素各自固定的子元素（可掛 onclick、textContent）
function cl(){ const S=new Set(); return {add:c=>S.add(c),remove:c=>S.delete(c),contains:c=>S.has(c),toggle:c=>S.has(c)?(S.delete(c),false):(S.add(c),true)}; }
function el(id){ const e={id,style:{},classList:cl(),Q:{},addEventListener(t,f){(this.L=this.L||{})[t]=f},
  getContext:()=>new Proxy({},{get:(_,k)=>k==="createRadialGradient"?(()=>({addColorStop(){}})):()=>{}}),clientWidth:1200,clientHeight:720,
  getBoundingClientRect(){return{left:0,top:0,width:1200,height:720}},getAttribute(k){return this[k]},
  querySelector(s){ return this.Q[s]||(this.Q[s]=el(id+" "+s)); },querySelectorAll(){return []},appendChild(){},focus(){},innerHTML:"",textContent:"",value:"0"}; return e; }
function boot(){
  REQ=[]; st={}; MADE=[]; KEYS=[];
  const mk=id=>st[id]||(st[id]=el(id));
  global.window={devicePixelRatio:1,open(){return {}},addEventListener(t,f){ if(t==="keydown") KEYS.push(f); },matchMedia:()=>({matches:false})};
  global.addEventListener=global.window.addEventListener;
  global.localStorage={getItem:()=>null,setItem(){}}; global.location={search:"?demo=1",pathname:"/board/"};
  global.confirm=()=>true; global.setTimeout=()=>0; global.clearTimeout=()=>0;
  global.XMLHttpRequest=function(){ this.open=(mm,u)=>{this.m=mm;this.u=u}; this.setRequestHeader=()=>{}; this.send=b=>{ this.body=b; REQ.push(this); }; };
  global.document={hidden:false,addEventListener(){},getElementById:mk,querySelector:()=>({onclick:null}),querySelectorAll:()=>[],
    createElement:()=>{ const e=mk("x"+Math.random()); MADE.push(e); return e; }, write(){ new Function(demoSrc)(); }};
  let T=0,F=null; global.performance={now:()=>T}; global.requestAnimationFrame=f=>{F=f};
  new Function('window.PROJECT_INDEX={projects:[],"default":0};')();
  inl.forEach((s,i)=>{ if(i===inl.length-1) s+=";global.__R={ITEMS,PJ,setSel};"; new Function(s)(); });
  for(let k=0;k<20;k++){ T+=40; F(T); }
  return global.__R;
}
// 送一個問題，回答回來，回傳那則回答的泡泡
function ask(q,text,sid){
  st["chatIn"].value=q; st["thinkB"].onclick();
  const p=REQ.filter(x=>x.m==="POST"&&x.u==="api/think").pop();
  p.status=200; p.responseText=JSON.stringify({sessionId:sid,created:true,after:-1}); p.onload();
  const box=MADE.filter(e=>/正在分析…/.test(e.innerHTML)).pop();
  const y=REQ.filter(r=>r.m==="GET"&&/^api\/think\/reply\?/.test(r.u)).pop();
  y.status=200; y.responseText=JSON.stringify({done:true,text}); y.onload();
  return box;
}
const key=k=>KEYS.forEach(f=>f({key:k,target:{tagName:"DIV"},preventDefault(){}}));

ok(/\.m\.a\.fold/.test(h),"CSS 有 .m.a.fold（收合樣式）");
ok(/#mdBigBox\{[^}]*max-width:calc\(100vw[^}]*\}/.test(h)&&/#mdBigBox\{[^}]*width:900px/.test(h)&&/#mdBigBox\{[^}]*max-height:85vh/.test(h)&&/#mdBigBox\{[^}]*overflow:auto/.test(h),"大視窗寬 ≤ 900px、高 ≤ 85vh、可捲動");
ok(/#mdBig\{[^}]*position:fixed/.test(h),"大視窗蓋在畫面上（position:fixed）");

const R=boot(); R.PJ.demo=false;
const Q1="第一個問題：哪些卡卡最久？而且這個問題故意寫得很長很長很長，超過四十個字一定會被截斷才對喔喔喔";
const a1=ask(Q1,"第一則 **回答**\n\n| a | b |\n|---|---|\n| 1 | 2 |","s1");
ok(!!a1&&/第一則 <b>回答<\/b>/.test(a1.innerHTML),"第一則回答顯示（Markdown 排版）");
ok(/class="mdExpand"[^>]*>⤢</.test(a1.innerHTML),"回答右上角有「⤢」按鈕（class=\"mdExpand\"）");
ok(!a1.classList.contains("fold"),"只有一則時不收合");
const head=Array.from(Q1).slice(0,40).join("");
const barTxt=(a1.innerHTML.match(/class="foldBar"[^>]*>([\s\S]*?)<span class="fl">/)||[])[1]||"";
ok(barTxt.indexOf(head)>=0&&barTxt.indexOf(Array.from(Q1).slice(0,41).join(""))<0,"收合列顯示問題前 40 字");
ok(/▸ 展開/.test(a1.innerHTML),"收合列有「▸ 展開」");

const a2=ask("第二個問題","第二則回答","s1");
ok(a2!==a1&&/第二則回答/.test(a2.innerHTML),"第二則回答顯示");
ok(a1.classList.contains("fold"),"第二則回答出現後，第一則有 fold");
ok(!a2.classList.contains("fold"),"最新一則沒有 fold");
ok(/第一則 <b>回答<\/b>/.test(a1.innerHTML),"收合不刪內容");

const bar=a1.querySelector(".foldBar");
ok(typeof bar.onclick==="function","收合列可以點");
bar.onclick(); ok(!a1.classList.contains("fold"),"點收合列 → 展開");
ok(/收合/.test(bar.querySelector(".fl").textContent),"展開後顯示「▾ 收合」");
bar.onclick(); ok(a1.classList.contains("fold"),"再點 → 收合");
ok(/展開/.test(bar.querySelector(".fl").textContent),"收合後顯示「▸ 展開」");

const a3=ask("第三個問題","第三則","s1");
ok(a1.classList.contains("fold")&&a2.classList.contains("fold")&&!a3.classList.contains("fold"),"第三則出現 → 前兩則都收合，最新一則展開");

// ⤢ 放大
const B=st["mdBig"];
ok(B.style.display!=="flex","大視窗一開始沒開");
const ex=a1.querySelector(".mdExpand"); ok(typeof ex.onclick==="function","「⤢」可以點");
ex.onclick();
ok(B.style.display==="flex","按「⤢」→ 打開大視窗");
const body1=a1.innerHTML.split('<div class="mdBody">')[1].replace(/<\/div>$/,"");
ok(body1.length>0&&B.innerHTML.indexOf(body1)>=0&&/<table class="mdTable">/.test(B.innerHTML),"大視窗內容和那則回答相同");
key("Escape"); ok(B.style.display!=="flex","Esc 關閉大視窗");
a3.querySelector(".mdExpand").onclick();
ok(B.style.display==="flex"&&/第三則/.test(B.innerHTML)&&!/第一則/.test(B.innerHTML),"另一則的「⤢」顯示那一則");
B.onclick({target:B.querySelector("#mdBigBox")}); ok(B.style.display==="flex","點大視窗裡面 → 不關");
B.onclick({target:B}); ok(B.style.display!=="flex","點背景 → 關閉");

console.log(bad?"bad "+bad+" 項":"全部通過");
