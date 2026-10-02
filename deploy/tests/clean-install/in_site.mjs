// Runs inside the site container (as the site's own user). Checks what the
// AI in a site can reach. Prints facts, never the key.
import fs from 'node:fs'
import net from 'node:net'
import { execFileSync } from 'node:child_process'

const fail = (m) => { console.log('BAD ' + m); process.exitCode = 1 }
const ok = (m) => console.log('ok  ' + m)
const tcp = (host, port) => new Promise((resolve) => {
  const s = net.connect({ host, port, timeout: 3000 }, () => { s.destroy(); resolve(true) })
  s.on('error', () => resolve(false)); s.on('timeout', () => { s.destroy(); resolve(false) })
})

for (const [h, p, want] of [['172.17.0.1', 8212, true], ['172.17.0.1', 4000, false], ['172.17.0.1', 8210, false],
  ['172.17.0.1', 8211, false], ['172.17.0.1', 22, false], ['172.29.10.1', 80, false], ['169.254.169.254', 80, false]]) {
  const open = await tcp(h, p)
  if (open === want) ok(`${h}:${p} ${open ? 'reachable' : 'blocked'}`)
  else fail(`${h}:${p} ${open ? 'reachable' : 'blocked'}`)
}

const creds = fs.readFileSync(`${process.env.DSH_HOME || '/home/dsh/dsh-home'}/.credentials.yaml`, 'utf8')
const key = (creds.match(/sms_[A-Za-z0-9_-]+/) || [])[0]
if (!key) fail('no starter model key in the site'); else ok('the site has its own starter model key')
const relay = 'http://172.17.0.1:8212/v1'
const H = { 'content-type': 'application/json', authorization: `Bearer ${key}` }
const msg = (extra = {}) => JSON.stringify({ model: 'cloud-fast', messages: [{ role: 'user', content: 'hi' }], ...extra })
const status = async (path, opt) => (await fetch(relay + path, opt)).status
const r = await fetch(relay + '/chat/completions', { method: 'POST', headers: H, body: msg() })
const answer = (await r.json()).choices?.[0]?.message?.content
r.status === 200 && answer === 'stub answer' ? ok('a model call through the relay works') : fail(`relay call ${r.status}`)
const cases = [
  ['no key', '/chat/completions', { method: 'POST', headers: { 'content-type': 'application/json' }, body: msg() }, 401],
  ['wrong key', '/chat/completions', { method: 'POST', headers: { ...H, authorization: 'Bearer sms_wrong' }, body: msg() }, 401],
  ['model not allowed', '/chat/completions', { method: 'POST', headers: H, body: JSON.stringify({ model: 'other', messages: [] }) }, 400],
  ['other endpoint', '/embeddings', { method: 'POST', headers: H, body: msg() }, 404]]
for (const [name, path, opt, want] of cases) {
  const got = await status(path, opt)
  got === want ? ok(`relay refuses ${name} (${got})`) : fail(`relay ${name}: ${got}, want ${want}`)
}

// First visit: DSH creates its default workspace under the configured
// Documents directory. It must be the office the template filled in.
const dflt = '/opt/swarm-documents/deepseek-harness/default-workspace'
fs.existsSync(dflt) && fs.realpathSync(dflt) === '/home/dsh/workspace'
  ? ok('the default workspace is the office workspace') : fail('default workspace does not resolve to /home/dsh/workspace')
const tree = execFileSync('dsh', ['--profile', 'web', '--dump-config'], { encoding: 'utf8', cwd: '/home/dsh' })
const docsSet = /id: workspace-controller[\s\S]{0,200}documentsDirectory: \/opt\/swarm-documents/.test(tree)
docsSet
  ? ok('the site profile sets the Documents directory') : fail('the site profile does not set documentsDirectory')
