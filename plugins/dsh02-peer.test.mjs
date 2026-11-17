// dsh-swarm-board and dsh-synapse call the DSH 0.2 gateway with the owner Peer.
//
//   D=$(mktemp -d) && npm install --prefix $D --no-save @deepseek-ai/dsh@0.2.1-rc.5
//   DSH02_PREFIX=$D node --test plugins/dsh02-peer.test.mjs
//
// DSH 0.2's gateway starts every in-process call with `assertPeerLive(request.peer, …)`, which reads
// `request.peer.signal`: a call without a Peer throws. On a 0.2 site that made the board's Copilot answer
// HTTP 502 ("送不進站台 DSH"). dsh02-load.test.mjs only loads the plugins with a stand-in gateway, so it
// could not see this.
//
// Here the stand-in gateway applies DSH 0.2's OWN peer check (taken from $DSH02_PREFIX's dsh-api-gateway
// source, not copied), and the host provides `connection.browserPeer` like DSH 0.2 does. Then the plugin's
// own HTTP routes are called the way the board page and the synapse page call them.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { Readable } from 'node:stream'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const prefix = process.env.DSH02_PREFIX

async function dsh02() {
  assert.ok(prefix, 'DSH02_PREFIX is not set (a directory with @deepseek-ai/dsh@0.2 installed)')
  const dshPkg = join(prefix, 'node_modules/@deepseek-ai/dsh/package.json')
  assert.match(JSON.parse(readFileSync(dshPkg, 'utf8')).version, /^0\.2\./)
  const require = createRequire(dshPkg)
  const cordis = await import(pathToFileURL(require.resolve('@deepseek-ai/cordis')).href)
  // DSH 0.2's own peer check, read from its gateway: the function body of assertPeerLive.
  const src = readFileSync(join(dirname(require.resolve('@deepseek-ai/dsh-api-gateway/package.json')), 'lib/index.js'), 'utf8')
  const m = src.match(/function assertPeerLive\(peer, endpoint\) \{([\s\S]*?)\n\}/)
  assert.ok(m, 'DSH 0.2 gateway has no assertPeerLive (the peer rule moved; revisit this test)')
  assert.match(m[1], /peer\.signal/, 'DSH 0.2 assertPeerLive no longer reads peer.signal')
  // eslint-disable-next-line no-new-func
  const assertPeerLive = new Function('peer', 'endpoint', 'TypertGatewayError', m[1])
  class TypertGatewayError extends Error {}
  return { cordis, check: (peer, endpoint) => assertPeerLive(peer, endpoint, TypertGatewayError) }
}

// A gateway that enforces DSH 0.2's peer rule, then answers like a small DSH.
function gateway(check, log) {
  let n = 0
  const answer = (request) => {
    const { namespace, method } = request
    if (namespace === 'session' && method === 'create') return { sessionId: `session-00000000-0000-0000-0000-00000000000${++n}` }
    if (namespace === 'session' && method === 'prompt') return { accepted: true }
    if (namespace === 'session' && method === 'modelCatalog') return { default: { provider: 'p', model: 'm' }, groups: [] }
    if (namespace === 'workspace' && method === 'create') return { workspace: { workspaceId: 'w1', title: '🌌 協作星系分析' } }
    return {}
  }
  return {
    async invoke(request) {
      check(request.peer, `${request.namespace}/${request.method}`) // throws without a Peer, as DSH 0.2 does
      log.push({ endpoint: `${request.namespace}/${request.method}`, peer: request.peer })
      return answer(request)
    },
    async stream(request) {
      check(request.peer, `${request.namespace}/${request.method}`)
      log.push({ endpoint: `${request.namespace}/${request.method}`, peer: request.peer })
      return (async function* () {})()
    },
  }
}

function host(Context, check) {
  const root = new Context()
  const routes = new Map()
  const log = []
  const ownerPeer = { principal: { kind: 'owner' }, signal: new AbortController().signal }
  root.provide('webServer', {
    register(route) { routes.set(route.path, route.handler); return () => routes.delete(route.path) },
  })
  root.provide('typertGateway', gateway(check, log))
  root.provide('connection', { browserPeer: ownerPeer })
  root.provide('workspaceRegistry', { archivedSessionIds: [] })
  return { root, routes, log, ownerPeer }
}

// Call a registered route handler the way the web server does (node req/res).
function call(handler, path, { method = 'GET', body } = {}) {
  return new Promise((resolve, reject) => {
    const req = Readable.from(body === undefined ? [] : [Buffer.from(JSON.stringify(body))])
    Object.assign(req, { method, url: path, headers: { 'content-type': 'application/json', host: 'site.example' } })
    const chunks = []
    const res = {
      statusCode: 200, headers: {},
      setHeader(k, v) { this.headers[k.toLowerCase()] = v }, getHeader(k) { return this.headers[k.toLowerCase()] },
      writeHead(code, headers) { this.statusCode = code; Object.assign(this.headers, headers || {}); return this },
      write(c) { chunks.push(Buffer.from(c)); return true },
      end(c) { if (c) chunks.push(Buffer.from(c)); resolve({ status: this.statusCode, text: Buffer.concat(chunks).toString('utf8') }) },
      on() { return this }, once() { return this }, removeListener() { return this },
    }
    Promise.resolve(handler(req, res)).catch(reject)
  })
}

async function start(dir, Context, check) {
  const pkg = JSON.parse(readFileSync(join(here, dir, 'package.json'), 'utf8'))
  const mod = await import(pathToFileURL(join(here, dir, pkg.exports['.'].default)).href)
  const h = host(Context, check)
  const data = mkdtempSync(join(tmpdir(), 'dsh02-peer-'))
  const config = { dataDir: join(data, 'data'), sessionsFile: join(data, 'sessions.json'), suggestionsFile: join(data, 'suggestions.json'),
    canvasFile: join(data, 'canvas.json'), fetch: false, fetchEveryMs: 0, digestEveryMs: 0, githubRecheckMs: 0 }
  const fiber = h.root.plugin({ name: pkg.name, apply: mod.apply }, config)
  await fiber.await()
  return { ...h, fiber }
}

test('the stand-in gateway really rejects a call without a Peer (DSH 0.2 rule)', async () => {
  const { check } = await dsh02()
  assert.throws(() => check(undefined, 'session/create'))
  assert.doesNotThrow(() => check({ signal: new AbortController().signal }, 'session/create'))
})

test('dsh-swarm-board: the Copilot (POST /board/api/think) reaches DSH as the owner', async () => {
  const { cordis, check } = await dsh02()
  const h = await start('swarm-board/wolffia/site-plugin', cordis.Context, check)
  const handler = h.routes.get('/board')
  assert.equal(typeof handler, 'function')
  const r = await call(handler, '/board/api/think', { method: 'POST', body: { project: '2', question: '只回覆 OK', context: { project: { title: 't' } } } })
  assert.equal(r.status, 200, `think answered ${r.status}: ${r.text}`)
  assert.match(JSON.parse(r.text).sessionId, /^session-/)
  const used = h.log.map((x) => x.endpoint)
  assert.ok(used.includes('session/create') && used.includes('session/prompt'), used.join(','))
  for (const x of h.log) assert.equal(x.peer, h.ownerPeer, `${x.endpoint} was not called as the owner`)
  await h.fiber.dispose()
})

test('dsh-synapse: the model menu and 問 (ask) reach DSH as the owner', async () => {
  const { cordis, check } = await dsh02()
  const h = await start('synapse', cordis.Context, check)
  const handler = h.routes.get('/synapse')
  assert.equal(typeof handler, 'function')
  // The page's model menu (GET /synapse/api/models → session/modelCatalog).
  const m = await call(handler, '/synapse/api/models')
  assert.equal(m.status, 200, `models answered ${m.status}: ${m.text}`)
  // The page's 問 (POST /synapse/api/ask → session/create, selectModel, prompt).
  const a = await call(handler, '/synapse/api/ask', { method: 'POST', body: { model: { provider: 'p', model: 'm' }, question: 'OK', context: {} } })
  assert.ok(a.status < 500, `ask answered ${a.status}: ${a.text}`)
  const used = h.log.map((x) => x.endpoint)
  assert.ok(used.includes('session/modelCatalog'), used.join(','))
  for (const x of h.log) assert.equal(x.peer, h.ownerPeer, `${x.endpoint} was not called as the owner`)
  await h.fiber.dispose()
})
