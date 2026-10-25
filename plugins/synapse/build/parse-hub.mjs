// 解析中樞文件的「## Function」段落，產生 synapse 用的圖資料。
// 用法：node parse-hub.mjs hub.md   → 印出 JSON
//
// 規範（從 HackMD 匯入 synapse 時一律遵守；不符合就報錯，不猜）：
//   文件分兩部分：前面「## 情境」自由書寫；後面「## Function」用統一格式。
//   ### S編號 名稱
//   - 接在：中樞 或 S編號        只能用 ID 指向，不用名稱
//   - 用途：一句話               這個 Function 回答什麼問題
//   - 說明：這個 Function 做什麼
//   三個欄位都必填、順序固定。S 編號是 ID，不代表先後；刪掉的編號不回收。
//   之後接回 hive 時再加「神經元」「證據」欄位（目前也接受，選填）。
import { readFileSync } from 'node:fs';

const HEAD = /^###\s+(S\d+)\s+([^：:]+?)\s*(?:[：:]\s*(.*))?$/;

export function parseHub(md) {
  const text = md.replace(/<!--[\s\S]*?-->/g, '');
  const lines = text.split('\n');
  const first = lines.filter((l) => /^>\s*\*\*/.test(l)).map((l) => l.replace(/^>\s*\*\*|\*\*\s*$/g, '').trim());
  const start = lines.findIndex((l) => /^##\s+(Function|策略)\s*$/.test(l));
  const problems = [];
  if (start < 0) return { first, strategies: [], problems: ['找不到「## Function」段落'] };
  let end = lines.findIndex((l, i) => i > start && /^##\s+/.test(l));
  if (end < 0) end = lines.length;

  const strategies = [];
  let cur = null, field = null;
  for (const raw of lines.slice(start + 1, end)) {
    const h = raw.match(HEAD);
    if (h && h[2].trim()) {
      cur = { id: h[1], name: h[2].trim(), summary: (h[3] || '').trim(), parent: null, purpose: '', note: '', neurons: [], evidence: [], pending: false, order: [] };
      strategies.push(cur); field = null; continue;
    }
    if (/^###\s/.test(raw)) { problems.push(`標題不符規範（應為「### S編號 名稱」）：${raw.trim()}`); cur = null; continue; }
    if (!cur) continue;
    const f = raw.match(/^-\s*(接在|用途|說明|神經元|證據)[：:]\s*(.*)$/);
    if (f) {
      field = f[1];
      const v = f[2].trim();
      cur.order.push(field);
      if (field === '接在') cur.parent = v || null;
      if (field === '用途') cur.purpose = v;
      if (field === '說明') cur.note = v;
      if (field === '神經元') cur.neurons = v ? v.split(/[、,，]\s*/).map((s) => s.trim()).filter(Boolean) : [];
      if (field === '證據' && v) addEvidence(cur, v);
      continue;
    }
    const sub = raw.match(/^\s{2,}-\s*(.+)$/);
    if (sub && field === '證據') { addEvidence(cur, sub[1].trim()); continue; }
    if (raw.trim() && !/^\s/.test(raw)) { if (!/^[-*]\s/.test(raw)) problems.push(`${cur.id}（${cur.name}）有欄位以外的文字；說明請寫在「- 說明：」：${raw.trim().slice(0, 20)}`); field = null; }
  }

  const REQUIRED = ['接在', '用途', '說明'];
  for (const s of strategies) {
    const miss = REQUIRED.filter((k) => !s.order.includes(k));
    if (miss.length) problems.push(`${s.id}（${s.name}）缺少欄位：${miss.join('、')}`);
    const got = s.order.filter((k) => REQUIRED.includes(k));
    if (!miss.length && got.join() !== REQUIRED.join()) problems.push(`${s.id}（${s.name}）欄位順序應為 ${REQUIRED.join(' → ')}，目前是 ${got.join(' → ')}`);
    for (const k of ['用途', '說明']) if (s.order.includes(k) && !(k === '用途' ? s.purpose : s.note)) problems.push(`${s.id}（${s.name}）的「${k}」是空的`);
    delete s.order;
  }
  const count = new Map();
  for (const s of strategies) count.set(s.id, (count.get(s.id) || 0) + 1);
  for (const [id, c] of count) if (c > 1) problems.push(`${id} 重複了 ${c} 次；S 編號是 ID，不能重複`);
  for (const s of strategies) {
    if (!s.parent) problems.push(`${s.id}（${s.name}）沒有「接在」`);
    else if (s.parent !== '中樞') {
      if (!/^S\d+$/.test(s.parent)) problems.push(`${s.id}（${s.name}）的「接在」必須是「中樞」或 S 編號，不能寫名稱：${s.parent}`);
      else if (!count.has(s.parent)) problems.push(`${s.id}（${s.name}）接在不存在的 ${s.parent}`);
      else if (s.parent === s.id) problems.push(`${s.id}（${s.name}）接在自己身上`);
    }
  }
  // 「接在」繞成迴圈時，這幾條都接不回中樞
  const parentOf = Object.fromEntries(strategies.map((s) => [s.id, s.parent]));
  for (const s of strategies) {
    const seen = new Set(); let p = s.id;
    while (p && p !== '中樞' && !seen.has(p)) { seen.add(p); p = parentOf[p]; }
    if (p === s.id && s.parent !== s.id) problems.push(`${s.id}（${s.name}）的「接在」繞成迴圈，接不回中樞`);
  }
  return { first, strategies, problems };
}

function addEvidence(cur, v) {
  const url = v.match(/https?:\/\/\S+/);
  if (url) {
    const u = url[0];
    const kind = /\/releases\/tag\//.test(u) ? 'release' : /\/milestone(s)?\//.test(u) ? 'milestone' : /\/issues\/\d+/.test(u) ? 'epic' : 'other';
    cur.evidence.push({ kind, url: u });
  } else if (/待建立/.test(v)) cur.pending = true;
}

if (process.argv[1] && process.argv[1].endsWith('parse-hub.mjs')) {
  const md = readFileSync(process.argv[2] || 0, 'utf8');
  console.log(JSON.stringify(parseHub(md), null, 2));
}
