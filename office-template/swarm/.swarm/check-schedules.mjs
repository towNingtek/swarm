#!/usr/bin/env node
// 檢查排程設定：node .swarm/check-schedules.mjs
// 讀 .swarm/registry.yaml 與各公司的 hives/{id}/project.yaml，
// 用和平台相同的規則判斷每個排程會不會執行。只讀檔，不修改任何東西。
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
let YAML;
for (const where of ['yaml', '/usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/yaml']) {
  try { YAML = require(where); break; } catch { /* try next */ }
}
if (!YAML) {
  console.log('找不到 YAML 解析器，無法檢查；請使用者到平台頁「排程」確認。');
  process.exit(2);
}

const ID = /^[a-z0-9][a-z0-9_-]{0,62}$/;
const META = new Set(['model', 'enabled', 'heartbeat']);
const DOW = new Set(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']);
const LIMITS = [[0, 59], [0, 23], [1, 31], [1, 12]];
let problems = 0;

function load(path) {
  if (!existsSync(path)) return { missing: true };
  try {
    return { data: YAML.parse(readFileSync(path, 'utf8')) };
  } catch (error) {
    problems++;
    const line = error.linePos && error.linePos[0] ? `第 ${error.linePos[0].line} 行` : '';
    console.log(`✗ ${path} 不是正確的 YAML ${line}：${String(error.message).split('\n')[0]}`);
    console.log('  → 最常見原因是縮排錯：排程那行要和同一角色的 `model:` 對齊（同樣的空格數）。');
    return { broken: true };
  }
}

function cronProblem(expr) {
  const parts = String(expr).trim().split(/\s+/);
  if (parts.length !== 5) return '要剛好 5 個欄位：分 時 日 月 星期';
  for (let i = 0; i < 5; i++) {
    for (const piece of parts[i].split(',')) {
      const [base, step] = piece.split('/');
      if (step !== undefined && !/^\d+$/.test(step)) return `第 ${i + 1} 欄的間隔不正確`;
      if (base === '*') continue;
      if (i === 4) {
        if (base.split('-').some((d) => !DOW.has(d))) return '星期請用 mon…sun，不要用數字';
        continue;
      }
      const [low, high] = LIMITS[i];
      for (const end of base.split('-')) {
        if (!/^\d+$/.test(end) || +end < low || +end > high) return `第 ${i + 1} 欄超出範圍 ${low}-${high}`;
      }
    }
  }
  return null;
}

const registry = load('.swarm/registry.yaml');
if (registry.missing) { console.log('✗ 找不到 .swarm/registry.yaml'); process.exit(1); }
if (registry.broken) process.exit(1);
const hives = Array.isArray(registry.data) ? registry.data : (registry.data || {}).hives || [];
if (!hives.length) console.log('還沒有任何公司。');

for (const hive of hives) {
  if (!hive || !ID.test(String(hive.id || ''))) { problems++; console.log('✗ registry 裡有一筆公司缺少正確的 id'); continue; }
  const id = hive.id;
  if (!existsSync(`hives/${id}`)) { problems++; console.log(`✗ ${id}：registry 有登記，但 hives/${id}/ 不存在`); continue; }
  const project = load(`hives/${id}/project.yaml`);
  if (project.broken) continue;
  const reports = (project.data && typeof project.data.reports === 'object' && project.data.reports) || {};
  const on = hive.enabled === true;
  console.log(`公司 ${id}：排程總開關 ${on ? '開' : '關（enabled 不是 true，全部不會執行）'}`);
  const timed = new Set();
  for (const [role, body] of Object.entries(hive.agents || {})) {
    if (!body || typeof body !== 'object') continue;
    for (const [name, cron] of Object.entries(body)) {
      if (META.has(name)) continue;
      timed.add(name);
      if (!ID.test(name)) { problems++; console.log(`  ✗ ${role}.${name}：代號只能用英文小寫、數字、_ 或 -`); continue; }
      const bad = typeof cron === 'string' ? cronProblem(cron) : '時間要寫成字串，例如 "0 9 * * *"';
      if (bad) { problems++; console.log(`  ✗ ${name}（${role}）時間 ${JSON.stringify(cron)}：${bad}`); continue; }
      if (!(name in reports)) { problems++; console.log(`  ✗ ${name}（${role}）：project.yaml 的 reports: 沒有同名定義，不會執行`); continue; }
      const ready = on && body.enabled !== false;
      if (!ready) problems++;
      console.log(`  ${ready ? '✓' : '✗'} ${name}（${role}）"${cron}" → ${ready ? '會執行' : '不會執行：公司或角色的排程沒開'}`);
    }
  }
  for (const name of Object.keys(reports)) {
    if (!timed.has(name)) { problems++; console.log(`  ✗ ${name}：project.yaml 有定義，但 registry 沒有時間，不會執行`); }
  }
}
console.log(problems ? `\n有 ${problems} 個問題，請修正後再執行一次本檢查。` : '\n全部正確。');
process.exit(problems ? 1 : 0);
