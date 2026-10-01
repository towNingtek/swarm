#!/usr/bin/env node
// 為一間公司開立 5 件「開辦任務」（本地任務）：
//   node .swarm/office-setup.mjs <公司代號>
// 只建立還不存在的檔案；已經有的（包含使用者改過的）一律不動。可以重複執行。
import { lstatSync, mkdirSync, openSync, writeSync, closeSync, constants } from 'node:fs';
import { join } from 'node:path';
import { ID } from './keys.mjs';

const hive = process.argv[2];
if (!hive || !ID.test(hive)) {
  console.log('用法：node .swarm/office-setup.mjs <公司代號>');
  process.exit(2);
}

function realDir(path) {
  const st = lstatSync(path, { throwIfNoEntry: false });
  if (st && !st.isDirectory()) {
    console.log(`✗ ${path} 不是資料夾（可能是連結），未建立任何任務`);
    process.exit(1);
  }
  return Boolean(st);
}

const base = join('hives', hive);
if (!realDir(base)) {
  console.log(`✗ 找不到公司 hives/${hive}/，請先用 hive-new 建立`);
  process.exit(1);
}
realDir(join(base, 'issues'));
const dir = join(base, 'issues', 'pm');
if (!realDir(dir)) mkdirSync(dir, { recursive: true });

const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Taipei' }).format(new Date());

function issue(title, goal, done, how) {
  return `# ${title}

狀態：待辦
建立：${today}　負責：pm

## 目標
${goal}

## 怎樣算完成
${done.map((d) => `- [ ] ${d}`).join('\n')}

## 做法
${how}

## 紀錄
- ${today} 建立（開辦任務）
`;
}

const TASKS = [
  ['001-office-rules.md', issue('寫好公司規則',
    '讓每個角色知道什麼不能碰、做事方式與語氣。',
    [`\`hives/${hive}/HIVE.md\` 有「禁止觸碰」與「操作範圍」兩段`, '使用者確認過內容'],
    `問使用者：有沒有不能碰的東西（正式資料、客戶個資、某些資料夾）？有沒有固定的做事方式或語氣？寫進 \`HIVE.md\` 後請他確認。`)],
  ['002-connect-github.md', issue('接上 GitHub（或決定只用本地任務）',
    '讓程式碼與任務能和團隊同步；沒有 repo 也可以決定只用本地任務。',
    [`\`node .swarm/key-test.mjs ${hive} github\` 成功，且 \`project.yaml\` 的 \`scm\` 已設定`,
     '或：使用者明確決定不接，紀錄寫下原因，`scm.type: none`'],
    `照 \`.dsh/skills/connect-github/SKILL.md\`。先問原本有沒有 token（交接）或要新申請；token 由使用者在終端機執行 \`node .swarm/set-key.mjs ${hive} github\` 自己放，不貼進對話。`)],
  ['003-connect-discord.md', issue('接上 Discord 通知',
    '排程做完、任務卡住時，AI 會通知人，不用一直上站看。',
    [`\`node .swarm/key-test.mjs ${hive} discord\` 成功`, '使用者確認頻道收到測試通知'],
    `照 \`.dsh/skills/connect-discord/SKILL.md\`。webhook 由使用者在終端機執行 \`node .swarm/set-key.mjs ${hive} discord\` 自己放，不貼進對話。`)],
  ['004-first-schedule.md', issue('設第一個排程',
    '讓 AI 定時自己做事，例如每個工作日早上整理待辦。',
    ['`node .swarm/check-schedules.mjs` 顯示「全部正確」且該排程「會執行」', '平台頁「排程」看得到它'],
    `見 \`AGENTS.md\`「排程」。有接 Discord 時，排程內容加一句「做完用 \`node .swarm/notify.mjs ${hive}\` 通知結果摘要」。`)],
  ['005-first-real-work.md', issue('第一件真正的工作',
    '用這個辦公室完成一件使用者真正需要的事。',
    ['使用者說出一件事，pm 開成一張新任務（006-…）', '做完並經使用者確認'],
    '問使用者現在最想交給辦公室的一件事，開成新任務，照「本地任務」流程完成。')],
];

const created = [], kept = [];
for (const [name, text] of TASKS) {
  let fd;
  try {
    fd = openSync(join(dir, name), constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o644);
  } catch (error) {
    if (error.code === 'EEXIST') { kept.push(name); continue; }
    throw error;
  }
  writeSync(fd, text);
  closeSync(fd);
  created.push(name);
}
if (created.length) console.log(`✓ 已建立 ${created.length} 件開辦任務：\n${created.map((n) => `  hives/${hive}/issues/pm/${n}`).join('\n')}`);
if (kept.length) console.log(`已經有、沒有變動：${kept.join('、')}`);
console.log(`\n看進度：node .swarm/check-office.mjs ${hive}`);
