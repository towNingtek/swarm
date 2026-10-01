#!/usr/bin/env node
// 設定金鑰（請使用者自己在「終端機」執行，AI 不經手金鑰）：
//   node .swarm/set-key.mjs <公司代號> github     這間公司專屬（hives/<公司>/.keys/）
//   node .swarm/set-key.mjs --shared github       整個辦公室共用（.swarm/keys/shared/）
//   種類：github（fine-grained token）、discord（webhook 網址）、hackmd（API token）
//   node .swarm/set-key.mjs <公司代號> github --remove
// 貼上時畫面不顯示；存成只有自己能讀的檔案，存好後立刻測試能不能用。
import { mkdirSync, lstatSync, openSync, writeSync, closeSync, renameSync, unlinkSync, constants } from 'node:fs';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';
import { KINDS, KIND_NAMES, SHARED, keysDir, checkDirs } from './keys.mjs';
import { testKey } from './key-test.mjs';

const [hive, kind, flag] = process.argv.slice(2);
if (!hive || !KINDS[kind]) {
  console.log(`用法：node .swarm/set-key.mjs <公司代號|--shared> ${KIND_NAMES} [--remove]`);
  process.exit(2);
}

function fail(message) {
  console.log(`✗ ${message}`);
  process.exit(1);
}

let dir;
try {
  dir = keysDir(hive);
} catch (error) {
  fail(error.message);
}
const target = join(dir, KINDS[kind].file);
function ensureDir() {
  // 每一層都要是真的資料夾；缺的層以 0700 建立（共用層是 .swarm/keys/shared 兩層）。
  const parts = dir.split('/');
  for (let i = 1; i <= parts.length; i += 1) {
    const path = parts.slice(0, i).join('/');
    const st = lstatSync(path, { throwIfNoEntry: false });
    if (st && !st.isDirectory()) fail(`${path} 不是資料夾（可能是連結），未儲存`);
    if (!st) mkdirSync(path, { mode: 0o700 });
  }
  try { checkDirs(dir); } catch (error) { fail(error.message); }
}

if (flag === '--remove') {
  try {
    try { checkDirs(dir); } catch (error) { fail(error.message); }
    unlinkSync(target);
    console.log(`✓ 已刪除 ${KINDS[kind].label}（${target}）。不再使用的話，記得也到發行的網站把它撤銷。`);
  } catch (error) {
    fail(error.code === 'ENOENT' ? '本來就沒有設定' : `刪除失敗（${error.code}）`);
  }
  process.exit(0);
}

function readHidden(prompt) {
  return new Promise((resolve) => {
    const input = process.stdin;
    if (!input.isTTY) {
      // 從管線讀（例如 xclip 貼上），不回顯。
      let data = '';
      input.setEncoding('utf8');
      input.on('data', (chunk) => { data += chunk; if (data.length > 8192) input.destroy(); });
      input.on('end', () => resolve(data.trim()));
      input.on('close', () => resolve(data.trim()));
      return;
    }
    process.stdout.write(prompt);
    input.setRawMode(true);
    input.setEncoding('utf8');
    let value = '';
    input.on('data', function onData(chunk) {
      for (const ch of chunk) {
        if (ch === '\r' || ch === '\n') {
          input.setRawMode(false);
          input.removeListener('data', onData);
          input.pause();
          process.stdout.write(value ? `（已收到 ${value.length} 個字元）\n` : '\n');
          resolve(value.trim());
          return;
        }
        if (ch === '\u0003') { process.stdout.write('\n已取消\n'); process.exit(130); }
        if (ch === '\u007f' || ch === '\b') { value = value.slice(0, -1); continue; }
        if (ch >= ' ' && value.length < 8192) value += ch;
      }
    });
  });
}

const value = await readHidden(`請貼上 ${KINDS[kind].label}（畫面不會顯示），按 Enter：`);
if (!value) fail('沒有收到內容，未儲存');
if (!KINDS[kind].valid(value)) fail(`格式不對，${KINDS[kind].hint}。未儲存`);

ensureDir();
const temporary = join(dir, `.${KINDS[kind].file}.${randomBytes(6).toString('hex')}.tmp`);
const fd = openSync(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
writeSync(fd, value + '\n');
closeSync(fd);
const existing = lstatSync(target, { throwIfNoEntry: false });
if (existing && !existing.isFile()) {
  unlinkSync(temporary);
  fail(`${target} 不是一般檔案，未儲存`);
}
renameSync(temporary, target);
console.log(`✓ 已儲存到 ${target}（只有你自己能讀）`);

const result = await testKey(hive, kind, { only: true });
console.log(result.ok ? `✓ 測試成功：${result.detail}` : `✗ 測試失敗：${result.detail}`);
process.exit(result.ok ? 0 : 1);
