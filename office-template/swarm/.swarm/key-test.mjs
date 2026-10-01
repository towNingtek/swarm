#!/usr/bin/env node
// 測試公司金鑰能不能用，不會發訊息、不會改任何東西：
//   node .swarm/key-test.mjs <公司代號> github|discord|hackmd
// GitHub：確認 token 有效並列出看得到的 repo；Discord：讀取 webhook 資訊（不發文）；HackMD：讀取帳號名稱。
import { KINDS, KIND_NAMES, SHARED, locate, readKey, redact } from './keys.mjs';

// only：只測這一層（set-key 剛存好時用），不退回共用層。
export async function testKey(hive, kind, { only = false } = {}) {
  let value;
  let found;
  try {
    found = locate(hive, kind);
    if (only && found && (found.scope === SHARED) !== (hive === SHARED)) found = null;
    value = found ? readKey(hive, kind, found) : null;
  } catch (error) {
    return { ok: false, detail: error.message };
  }
  if (!value) return { ok: false, detail: `還沒設定 ${KINDS[kind].label}` };
  const layer = found.scope === SHARED && hive !== SHARED ? '（用的是共用金鑰）' : '';
  const result = await probe(kind, value);
  return { ok: result.ok, detail: result.detail + (result.ok ? layer : '') };
}

async function probe(kind, value) {
  const signal = AbortSignal.timeout(15000);
  try {
    if (kind === 'github') {
      const headers = { Authorization: `Bearer ${value}`, Accept: 'application/vnd.github+json',
                        'User-Agent': 'swarm-office' };
      const user = await fetch('https://api.github.com/user', { headers, signal });
      if (user.status === 401) return { ok: false, detail: 'GitHub 拒絕這個 token（可能過期或已撤銷）' };
      if (!user.ok) return { ok: false, detail: `GitHub 回應 ${user.status}` };
      const login = (await user.json()).login;
      const repos = await fetch('https://api.github.com/user/repos?per_page=10&sort=updated', { headers, signal });
      const names = repos.ok ? (await repos.json()).map((r) => r.full_name) : [];
      return { ok: true, detail: `GitHub 帳號 ${login}；看得到的 repo：${names.join('、') || '（沒有，請確認 token 有選 repo）'}` };
    }
    if (kind === 'gitlab') {
      const headers = { 'PRIVATE-TOKEN': value };
      const user = await fetch('https://gitlab.com/api/v4/user', { headers, signal });
      if (user.status === 401) return { ok: false, detail: 'GitLab 拒絕這個 token（可能過期或已撤銷）' };
      if (!user.ok) return { ok: false, detail: `GitLab 回應 ${user.status}` };
      const login = (await user.json()).username;
      const self = await fetch('https://gitlab.com/api/v4/personal_access_tokens/self', { headers, signal });
      const info = self.ok ? await self.json() : {};
      const extra = info.expires_at ? `；到期 ${info.expires_at}` : '';
      return { ok: true, detail: `GitLab 帳號 ${login}${info.scopes ? `；權限 ${info.scopes.join(',')}` : ''}${extra}` };
    }
    if (kind === 'hackmd') {
      const me = await fetch('https://api.hackmd.io/v1/me',
                             { headers: { Authorization: `Bearer ${value}` }, signal });
      if (me.status === 401 || me.status === 403) return { ok: false, detail: 'HackMD 拒絕這個 token（可能已撤銷）' };
      if (!me.ok) return { ok: false, detail: `HackMD 回應 ${me.status}` };
      const info = await me.json();
      return { ok: true, detail: `HackMD 帳號 ${info.name}（${info.userPath}）` };
    }
    const hook = await fetch(value, { signal });
    if (hook.status === 401 || hook.status === 404) return { ok: false, detail: 'Discord 找不到這個 webhook（可能已刪除）' };
    if (!hook.ok) return { ok: false, detail: `Discord 回應 ${hook.status}` };
    const info = await hook.json();
    return { ok: true, detail: `Discord webhook「${info.name}」，頻道 ID ${info.channel_id}` };
  } catch (error) {
    return { ok: false, detail: redact(`連線失敗：${error.name === 'TimeoutError' ? '逾時' : error.message}`) };
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const [hive, kind] = process.argv.slice(2);
  if (!hive || !KINDS[kind]) {
    console.log(`用法：node .swarm/key-test.mjs <公司代號|--shared> ${KIND_NAMES}`);
    process.exit(2);
  }
  const result = await testKey(hive, kind);
  console.log(result.ok ? `✓ ${result.detail}` : `✗ ${result.detail}`);
  process.exit(result.ok ? 0 : 1);
}
