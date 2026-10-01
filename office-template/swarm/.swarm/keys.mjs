// 金鑰的共用規則（set-key、notify、git-credential、check-office 共用）。
// 兩層，和 Swarm 母站相同：
//   .swarm/keys/shared/    整個辦公室共用（每間公司都能用）
//   hives/{公司}/.keys/    這間公司專屬；有的話優先用這層
// 權限只有自己能讀；這裡的程式只回報「有沒有、在哪一層」，不印出內容。
import { lstatSync, openSync, readSync, closeSync, fstatSync, constants } from 'node:fs';
import { join } from 'node:path';

export const ID = /^[a-z0-9][a-z0-9_-]{0,62}$/;

export const KINDS = {
  github: {
    file: 'github.token',
    label: 'GitHub token',
    // fine-grained（github_pat_）優先；也接受 classic（ghp_），但建議不要用。
    valid: (v) => /^(github_pat_[A-Za-z0-9_]{40,255}|ghp_[A-Za-z0-9]{36,255})$/.test(v),
    hint: '應該是 github_pat_ 開頭（fine-grained token）',
  },
  gitlab: {
    file: 'gitlab.token',
    label: 'GitLab token',
    valid: (v) => /^glpat-[A-Za-z0-9_.-]{20,255}$/.test(v),
    hint: '應該是 glpat- 開頭（GitLab personal／project access token）',
  },
  discord: {
    file: 'discord-webhook.url',
    label: 'Discord webhook',
    valid: (v) => /^https:\/\/(discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)\/api\/webhooks\/\d{5,25}\/[A-Za-z0-9_-]{20,128}$/.test(v),
    hint: '應該像 https://discord.com/api/webhooks/數字/一串英數字',
  },
  hackmd: {
    file: 'hackmd.token',
    label: 'HackMD API token',
    valid: (v) => /^[A-Za-z0-9]{20,128}$/.test(v),
    hint: '應該是 HackMD「設定 → API」產生的一串英數字',
  },
};

export const KIND_NAMES = Object.keys(KINDS).join('|');

// 在命令列用 --shared 代替公司代號，表示共用層。
export const SHARED = '--shared';
export const SHARED_DIR = join('.swarm', 'keys', 'shared');

// 金鑰資料夾。scope 是公司代號或 SHARED。
export function keysDir(scope) {
  if (scope === SHARED) return SHARED_DIR;
  if (!ID.test(scope)) throw new Error(`公司代號不正確：${scope}`);
  const hiveDir = join('hives', scope);
  const st = lstatSync(hiveDir, { throwIfNoEntry: false });
  if (!st || !st.isDirectory()) throw new Error(`找不到公司 hives/${scope}/`);
  return join(hiveDir, '.keys');
}

// 路徑上每一層都必須是真的資料夾（不是連結），否則金鑰會被導到別處讀寫。
// 回傳 true：存在；false：還沒建立。
export function checkDirs(dir) {
  const parts = dir.split('/');
  for (let i = 1; i <= parts.length; i += 1) {
    const path = parts.slice(0, i).join('/');
    const st = lstatSync(path, { throwIfNoEntry: false });
    if (!st) return false;
    if (!st.isDirectory()) throw new Error(`${path} 不是資料夾（可能是連結），不使用`);
  }
  return true;
}

function fileIn(scope, kind) {
  const dir = keysDir(scope);
  return checkDirs(dir) ? join(dir, KINDS[kind].file) : null;
}

// 找這把金鑰在哪一層：先公司專屬、再共用。hive 可以是 null（還沒有公司）。
export function locate(hive, kind) {
  const scopes = hive && hive !== SHARED ? [hive, SHARED] : [SHARED];
  for (const scope of scopes) {
    const path = fileIn(scope, kind);
    const st = path && lstatSync(path, { throwIfNoEntry: false });
    if (st) return { scope, path };
  }
  return null;
}

// 有沒有這把金鑰（只看檔案，不讀內容）。回傳 'hive'、'shared' 或 false。
export function hasKey(hive, kind) {
  try {
    const found = locate(hive, kind);
    if (!found) return false;
    const st = lstatSync(found.path);
    if (!st.isFile() || st.size === 0) return false;
    return found.scope === SHARED ? 'shared' : 'hive';
  } catch {
    return false;
  }
}

// 讀金鑰給程式用（不會印出）。拒絕 symlink 與過大的檔案。
export function readKey(hive, kind, found = locate(hive, kind)) {
  if (!found) return null;
  const path = found.path;
  let fd;
  try {
    fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw new Error(`${KINDS[kind].label} 檔案無法讀取（${error.code}）`);
  }
  try {
    const st = fstatSync(fd);
    if (!st.isFile() || st.size > 4096) throw new Error(`${KINDS[kind].label} 檔案格式不對`);
    const buf = Buffer.alloc(st.size);
    readSync(fd, buf, 0, st.size, 0);
    const value = buf.toString('utf8').trim();
    if (!KINDS[kind].valid(value)) throw new Error(`${KINDS[kind].label} 格式不對，${KINDS[kind].hint}；請重新設定`);
    return value;
  } finally {
    closeSync(fd);
  }
}

// 把任何輸出裡可能出現的金鑰遮掉。
export function redact(text) {
  return String(text)
    .replace(/github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+|glpat-[A-Za-z0-9_.-]+/g, '[已遮蔽]')
    .replace(/(api\/webhooks\/\d+\/)[A-Za-z0-9_-]+/g, '$1[已遮蔽]');
}
