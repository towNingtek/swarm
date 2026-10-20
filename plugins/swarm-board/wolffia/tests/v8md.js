// 分析回答的 Markdown 排版：v8.html 的 mdRender(text) 先 esc() 全部文字（回答裡的 HTML 不生效），再轉表格／清單／code／程式碼區塊／粗體／標題；
// 連結只留文字，不產生 <a href>。從 v8.html 抽出 esc 與 mdRender 兩個函式直接跑。
const fs=require("fs"),h=fs.readFileSync(__dirname+"/../v8.html","utf8");
let bad=0; const ok=(c,msg)=>{console.log((c?"ok  ":"bad ")+msg); if(!c) bad++;};
function grab(name){ const i=h.indexOf("function "+name+"("); if(i<0) return "";
  let d=0,j=h.indexOf("{",i); for(;j<h.length;j++){ if(h[j]==="{") d++; else if(h[j]==="}"&&--d===0) break; } return h.slice(i,j+1); }
const src=grab("esc")+"\n"+grab("mdRender");
ok(/function mdRender/.test(src),"v8.html 有 function mdRender");
const mdRender=new Function(src+"\nreturn mdRender;")();
ok(/r\.text\?mdRender\(r\.text\)/.test(h),"分析回答（awaitReply）用 mdRender 顯示");
ok(/\.mdWrap\{[^}]*overflow-x:auto/.test(h)&&/\.mdTable\{[^}]*font-size:11px/.test(h),"表格外框可橫向捲動、字小一號");

// 表格
let o=mdRender("看結果：\n\n| 卡 | 狀態 | 原因 |\n|---|:---:|---|\n| #12 | 卡住 | 等 **review** |\n| #13 | 完成 |\n\n結束");
ok(/<table class="mdTable">/.test(o),"表格 → <table class=\"mdTable\">");
ok((o.match(/<th>/g)||[]).length===3,"表頭 3 欄");
const trs=o.split("<tbody>")[1]||""; const rows=trs.split("<tr>").slice(1);
ok(rows.length===2&&rows.every(r=>(r.match(/<td>/g)||[]).length===3),"每列 3 欄（少的補空格）");
ok(/<td>等 <b>review<\/b><\/td>/.test(o),"表格格子內粗體");
ok(!/\|---/.test(o)&&!/\| #12/.test(o),"不再出現一堆 |");
ok(/看結果/.test(o)&&/結束/.test(o),"表格前後的文字保留");
o=mdRender("a|b\n--|--\n1|2");
ok(/<table class="mdTable">/.test(o)&&(o.match(/<td>/g)||[]).length===2,"沒有外框 | 的表格也認得");

// 清單
o=mdRender("- 一\n- 二\n* 三");
ok(/<ul[^>]*><li>一<\/li><li>二<\/li><li>三<\/li><\/ul>/.test(o),"- / * 無序清單 → <ul>");
o=mdRender("1. 甲\n2. 乙\n3. 丙");
ok(/<ol[^>]*><li>甲<\/li><li>乙<\/li><li>丙<\/li><\/ol>/.test(o),"1. 有序清單 → <ol>");
o=mdRender("- 一\n\n1. 甲");
ok(/<\/ul>.*<ol/.test(o),"無序與有序分開");

// code
o=mdRender("執行 `npm test` 看看");
ok(/執行 <code>npm test<\/code> 看看/.test(o),"行內 code → <code>");
o=mdRender("`**不是粗體**`");
ok(/<code>\*\*不是粗體\*\*<\/code>/.test(o),"code 內不轉粗體");
o=mdRender("前\n```js\nif(a<b){ x=\"**y**\"; }\n- 不是清單\n```\n後");
ok(/<pre[^>]*><code>if\(a&lt;b\)\{ x=&quot;\*\*y\*\*&quot;; \}\n- 不是清單<\/code><\/pre>/.test(o),"``` 區塊 → <pre>，內容跳脫、不轉 Markdown、保留換行");
ok(/前/.test(o)&&/後/.test(o),"區塊前後文字保留");

// 粗體、標題、段落
o=mdRender("這是 **重點** 喔");
ok(/這是 <b>重點<\/b> 喔/.test(o),"**粗體** → <b>");
for(const hh of ["# 一級","## 二級","### 三級"]){ o=mdRender(hh); ok(/<b>[一二三]級<\/b>/.test(o)&&!/#/.test(o),hh.split(" ")[0]+" 標題 → <b>"); }
o=mdRender("第一行\n第二行\n\n第二段");
ok(/第一行<br>第二行/.test(o)&&(o.match(/class="mdP"/g)||[]).length===2,"段落內換行保留（<br>）、空行分段");

// 安全
o=mdRender("<script>alert(1)</script>");
ok(!/<script/i.test(o)&&/&lt;script&gt;alert\(1\)&lt;\/script&gt;/.test(o),"<script> 只當文字（被跳脫）");
o=mdRender('<img src=x onerror="alert(1)">');
ok(!/<img/i.test(o)&&/&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/.test(o),"<img onerror> 只當文字（被跳脫）");
o=mdRender("| a | b |\n|---|---|\n| <img src=x onerror=alert(1)> | `<b>` |");
ok(!/<img/i.test(o)&&/<code>&lt;b&gt;<\/code>/.test(o),"表格／code 裡的 HTML 也被跳脫");
o=mdRender("點 [這裡](javascript:alert(1)) 或 [文件](https://example.com)");
ok(!/<a/i.test(o)&&!/javascript:/i.test(o)&&/點 這裡 或 文件/.test(o),"[文字](javascript:…) 不產生連結、只留文字");
o=mdRender("**<i>x</i>**");
ok(/<b>&lt;i&gt;x&lt;\/i&gt;<\/b>/.test(o),"粗體內的 HTML 也被跳脫");
ok(mdRender("")===""&&typeof mdRender(null)==="string","空字串／null 不出錯");

console.log(bad?"bad "+bad+" 項":"全部通過");
