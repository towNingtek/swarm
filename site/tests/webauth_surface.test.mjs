// The web-auth fork replaces DSH's stock web server (`webServer` service). It must answer every member
// DSH's own plugins read from `webServer`, or a turn fails at run time: DSH 0.2's dsh-web-app reads
// `webServer.getOrigin()` and `.protocol` on every turn, and the fork had neither ("getOrigin is not a
// function", every turn ended in error on a 0.2 site).
//
//   node --test site/tests/webauth_surface.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { register } from 'node:module'
import { readFileSync } from 'node:fs'

const here = dirname(fileURLToPath(import.meta.url))
// The fork imports @deepseek-ai/cordis, schemastery and dsh-host-webserver, which the site image installs
// beside it. Outside the image, point DSH_PREFIX at a directory with @deepseek-ai/dsh installed.
const prefix = process.env.DSH_PREFIX
assert.ok(prefix, 'set DSH_PREFIX to a directory with @deepseek-ai/dsh installed (npm install --prefix $D @deepseek-ai/dsh@0.2.1-rc.5)')
const dshRequire = createRequire(join(prefix, 'node_modules', '@deepseek-ai', 'dsh', 'package.json'))
// Map the fork's @deepseek-ai/* imports to that install's ESM entries.
const entries = {}
for (const name of ['@deepseek-ai/cordis', '@deepseek-ai/schemastery', '@deepseek-ai/dsh-host-webserver', '@deepseek-ai/dsh-scope']) {
  try {
    const pkgFile = dshRequire.resolve(name + '/package.json')
    const pkg = JSON.parse(readFileSync(pkgFile, 'utf8'))
    const exp = pkg.exports?.['.'] ?? pkg.exports
    const rel = (typeof exp === 'string' ? exp : exp?.import ?? exp?.default ?? exp?.node?.import ?? exp?.node) ?? pkg.module ?? pkg.main ?? 'index.js'
    entries[name] = pathToFileURL(join(dirname(pkgFile), typeof rel === 'string' ? rel : rel.default)).href
  } catch {}
}
register('data:text/javascript,' + encodeURIComponent(`
  const entries = ${JSON.stringify(entries)}
  export async function resolve(spec, ctx, next) {
    return entries[spec] ? { url: entries[spec], shortCircuit: true } : next(spec, ctx)
  }`))
const forkDir = join(here, '..', 'dsh-profile', 'vendor', 'dsh-web-auth')

// What DSH 0.2.1's own plugins read from `webServer` (grep of @deepseek-ai/*/lib for `webServer.<x>`, 2026-10-29),
// except `listen`, which is only the stock server's own effect label.
const DSH02_READS = ['register', 'registerUpgrade', 'registerFallback', 'renderIndex', 'host', 'protocol', 'getOrigin']

const load = () => import(pathToFileURL(join(forkDir, 'src', 'index.js')).href)

test('the fork answers every webServer member DSH 0.2 reads', async () => {
  const proto = (await load()).default.prototype
  for (const name of DSH02_READS) {
    const d = Object.getOwnPropertyDescriptor(proto, name)
    assert.ok(d, `webServer.${name} is missing from the fork`)
    if (name === 'host' || name === 'protocol') assert.equal(typeof d.get, 'function', `${name} is a getter`)
    else assert.equal(typeof d.value, 'function', `${name} is a method`)
  }
})

test('getOrigin and protocol follow the stock contract', async () => {
  const mod = await load()
  const make = (host, port) => Object.assign(Object.create(mod.default.prototype), { config: { host }, boundPort: port })
  assert.equal(make('127.0.0.1', 3081).getOrigin(), 'http://127.0.0.1:3081')
  assert.equal(make('0.0.0.0', 3081).getOrigin(), 'http://127.0.0.1:3081')
  assert.equal(make('::1', 3081).getOrigin(), 'http://[::1]:3081')
  assert.equal(make('127.0.0.1', 3081).protocol, 'http:')
  assert.throws(() => make('127.0.0.1', undefined).getOrigin(), /before the listener bound/)
  assert.throws(() => make('fe80::1%eth0', 3081).getOrigin(), /zone id/)
})
