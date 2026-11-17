// Load dsh-swarm-board, dsh-synapse and swarm-brand in a real DSH 0.2 cordis.
//
//   D=$(mktemp -d) && npm install --prefix $D --no-save @deepseek-ai/dsh@0.2.1-rc.4
//   DSH02_PREFIX=$D node --test plugins/dsh02-load.test.mjs
//
// The cordis is the one $DSH02_PREFIX's @deepseek-ai/dsh resolves (the one a 0.2 site runs);
// the Host services the plugins use (webServer, typertGateway, workspaceRegistry) are stand-ins.
// Each plugin's apply must bring its fiber to ACTIVE; a startup error fails the test.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const prefix = process.env.DSH02_PREFIX

const PLUGINS = [
  { name: 'dsh-swarm-board', dir: 'swarm-board/wolffia/site-plugin', route: '/board' },
  { name: 'dsh-synapse', dir: 'synapse', route: '/synapse' },
  { name: 'swarm-brand', dir: 'swarm-brand' },
]

async function loadDsh() {
  assert.ok(prefix, 'DSH02_PREFIX is not set (a directory with @deepseek-ai/dsh@0.2 installed)')
  const dshPkg = join(prefix, 'node_modules/@deepseek-ai/dsh/package.json')
  const dsh = JSON.parse(readFileSync(dshPkg, 'utf8'))
  const require = createRequire(dshPkg)
  const cordisPkg = JSON.parse(readFileSync(require.resolve('@deepseek-ai/cordis/package.json'), 'utf8'))
  const cordis = await import(pathToFileURL(require.resolve('@deepseek-ai/cordis')).href)
  return { version: dsh.version, cordisVersion: cordisPkg.version, cordis }
}

function host(Context) {
  const root = new Context()
  const routes = []
  root.provide('webServer', {
    register(route) {
      assert.equal(typeof route.handler, 'function')
      routes.push(route.path)
      return () => { routes.splice(routes.indexOf(route.path), 1) }
    },
  })
  root.provide('typertGateway', { async invoke() { return null } })
  root.provide('workspaceRegistry', { archivedSessionIds: [] })
  return { root, routes }
}

test('$DSH02_PREFIX holds DSH 0.2 and its cordis', async () => {
  const { version, cordisVersion } = await loadDsh()
  assert.match(version, /^0\.2\./)
  assert.match(cordisVersion, /^4\./)
})

for (const p of PLUGINS) {
  test(`${p.name}: apply loads in DSH 0.2 without an error`, async () => {
    const { cordis } = await loadDsh()
    const pkg = JSON.parse(readFileSync(join(here, p.dir, 'package.json'), 'utf8'))
    assert.equal(pkg.name, p.name)
    const mod = await import(pathToFileURL(join(here, p.dir, pkg.exports['.'].default)).href)
    assert.equal(typeof mod.apply, 'function')

    const { root, routes } = host(cordis.Context)
    const data = mkdtempSync(join(tmpdir(), 'dsh02-load-'))
    // No background work: no fetcher, no digest, no gh rechecks, no skill install.
    const config = { dataDir: join(data, 'data'), sessionsFile: join(data, 'sessions.json'), suggestionsFile: join(data, 'suggestions.json'),
      canvasFile: join(data, 'canvas.json'), fetch: false, fetchEveryMs: 0, digestEveryMs: 0, githubRecheckMs: 0 }
    const fiber = root.plugin({ name: p.name, apply: mod.apply }, config)
    await fiber.await() // rethrows a startup error
    assert.equal(fiber.state, 2 /* FiberState.ACTIVE */)
    if (p.route) assert.deepEqual(routes, [p.route])
    await fiber.dispose()
    assert.deepEqual(routes, [])
  })
}
