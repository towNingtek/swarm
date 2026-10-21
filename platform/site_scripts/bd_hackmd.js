// Publish a consultant profile repo to ONE HackMD note (BD reports, opt-in).
// Runs INSIDE the site container: `node -` with `const __REQ = {...};` prepended
// by platform/support_site_line.py after a scheduled run whose report has
// `hackmd: {repo, token_file, owner}` in project.yaml.
//   __REQ = { workspace, repo, keys_dir, token_file, owner_label, changes }
//     workspace   absolute path of the office in the container
//     repo        repo directory, relative to workspace (must stay inside it)
//     keys_dir    absolute keys directory in the container
//     token_file  the site's HackMD token, relative to keys_dir (never printed)
//     changes     this week's change text from the run ("" = no update)
// The repo is expected to hold profile/consultant-profile.md and cases/NN-*.md.
// The note id is kept in <repo>/.hackmd-note-id; created on first run, then
// the same note is updated. Prints {ok, url, created, cases} or {ok:false,error}.
'use strict';
const fs = require('fs');
const path = require('path');
const r = __REQ;
const out = (o) => process.stdout.write(JSON.stringify(o) + '\n');
function fail(error) { out({ ok: false, error }); process.exit(0); }

// Resolve `name` under `base`; refuse absolute names, `..` and symlinks that escape.
function inside(base, name) {
  if (typeof base !== 'string' || typeof name !== 'string' || !name || name.includes('\0') ||
      path.isAbsolute(name) || name.split(/[\\/]/).includes('..')) return null;
  try {
    const root = fs.realpathSync(base);
    const full = fs.realpathSync(path.join(root, name));
    return full === root || full.startsWith(root + path.sep) ? full : null;
  } catch { return null; }
}

const tokenPath = inside(r.keys_dir, r.token_file);
if (!tokenPath) fail('token_unreadable');
let token;
try { token = fs.readFileSync(tokenPath, 'utf8').trim(); } catch { fail('token_unreadable'); }
if (!token || token.length > 400) fail('token_invalid');

const repo = inside(r.workspace, r.repo);
if (!repo) fail('repo_missing');
r.repo = repo;

const read = (p) => { try { return fs.readFileSync(path.join(r.repo, p), 'utf8'); } catch { return null; } };
const profile = read('profile/consultant-profile.md');
if (!profile) fail('profile_missing');

function caseSummary(file, text) {
  const title = ((text.match(/^#\s+(.+)$/m) || [])[1] || file).replace(/^案例：\s*/, '').trim();
  const status = ((text.match(/狀態[：:]\s*\**([^\n*|]+)/) || [])[1] || '').trim();
  const expo = ((text.match(/可對外程度[：:]\s*\**([^\n*|]+)/) || [])[1] || '').trim();
  let one = '';
  const m = text.match(/##\s*一句話摘要\s*\n+([\s\S]*?)(?:\n##\s|$)/);
  if (m) one = m[1].replace(/\s+/g, ' ').trim();
  if (one.length > 280) one = one.slice(0, 278) + '…';
  return { file, title, status, expo, one };
}
const caseDir = path.join(r.repo, 'cases');
let cases = [];
try {
  cases = fs.readdirSync(caseDir).filter((f) => /^\d+.*\.md$/.test(f)).sort()
    .map((f) => caseSummary(f, fs.readFileSync(path.join(caseDir, f), 'utf8')));
} catch { /* no cases yet */ }

const now = new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 16).replace('T', ' ');
const changes = (r.changes || '').trim();
const body = [
  `# 顧問側寫 — ${r.owner_label}`,
  '',
  `> 自動維護（每週一素材維護後更新）｜最後更新 ${now}（台北）｜來源：private repo \`${path.basename(r.repo)}\``,
  '> 只有擁有者能看。要分享前請先確認內容可以對外。',
  '',
  '## 本週變化',
  '',
  changes || '本週無更新。',
  '',
  '---',
  '',
  profile.replace(/^#\s+/, '## '),
  '',
  '---',
  '',
  `## 案例（${cases.length} 筆，全文在 repo 的 cases/）`,
  '',
  ...cases.map((c) => `### ${c.title}\n\n- 檔案：\`cases/${c.file}\`　狀態：${c.status || '—'}　可對外程度：${c.expo || '—'}\n\n${c.one || '（沒有一句話摘要）'}\n`),
].join('\n');

const idFile = path.join(r.repo, '.hackmd-note-id');
let noteId = null;
try { noteId = fs.readFileSync(idFile, 'utf8').trim() || null; } catch {}

async function api(method, url, payload) {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 20000);
  try {
    const res = await fetch('https://api.hackmd.io/v1' + url, {
      method, signal: ctl.signal,
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: payload ? JSON.stringify(payload) : undefined,
    });
    const text = await res.text();
    let data = null; try { data = text ? JSON.parse(text) : null; } catch {}
    return { status: res.status, data };
  } catch (e) { return { status: 0, data: null }; } finally { clearTimeout(t); }
}

(async () => {
  let created = false;
  if (noteId && !/^[A-Za-z0-9_-]{6,64}$/.test(noteId)) noteId = null;
  if (noteId) {
    const res = await api('PATCH', '/notes/' + noteId, { content: body });
    if (res.status === 404) noteId = null;
    else if (res.status < 200 || res.status >= 300) fail('hackmd_update_' + res.status);
  }
  if (!noteId) {
    const res = await api('POST', '/notes', {
      title: `顧問側寫 — ${r.owner_label}`, content: body, tags: ['bd', '顧問側寫'],
      readPermission: 'owner', writePermission: 'owner', commentPermission: 'disabled',
    });
    if (res.status < 200 || res.status >= 300 || !res.data || !res.data.id) fail('hackmd_create_' + res.status);
    noteId = res.data.id; created = true;
    fs.writeFileSync(idFile, noteId + '\n');
  }
  const info = await api('GET', '/notes/' + noteId);
  const url = (info.data && (info.data.publishLink || (info.data.userPath && info.data.shortId
              && `https://hackmd.io/@${info.data.userPath}/${info.data.shortId}`))) || `https://hackmd.io/${noteId}`;
  out({ ok: true, url, created, cases: cases.length, chars: body.length });
})();
