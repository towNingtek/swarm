#!/usr/bin/env node
// 辦公室總覽：node .swarm/check-office.mjs [公司代號]
// 列出每間公司的任務進度、外部整合有沒有接（只看金鑰檔存不存在，不讀內容）、排程開關。
// 只讀檔，不修改任何東西。
import { readFileSync, readdirSync, lstatSync } from 'node:fs';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { ID, KINDS, hasKey } from './keys.mjs';

const require = createRequire(import.meta.url);
let YAML = null;
for (const where of ['yaml', '/usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/yaml']) {
  try { YAML = require(where); break; } catch { /* try next */ }
}

const STATES = ['待辦', '進行中', '完成', '擱置'];
const only = process.argv[2];

function read(path) {
  try {
    const st = lstatSync(path);
    if (!st.isFile() || st.size > 256 * 1024) return null;
    return readFileSync(path, 'utf8');
  } catch {
    return null;
  }
}

function listDir(path) {
  try {
    return lstatSync(path).isDirectory() ? readdirSync(path) : [];
  } catch {
    return [];
  }
}

export function parseIssue(text) {
  const title = (text.match(/^#\s+(.+)$/m) || [])[1] || '（沒有標題）';
  const state = (text.match(/^狀態[:：]\s*(\S+)/m) || [])[1];
  // 沒有「狀態：」行的是舊格式紀錄（例如從別處搬來的公司）：不猜它是否完成。
  if (state === undefined) return { title: title.trim(), state: null };
  return { title: title.trim(), state: STATES.includes(state) ? state : '待辦' };
}

function hives() {
  const text = read('.swarm/registry.yaml');
  if (text && YAML) {
    try {
      const data = YAML.parse(text) || {};
      if (Array.isArray(data.hives)) return data.hives.filter((h) => h && ID.test(String(h.id)));
    } catch { /* fall back to folders */ }
  }
  return listDir('hives').filter((id) => ID.test(id)).map((id) => ({ id }));
}

const LABEL = { github: 'GitHub', gitlab: 'GitLab', discord: 'Discord', hackmd: 'HackMD' };
const layer = (where) => (where === 'shared' ? '共用' : '公司專屬');
const shared = Object.keys(KINDS).filter((k) => hasKey(null, k));
if (shared.length) console.log(`共用金鑰（.swarm/keys/shared/）：${shared.map((k) => LABEL[k] || k).join('、')}`);

const all = hives().filter((h) => !only || h.id === only);
if (!all.length) {
  console.log(only ? `找不到公司 ${only}` : '還沒有任何公司。先建立第一間公司（hive-new）。');
  process.exit(0);
}

for (const hive of all) {
  const base = join('hives', hive.id);
  // Without a YAML parser the company settings are unknown: say so rather than guess.
  const project = YAML ? (() => { try { return YAML.parse(read(join(base, 'project.yaml')) || '') || {}; } catch { return {}; } })() : null;
  const name = project?.project?.name || hive.id;
  console.log(`\n■ ${name}（${hive.id}）  排程總開關：${hive.enabled === true ? '開' : '關'}`);

  const scm = project === null ? null : (project?.scm?.type || 'none');
  const github = hasKey(hive.id, 'github');
  const discord = hasKey(hive.id, 'discord');
  if (scm === 'gitlab') {
    const gitlab = hasKey(hive.id, 'gitlab');
    console.log(`  GitLab：${gitlab ? `已接（${layer(gitlab)}）` : '設定要用，但還沒放 token'}`);
  }
  const githubText = scm === null ? (github ? `已放 token（${layer(github)}）` : '未放 token')
    : scm === 'github' ? (github ? `已接（${layer(github)}）` : '設定要用，但還沒放 token')
    : scm === 'gitlab' ? (github ? `也有 token（${layer(github)}）` : null)
    : (github ? `有 token（${layer(github)}；project.yaml 尚未設 scm）` : '未接（任務只記在本地）');
  if (githubText) console.log(`  GitHub：${githubText}`);
  console.log(`  Discord 通知：${discord ? `已接（${layer(discord)}）` : '未接'}`);
  const hackmd = hasKey(hive.id, 'hackmd');
  if (hackmd) console.log(`  HackMD：已接（${layer(hackmd)}）`);

  const open = [];
  let done = 0, parked = 0, legacy = 0;
  for (const role of listDir(join(base, 'issues')).sort()) {
    for (const file of listDir(join(base, 'issues', role)).filter((f) => f.endsWith('.md')).sort()) {
      const text = read(join(base, 'issues', role, file));
      if (text === null) continue;
      const issue = parseIssue(text);
      if (issue.state === null) legacy++;
      else if (issue.state === '完成') done++;
      else if (issue.state === '擱置') parked++;
      else open.push(`${issue.state === '進行中' ? '▶' : '○'} [${role}] ${file.replace(/\.md$/, '')}：${issue.title}`);
    }
  }
  console.log(`  任務：未完成 ${open.length}、完成 ${done}、擱置 ${parked}`
    + (legacy ? `；另有 ${legacy} 份舊紀錄沒有「狀態：」行（不列入）` : ''));
  // 已經在運作的公司（有舊紀錄）不催開辦任務；使用者要的話可以自己跑 office-setup。
  if (!legacy && !read(join(base, 'issues', 'pm', '001-office-rules.md'))) {
    console.log(`  ⚠ 還沒開辦任務 → 執行 node .swarm/office-setup.mjs ${hive.id}`);
  }
  for (const line of open.slice(0, 15)) console.log(`    ${line}`);
  if (open.length > 15) console.log(`    …還有 ${open.length - 15} 件`);
}
console.log('\n排程細節：node .swarm/check-schedules.mjs');
