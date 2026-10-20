// 分析回答裡的 #編號連到看板上的卡：v8.html 的 isnLink(mdRender(text)) 把看板上有的 #n 包成 <span class="isn" data-n>；
// 滑過亮起（ISNH）、點一下選取並聚焦（setSel＋fitSel）、雙擊開 GitHub（新分頁 noopener）；window.WOLFFIA.focus(n)。
// 從 v8.html 抽出相關函式，配上假的 ITEMS／setSel／fitSel／window.open 直接跑。
const fs=require("fs"),h=fs.readFileSync(__dirname+"/../v8.html","utf8");
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};
function grab(name){ const i=h.indexOf("function "+name+"("); if(i<0) return "";
  let d=0,j=h.indexOf("{",i); for(;j<h.length;j++){ if(h[j]==="{") d++; else if(h[j]==="}"&&--d===0) break; } return h.slice(i,j+1); }
const names=["esc","mdRender","isnFind","isnLink","isnOf","isnFocus","isnAct"];
names.forEach(n=>ok(grab(n),"v8.html 有 function "+n));
const ITEMS=[
  {n:12,u:"https://github.com/o/swarm/issues/12",repo:"o/swarm",vis:true},
  {n:91,u:"https://github.com/o/swarm/issues/91",repo:"o/swarm",vis:true},
  {n:91,u:"https://github.com/o/webapp/issues/91",repo:"o/webapp",vis:true},
  {n:7,u:"https://github.com/o/swarm/issues/7",repo:"o/swarm",vis:true}];
const calls=[], win={open:(...a)=>calls.push(["open",...a])};
const env={ITEMS,SEL:{},setSel:(l,m)=>calls.push(["setSel",l.map(x=>x.u),m]),fitSel:()=>{calls.push(["fitSel"]); return true;},window:win};
const F=new Function(...Object.keys(env),"var ISNH=null;"+names.map(grab).join("\n")+
  "\nreturn {mdRender,isnLink,isnFocus,isnAct,hov:function(){return ISNH;}};")(...Object.values(env));
const R=t=>F.isnLink(F.mdRender(t));

let o=R("先做 #12，再看 #999");
ok(/<span class="isn" data-n="12" data-u="https:\/\/github.com\/o\/swarm\/issues\/12">#12<\/span>/.test(o),"看板上有的 #12 被包成 data-n");
ok(!/data-n="999"/.test(o)&&/#999/.test(o),"看板上沒有的 #999 不包、文字保留");
o=R("a#12 #123 #12a x");
ok(/^<p class="mdP">a#12 /.test(o),"前面是字母的 a#12 不包");
ok(!/data-n="123"/.test(o)&&!/data-n="12">#12<\/span>3/.test(o),"#123 不會被當成 #12（後面是數字）");
ok(/>#12<\/span>a/.test(o),"#12a：後面是字母仍包");
o=R("行內 `#12` 與區塊\n```\nfix #7\n```\n外面 #7");
ok(!/<code>[^<]*<span/.test(o)&&/<code>#12<\/code>/.test(o),"行內 code 裡的 #12 不包");
ok(/<pre[^>]*><code>fix #7<\/code><\/pre>/.test(o),"``` 區塊裡的 #7 不包");
ok(/外面 <span class="isn" data-n="7"/.test(o),"區塊外的 #7 照包");
o=R("**#12** 與 | 卡 |\n|---|\n| #7 |");
ok(/<b><span class="isn" data-n="12"/.test(o)&&/<td><span class="isn" data-n="7"/.test(o),"粗體、表格裡的 #編號也包");
o=R("webapp #91 與 swarm #91 與 o/webapp #91");
const us=(o.match(/data-u="[^"]+"/g)||[]);
ok(us.length===3&&/webapp/.test(us[0])&&/\/swarm\//.test(us[1])&&/webapp/.test(us[2]),"repo #n 選對 repo（webapp #91 → webapp 那張）");
o=R("只寫 #91");
ok(/data-n="91"/.test(o),"同號多張、沒寫 repo 時仍包（選第一張）");
o=R('<a href="#12">');
ok(!/<a /.test(o),"HTML 仍被跳脫，不產生標籤");

// 互動
const el=(n,u)=>({getAttribute:k=>k==="data-n"?String(n):k==="data-u"?u:null});
const e91=el(91,"https://github.com/o/webapp/issues/91");
F.isnAct("over",e91); ok(F.hov()&&F.hov().u===e91.getAttribute("data-u"),"滑過：該卡進入 hover 高亮（ISNH）");
F.isnAct("out",e91); ok(F.hov()===null,"移開：取消高亮");
calls.length=0; F.isnAct("click",e91);
ok(calls[0]&&calls[0][0]==="setSel"&&calls[0][1].join()===e91.getAttribute("data-u")&&calls[0][2]==="set","點擊後該卡被選取（setSel set）");
ok(calls.some(c=>c[0]==="fitSel"),"點擊後呼叫聚焦（fitSel）");
calls.length=0; F.isnAct("dbl",e91);
ok(calls.length===1&&calls[0][1]==="https://github.com/o/webapp/issues/91"&&calls[0][2]==="_blank"&&/noopener/.test(calls[0][3]),"雙擊開新分頁到該卡網址（noopener）");
calls.length=0;
ok(F.isnFocus(12)===true&&calls.some(c=>c[0]==="setSel")&&calls.some(c=>c[0]==="fitSel"),"focus(12) 找到 → 選取＋聚焦、回 true");
calls.length=0;
ok(F.isnFocus(999)===false&&calls.length===0,"focus(999) 找不到回 false、不動選取");

// 接線
ok(/focus:function\(n\)\{ return isnFocus\(n\); \}/.test(h),"WOLFFIA.focus(n) 接到 isnFocus");
ok(/isnLink\(r\.text\?mdRender\(r\.text\)/.test(h),"分析回答用 isnLink(mdRender(...)) 顯示");
ok(/SEL\[it\.u\]\|\|ISNH===it/.test(h),"星系畫面：hover 中的卡沿用選取外框");
ok(/addEventListener\(p\[0\]/.test(h)&&/"dblclick","dbl"/.test(h),"文件上掛滑過／點擊／雙擊事件");

console.log(bad?"bad "+bad+" 項":"全部通過");
