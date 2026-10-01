#!/usr/bin/env node
// git 的憑證小幫手：讓 git 用公司的 GitHub／GitLab token，而 token 不會寫進 .git/config 或網址。
// 在某個 repo 裡設定一次（只記錄這支程式的路徑，不記錄 token）：
//   git config credential.https://github.com.helper \
//     "!node /home/dsh/workspace/.swarm/git-credential.mjs <公司代號>"
//   （GitLab 把 github.com 換成 gitlab.com）
// 之後 git clone／pull／push https://github.com/...、https://gitlab.com/... 就會自動使用。
import { readKey } from './keys.mjs';

const [hive, action] = process.argv.slice(2);
if (action !== 'get') process.exit(0); // store／erase：什麼都不存
let input = '';
for await (const chunk of process.stdin) input += chunk;
const fields = Object.fromEntries(input.split('\n').filter(Boolean).map((l) => l.split(/=(.*)/s).slice(0, 2)));
const HOSTS = { 'github.com': ['github', 'x-access-token'], 'gitlab.com': ['gitlab', 'oauth2'] };
if (fields.protocol !== 'https' || !Object.hasOwn(HOSTS, fields.host)) process.exit(0);
const [kind, username] = HOSTS[fields.host];
let token = null;
try {
  token = readKey(hive, kind);
} catch (error) {
  process.stderr.write(`[git-credential] ${error.message}\n`);
}
if (!token) process.exit(0);
process.stdout.write(`username=${username}\npassword=${token}\n`);
