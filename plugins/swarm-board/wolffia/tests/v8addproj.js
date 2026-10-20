// 站台看板「＋ 加入 project…」：打開清單（GET api/projects/available）、搜尋框篩名稱或組織、已加入的打勾不能點、
// 點一筆送出 POST api/projects/add {owner, number}，完成後選單出現它並切過去；不再跳 alert 叫人改主機的檔案。
// 和 v8multiorg 同方式：node 假 DOM＋假的 XHR 跑 v8.html；資料是這支測試自己產生的假資料。
const fs=require("fs"),os=require("os"),path=require("path"),dir=__dirname+"/../";
const h=fs.readFileSync(dir+"v8.html","utf8"); const re=/<script>([\s\S]*?)<\/script>/g; let m; const inl=[]; while((m=re.exec(h))) inl.push(m[1]);
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};

const D=fs.mkdtempSync(path.join(os.tmpdir(),"wolffia-v8addproj-"))+"/";
const NOWS="2026-10-20T00:00:00Z";
function board(owner,num,key,title){
  const items=[1,2,3].map(i=>({n:num*100+i,t:title+" #"+i,u:"https://github.com/"+owner+"/r/issues/"+(num*100+i),repo:"r",owner,
    state:"OPEN",created:"2026-10-0"+i+"T00:00:00Z",closed:null,status:"待辦",since:"2026-10-0"+i+"T00:00:00Z",hist:[],who:[],labels:[],
    type:null,parent:null,blockedBy:[],target:null}));
  return {num,owner,key,cols:["待辦","進行中 (7 天一輪)","審核後請關閉","完成"],title,url:"https://github.com/orgs/"+owner+"/projects/"+num,fetched:NOWS,items};
}
fs.writeFileSync(D+"p2.js","window.PROJECTS=window.PROJECTS||{};window.PROJECTS[2]="+JSON.stringify(board("acme",2,2,"TI 二號"))+";\n");
const row=(num,owner,key,title)=>({num,owner,key,title,open:3,total:3});
fs.writeFileSync(D+"index.js","window.PROJECT_INDEX="+JSON.stringify({default:2,projects:[row(2,"acme",2,"TI 二號")]})+";\n");

const AVAIL={github:true,projects:[
  {owner:"acme",number:1,title:"TI 一號",items:30,added:false},
  {owner:"Other",number:1,title:"Bright 一號",items:20,added:false},
  {owner:"acme",number:2,title:"TI 二號",items:10,added:true},
  {owner:"Alice",number:5,title:"自己的看板",items:4,added:false}]};
const NEWINDEX={default:2,projects:[row(2,"acme",2,"TI 二號"),row(1,"other","other/1","Bright 一號")]};

let SENT=[], GETS=[], ALERTS=[], RESP={};
function boot(){
  SENT=[]; GETS=[]; ALERTS=[];
  const st={}; const ctx=new Proxy({},{get:(o,k)=>k==="createRadialGradient"?(()=>({addColorStop(){}})):()=>{}});
  const mk=id=>st[id]||(st[id]={id,style:{},classList:{add(){},remove(){},contains(){return false}},addEventListener(t,f){(this.L=this.L||{})[t]=f},getContext:()=>ctx,clientWidth:1200,clientHeight:720,
    getBoundingClientRect(){return{left:0,top:0,width:1200,height:720}},getAttribute(k){return this[k]},querySelector(){return{onclick:null}},querySelectorAll(){return []},appendChild(){},innerHTML:"",textContent:"",value:"0"});
  const WL={}, WA={}; global.window={devicePixelRatio:1,open(){return {}},addEventListener(t,f){(WA[t]=WA[t]||[]).push(f); WL[t]=e=>WA[t].forEach(g=>g(e));}}; global.addEventListener=global.window.addEventListener;
  global.localStorage={getItem:()=>null,setItem(){}}; global.setTimeout=()=>0; global.clearTimeout=()=>0; global.location={search:"",pathname:"/board/"};
  global.confirm=()=>true; global.alert=s=>ALERTS.push(s);
  global.XMLHttpRequest=function(){ this.open=(mm,u)=>{this.m=mm;this.u=u}; this.setRequestHeader=()=>{};
    this.send=b=>{ const r=RESP[this.m+" "+this.u];
      if(this.m==="POST") SENT.push({u:this.u,body:JSON.parse(b)}); else GETS.push(this.u);
      if(r){ this.status=r[0]; this.responseText=JSON.stringify(r[1]); this.onload&&this.onload(); } }; };
  global.document={hidden:false,addEventListener(){},getElementById:mk,querySelector:()=>({onclick:null}),querySelectorAll:()=>[],createElement:()=>mk("x"+Math.random()),
    write(s){ const f=s.match(/data\/([\w-]+\.js)/)[1]; if(fs.existsSync(D+f)) new Function(fs.readFileSync(D+f,"utf8"))(); }};
  let T=0,F=null; global.performance={now:()=>T}; global.requestAnimationFrame=f=>{F=f};
  new Function(fs.readFileSync(D+"index.js","utf8"))();
  inl.forEach(s=>new Function(s)());
  const tick=n=>{for(let k=0;k<n;k++){T+=40;F(T);}}; tick(10);
  return {st};
}
// 清單裡第 i 列（data-i）的假 click target：點到列裡面的文字也會往上找到列
function rowTarget(L,html,title){
  const mm=new RegExp('<button[^>]*data-i="(\\d+)"([^>]*)>(?:(?!</button>).)*'+title).exec(html); if(!mm) return null;
  const btn={getAttribute:k=>k==="data-i"?mm[1]:(k==="disabled"&&/disabled/.test(mm[2])?"":null),disabled:/disabled/.test(mm[2]),parentNode:L};
  return {getAttribute:()=>null,parentNode:btn};
}
const openList=st=>{ st.pSel.value="+"; st.pSel.onchange.call(st.pSel); };

// 1. 選「＋ 加入 project…」打開清單，不跳 alert
RESP={"GET api/projects/available":[200,AVAIL],"POST api/projects/add":[200,{ok:true,key:"other/1",already:false,index:NEWINDEX}]};
{ const {st}=boot();
  ok(/<option value="\+">＋ 加入 project…<\/option>/.test(st.pSel.innerHTML),"選單有「＋ 加入 project…」");
  openList(st);
  ok(ALERTS.length===0,"沒有 alert");
  ok(st.apBox.style.display==="flex","選「＋ 加入 project…」打開清單");
  ok(GETS.includes("api/projects/available"),"打開時問 api/projects/available");
  ok(st.pSel.value==="2","選單留在原來的 project（"+st.pSel.value+"）");
  const html=st.apList.innerHTML;
  ok(["TI 一號","Bright 一號","TI 二號","自己的看板"].every(t=>html.includes(t)),"清單列出全部 4 個");
  ok(html.indexOf("TI 一號")<html.indexOf("Bright 一號")&&html.indexOf("Bright 一號")<html.indexOf("TI 二號")&&html.indexOf("TI 二號")<html.indexOf("自己的看板"),"照後端給的順序（卡片數由多到少）");
  ok(/30 張/.test(html),"每列顯示卡片數");

  // 2. 已加入的打勾、不能點
  const added=/<button[^>]*data-i="2"[^>]*>(?:(?!<\/button>).)*<\/button>/.exec(html)[0];
  ok(/disabled/.test(added)&&/✓/.test(added),"已加入的（TI 二號）打勾、disabled");
  ok(!/disabled/.test(/<button[^>]*data-i="0"[^>]*>/.exec(html)[0]),"沒加入的可以點");
  SENT=[]; st.apList.onclick({target:rowTarget(st.apList,html,"TI 二號")});
  ok(SENT.length===0,"點已加入的不送出");
  window.__addProj.pick(AVAIL.projects[2]); ok(SENT.length===0,"pick 已加入的也不送出");

  // 3. 搜尋框篩選：名稱、組織
  st.apQ.value="bright"; st.apQ.oninput();
  ok(st.apList.innerHTML.includes("Bright 一號")&&!st.apList.innerHTML.includes("TI 一號")&&!st.apList.innerHTML.includes("自己的看板"),"搜尋「bright」只剩 Bright 一號");
  st.apQ.value="acme"; st.apQ.oninput();
  ok(st.apList.innerHTML.includes("TI 一號")&&st.apList.innerHTML.includes("TI 二號")&&!st.apList.innerHTML.includes("Bright"),"搜尋組織「acme」只剩它的");
  st.apQ.value="自己"; st.apQ.oninput();
  ok(st.apList.innerHTML.includes("自己的看板")&&!st.apList.innerHTML.includes("TI"),"搜尋名稱「自己」");
  st.apQ.value="沒有這個"; st.apQ.oninput();
  ok(/沒有符合/.test(st.apList.innerHTML),"沒有符合時說沒有");
  st.apQ.value="Bright"; st.apQ.oninput();

  // 4. 點列＝勾選（立刻打勾、還沒送出）；可多選；再點一次取消
  SENT=[]; st.apQ.value=""; st.apQ.oninput();
  ok(st.apGo.disabled===true,"還沒勾選時「加入」不能按");
  st.apList.onclick({target:rowTarget(st.apList,st.apList.innerHTML,"Bright 一號")});
  ok(SENT.length===0,"點一列不會馬上送出");
  const r1=/<button[^>]*data-i="1"[^>]*>(?:(?!<\/button>).)*<\/button>/.exec(st.apList.innerHTML)[0];
  ok(/class="apRow on"/.test(r1)&&/✓/.test(r1),"點了立刻打勾");
  ok(st.apGo.disabled===false&&st.apGo.textContent==="加入 1 個","按鈕顯示「加入 1 個」");
  st.apList.onclick({target:rowTarget(st.apList,st.apList.innerHTML,"自己的看板")});
  ok(st.apGo.textContent==="加入 2 個","多選：「加入 2 個」");
  st.apList.onclick({target:rowTarget(st.apList,st.apList.innerHTML,"自己的看板")});
  ok(st.apGo.textContent==="加入 1 個","再點一次取消勾選");
  st.apQ.value="town"; st.apQ.oninput(); st.apQ.value=""; st.apQ.oninput();
  ok(/class="apRow on"[^>]*data-i="1"/.test(st.apList.innerHTML),"搜尋篩選後勾選還在");

  // 5. 按「加入」→ 逐一送出 {owner, number}，全部完成後選單出現它並切過去
  st.apGo.onclick();
  ok(SENT.length===1&&SENT[0].u==="api/projects/add","按「加入」→ POST api/projects/add");
  ok(SENT[0]&&JSON.stringify(SENT[0].body)===JSON.stringify({owner:"Other",number:1}),"送出 {owner, number}："+JSON.stringify(SENT[0]&&SENT[0].body));
  ok(/<option value="other\/1" selected>Bright 一號/.test(st.pSel.innerHTML),"選單出現新的 project 並選中");
  ok(location.search==="?p=other/1","切過去（location.search="+location.search+"）");
  ok(st.apBox.style.display==="none","清單關掉");
  ok(window.PROJECT_INDEX===NEWINDEX||window.PROJECT_INDEX.projects.length===2,"用回來的 index");
}

// 4b. 一次勾兩個 → 兩個都送出（一個接一個）
RESP={"GET api/projects/available":[200,AVAIL],"POST api/projects/add":[200,{ok:true,key:"other/1",already:false,index:NEWINDEX}]};
{ const {st}=boot(); openList(st);
  st.apList.onclick({target:rowTarget(st.apList,st.apList.innerHTML,"TI 一號")});
  st.apList.onclick({target:rowTarget(st.apList,st.apList.innerHTML,"Bright 一號")});
  st.apGo.onclick();
  ok(SENT.length===2&&SENT.map(x=>x.body.owner+"/"+x.body.number).join(",")==="acme/1,Other/1","兩個都送出："+SENT.map(x=>x.body.owner+"/"+x.body.number).join(","));
}

// 5. 加入失敗：顯示錯誤、不切換
RESP={"GET api/projects/available":[200,AVAIL],"POST api/projects/add":[400,{error:"不在可加入的 project 清單裡"}]};
{ const {st}=boot(); openList(st);
  st.apList.onclick({target:rowTarget(st.apList,st.apList.innerHTML,"TI 一號")}); st.apGo.onclick();
  ok(SENT.length===1&&SENT[0].body.owner==="acme"&&SENT[0].body.number===1,"送出 acme 1");
  ok(/加入失敗/.test(st.apMsg.textContent)&&/不在可加入/.test(st.apMsg.textContent),"失敗時顯示錯誤："+st.apMsg.textContent);
  ok(location.search==="","失敗不切換");
  ok(st.apBox.style.display==="flex","清單留著"); }

// 6. 站台 gh 沒登入：說怎麼連接
RESP={"GET api/projects/available":[200,{github:false}]};
{ const {st}=boot(); openList(st);
  ok(/尚未連接 GitHub/.test(st.apMsg.textContent),"github:false → 提示連接 GitHub");
  ok(!/apRow" data-i/.test(st.apList.innerHTML),"沒有可點的列"); }

// 7. 舊的說明拿掉了
ok(!h.includes("docs/wolffia/projects.json")&&!h.includes("跟拖曳改狀態一起做"),"v8.html 沒有舊的 alert 說明");

fs.rmSync(D,{recursive:true,force:true});
console.log(bad?"bad "+bad:"全部通過");
process.exit(bad?1:0);
