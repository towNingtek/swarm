// Host routes against a fake ctx and fake req/res. No DSH, no network.
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import vm from 'node:vm'
import * as plugin from '../lib/index.js'

const V8 = readFileSync(new URL('../../v8.html', import.meta.url), 'utf8')
const LAUNCHER = readFileSync(new URL('../lib/launcher.js', import.meta.url), 'utf8')

// Fake startFetcher: records its options, counts stop(). No python, no gh.
function fakeFetcher() {
  const calls = []
  const fn = (opts) => { const f = { opts, stopped: 0, stop() { f.stopped++ } }; calls.push(f); return f }
  fn.calls = calls
  return fn
}

const settle = () => new Promise((r) => setImmediate(r))

function setup(config = {}, hooks = {}) {
  const root = mkdtempSync(join(tmpdir(), 'swarm-board-'))
  const dataDir = join(root, 'data')
  mkdirSync(dataDir)
  writeFileSync(join(dataDir, 'index.js'), 'window.PROJECTS={}')
  writeFileSync(join(dataDir, 'p2.js'), 'window.P2=1')
  writeFileSync(join(root, 'secret.js'), 'SECRET')
  writeFileSync(join(dataDir, 'demo-epic.js'), 'DEMO')
  const registered = []
  const disposers = []
  const logs = []
  const startFetcher = hooks.startFetcher ?? fakeFetcher()
  const ctx = {
    logger: () => ({ warn: (...a) => logs.push(a.join(' ')), error() {}, info: (...a) => logs.push(a.join(' ')) }),
    effect: (fn) => { const d = fn(); if (typeof d === 'function') disposers.push(d) },
    connection: {},
    typertGateway: {},
    webServer: { register: (r) => { registered.push(r); return () => {} } },
  }
  plugin.apply(ctx, { dataDir, ...config }, { hasGitHub: () => true, ...hooks, startFetcher })
  return { root, dataDir, registered, disposers, logs, startFetcher }
}

async function call(route, url, method = 'GET') {
  const res = {
    status: 0, headers: {}, body: undefined, headersSent: false,
    writeHead(status, headers) { this.status = status; this.headers = headers; this.headersSent = true; return this },
    end(body) { this.body = body === undefined ? undefined : String(body) },
    destroy() {},
  }
  await route.handler({ method, url, headers: {} }, res)
  return res
}

const assertHardened = (res) => {
  assert.equal(res.headers['cache-control'], 'no-store')
  assert.equal(res.headers['x-content-type-options'], 'nosniff')
}

test('exports name and inject', () => {
  assert.equal(plugin.name, 'dsh-swarm-board')
  assert.deepEqual(plugin.inject, ['connection', 'webServer', 'typertGateway'])
})

test('registers exactly one /board prefix route', () => {
  const { registered } = setup()
  assert.equal(registered.length, 1)
  assert.equal(registered[0].kind, 'prefix')
  assert.equal(registered[0].path, '/board')
  assert.equal(typeof registered[0].handler, 'function')
})

test('/board/ serves the board HTML (v8.html)', async () => {
  const [route] = setup().registered
  const res = await call(route, '/board/')
  assert.equal(res.status, 200)
  assert.match(res.headers['content-type'], /^text\/html/)
  assert.equal(res.body, V8)
  assertHardened(res)
  const q = await call(route, '/board/?n=2')
  assert.equal(q.status, 200)
  assert.equal(q.body, V8)
})

test('/board/ HTML is read once at start', async () => {
  const root = mkdtempSync(join(tmpdir(), 'swarm-board-html-'))
  const html = join(root, 'board.html')
  writeFileSync(html, '<p>first</p>')
  const [route] = setup({ html }).registered
  writeFileSync(html, '<p>second</p>')
  assert.equal((await call(route, '/board/')).body, '<p>first</p>')
})

test('/board redirects to /board/', async () => {
  const [route] = setup().registered
  const res = await call(route, '/board')
  assert.equal(res.status, 308)
  assert.equal(res.headers.location, '/board/')
  assertHardened(res)
})

test('/board/data/p2.js and index.js are read from dataDir on each request', async () => {
  const { registered: [route], dataDir } = setup()
  const res = await call(route, '/board/data/p2.js?t=123')
  assert.equal(res.status, 200)
  assert.match(res.headers['content-type'], /javascript/)
  assert.equal(res.body, 'window.P2=1')
  assertHardened(res)
  writeFileSync(join(dataDir, 'p2.js'), 'window.P2=2')
  assert.equal((await call(route, '/board/data/p2.js')).body, 'window.P2=2')
  assert.equal((await call(route, '/board/data/index.js')).body, 'window.PROJECTS={}')
  const head = await call(route, '/board/data/p2.js', 'HEAD')
  assert.equal(head.status, 200)
  assert.equal(head.body, undefined)
})

test('dataDir defaults to $DSH_HOME/swarm-board/data', () => {
  const before = process.env.DSH_HOME
  process.env.DSH_HOME = '/x/dsh-home'
  try {
    assert.equal(plugin.defaultDataDir(), '/x/dsh-home/swarm-board/data')
  } finally {
    if (before === undefined) delete process.env.DSH_HOME
    else process.env.DSH_HOME = before
  }
})

test('traversal and other file names are 404', async () => {
  const [route] = setup().registered
  for (const url of [
    '/board/data/../secret.js',
    '/board/data/%2e%2e/secret.js',
    '/board/data/..%2fsecret.js',
    '/board/data/..%2Fsecret.js',
    '/board/../data/p2.js',
    '/board/data/p3.js',
    '/board/data/demo-epic.js',
    '/board/data/p2.json',
    '/board/data/p.js',
    '/board/data/px.js',
    '/board/data/P2.js',
    '/board/data/p2.js/',
    '/board/data/sub/p2.js',
    '/board/data/',
    '/board/data',
    '/board/v8.html',
    '/board/_/other.js',
    '/board/_/../data/p2.js/..',
  ]) {
    const res = await call(route, url)
    assert.equal(res.status, 404, url)
    assert.notEqual(res.body, 'SECRET', url)
    assertHardened(res)
  }
})

test('only GET and HEAD', async () => {
  const [route] = setup().registered
  const res = await call(route, '/board/', 'POST')
  assert.equal(res.status, 405)
  assertHardened(res)
})

test('/board/_/launcher.js serves the launcher script', async () => {
  const [route] = setup().registered
  const res = await call(route, '/board/_/launcher.js')
  assert.equal(res.status, 200)
  assert.match(res.headers['content-type'], /javascript/)
  assert.equal(res.body, LAUNCHER)
  assertHardened(res)
})

function fakeDom() {
  const body = { children: [], appendChild(el) { this.children.push(el); el.parent = this } }
  const document = {
    body,
    getElementById: (id) => body.children.find((el) => el.id === id) ?? null,
    createElement: (tag) => ({
      tag, style: {}, listeners: {},
      addEventListener(type, fn) { this.listeners[type] = fn },
      remove() { const i = body.children.indexOf(this); if (i >= 0) body.children.splice(i, 1) },
    }),
    addEventListener() {}, removeEventListener() {},
  }
  return document
}

test('launcher mounts a fixed 📋 看板 button that opens /board/ in a new tab', () => {
  const document = fakeDom()
  vm.runInNewContext(LAUNCHER, { window: {}, document, Symbol, Object })
  const [a] = document.body.children
  assert.equal(a.tag, 'a')
  assert.equal(a.href, '/board/')
  assert.equal(a.target, '_blank')
  assert.match(a.rel, /noopener/)
  assert.match(a.textContent, /📋 看板/)
  assert.equal(a.style.position, 'fixed')
  assert.ok(a.style.right && a.style.bottom)
})

test('launcher registers with the DSH module loader and cleans up on dispose', () => {
  const document = fakeDom()
  let loaded
  vm.runInNewContext(LAUNCHER, { window: { __ModuleLoader__: { load: (m) => { loaded = m } } }, document, Symbol, Object })
  assert.equal(loaded.id, 'dsh-swarm-board')
  assert.equal(document.body.children.length, 0)
  const mod = loaded.factory(() => { throw new Error('no require needed') })
  const disposers = []
  mod.apply({ effect: (fn) => disposers.push(fn()) })
  assert.equal(document.body.children.length, 1)
  assert.equal(document.body.children[0].href, '/board/')
  disposers.forEach((d) => d())
  assert.equal(document.body.children.length, 0)
})

// ── 抓資料：apply 接上 startFetcher ──

test('with gh, apply starts the fetcher with projects / outDir / script, stop() on dispose', async () => {
  const { dataDir, disposers, startFetcher } = setup({ projects: [2, 3], fetchScript: '/opt/w/fetch-project.py', python: '/usr/bin/python3', fetchEveryMs: 5000 })
  await settle()
  assert.equal(startFetcher.calls.length, 1)
  const { opts } = startFetcher.calls[0]
  assert.deepEqual(opts.projects, [2, 3])
  assert.equal(opts.outDir, dataDir)
  assert.equal(opts.script, '/opt/w/fetch-project.py')
  assert.equal(opts.python, '/usr/bin/python3')
  assert.equal(opts.intervalMs, 5000)
  disposers.forEach((d) => d())
  assert.equal(startFetcher.calls[0].stopped, 1)
})

test('fetcher defaults: script ../../fetch-project.py beside the plugin, python3, every 10 minutes', async () => {
  const { startFetcher } = setup({ projects: [2] })
  await settle()
  const { opts } = startFetcher.calls[0]
  assert.equal(opts.script, new URL('../../fetch-project.py', import.meta.url).pathname)
  assert.equal(opts.python, 'python3')
  assert.equal(opts.intervalMs, 10 * 60_000)
})

test('fetchEveryMs: 0 or empty projects: startFetcher is not called', async () => {
  for (const config of [{ projects: [2], fetchEveryMs: 0 }, { projects: [] }, {}]) {
    const { startFetcher } = setup(config)
    await settle()
    assert.equal(startFetcher.calls.length, 0, JSON.stringify(config))
  }
})

test('disposed before the gh check returns: startFetcher is not called', async () => {
  let answer
  const { disposers, startFetcher } = setup({ projects: [2] }, { hasGitHub: () => new Promise((r) => { answer = r }) })
  await settle()
  disposers.forEach((d) => d())
  answer(true)
  await settle()
  assert.equal(startFetcher.calls.length, 0)
})

test('without gh: startFetcher is not called, one log line, status.json is {"github":false}', async () => {
  for (const hasGitHub of [() => false, () => Promise.resolve(false), () => { throw new Error('ENOENT gh') }, () => Promise.reject(new Error('x'))]) {
    const { registered: [route], startFetcher, logs } = setup({ projects: [2] }, { hasGitHub })
    await settle()
    assert.equal(startFetcher.calls.length, 0)
    assert.equal(logs.filter((l) => /gh/.test(l)).length, 1)
    const res = await call(route, '/board/data/status.json')
    assert.equal(res.status, 200)
    assert.match(res.headers['content-type'], /^application\/json/)
    assert.deepEqual(JSON.parse(res.body), { github: false })
    assertHardened(res)
  }
})

test('with gh: status.json is {"github":true}', async () => {
  const [route] = setup({ projects: [2] }).registered
  const res = await call(route, '/board/data/status.json')
  assert.equal(res.status, 200)
  assert.deepEqual(JSON.parse(res.body), { github: true })
  assert.equal((await call(route, '/board/data/status.json', 'HEAD')).body, undefined)
})

// gh logged in AFTER DSH started: no restart needed.
function lateLogin(config = {}) {
  let loggedIn = false, checks = 0, t = 0
  const intervals = []
  const s = setup({ projects: [2], githubRecheckMs: 30_000, ...config }, {
    hasGitHub: () => { checks++; return loggedIn },
    now: () => t,
    setInterval: (fn, ms) => { const h = { fn, ms }; intervals.push(h); return h },
    clearInterval: (h) => { h.cleared = true },
  })
  return { ...s, intervals, login: () => { loggedIn = true }, advance: (ms) => { t += ms }, checks: () => checks }
}

test('gh login after startup: status.json turns true and the fetcher starts, no restart', async () => {
  const b = lateLogin()
  const [route] = b.registered
  await settle()
  assert.equal(b.startFetcher.calls.length, 0)
  assert.deepEqual(JSON.parse((await call(route, '/board/data/status.json')).body), { github: false })

  b.login()
  // Within the recheck window the cached answer stands (gh is not run on every request).
  assert.deepEqual(JSON.parse((await call(route, '/board/data/status.json')).body), { github: false })
  b.advance(30_000)
  assert.deepEqual(JSON.parse((await call(route, '/board/data/status.json')).body), { github: true })
  await settle()
  assert.equal(b.startFetcher.calls.length, 1, 'the fetcher starts once gh is seen logged in')
  assert.ok(b.logs.some((l) => /fetcher started/.test(l)))
})

test('gh login after startup: the background recheck starts the fetcher without a page visit', async () => {
  const b = lateLogin()
  await settle()
  assert.equal(b.intervals.length, 1)
  assert.equal(b.intervals[0].ms, 30_000)
  b.login(); b.advance(30_000)
  b.intervals[0].fn()
  await settle()
  assert.equal(b.startFetcher.calls.length, 1)
  b.intervals[0].fn() // once running, the timer no longer asks gh
  await settle()
  assert.equal(b.startFetcher.calls.length, 1)
})

test('gh login after startup: actions stop answering 409', async () => {
  const b = lateLogin()
  const route = b.registered[0]
  const post = async () => {
    const res = { status: 0, headers: {}, body: undefined, headersSent: false,
      writeHead(s, h) { this.status = s; this.headers = h; this.headersSent = true }, end(x) { this.body = x === undefined ? undefined : String(x) }, destroy() {} }
    const req = { method: 'POST', url: '/board/api/status', headers: { 'content-type': 'application/json', host: 'h' },
      on(ev, fn) { if (ev === 'data') fn(Buffer.from('{}')); if (ev === 'end') fn() } }
    await route.handler(req, res)
    return res.status
  }
  assert.equal(await post(), 409)
  b.login(); b.advance(30_000)
  assert.notEqual(await post(), 409)
})

test('gh check is shared and throttled: concurrent requests run gh once', async () => {
  const b = lateLogin()
  const [route] = b.registered
  await settle()
  const before = b.checks()
  b.advance(30_000)
  await Promise.all([1, 2, 3, 4].map(() => call(route, '/board/data/status.json')))
  assert.equal(b.checks() - before, 1)
})

test('disposing clears the gh recheck timer', async () => {
  const b = lateLogin()
  await settle()
  b.disposers.forEach((d) => d())
  assert.equal(b.intervals[0].cleared, true)
})

test('status.json waits for the gh check', async () => {
  let answer
  const [route] = setup({}, { hasGitHub: () => new Promise((r) => { answer = r }) }).registered
  const pending = call(route, '/board/data/status.json')
  await settle()
  answer(true)
  assert.deepEqual(JSON.parse((await pending).body), { github: true })
})

test('projects.json above dataDir is written from config.projects only when missing', async () => {
  const { root } = setup({ projects: [7, 2] })
  const file = join(root, 'projects.json')
  assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), { default: 7, projects: [7, 2] })

  const root2 = mkdtempSync(join(tmpdir(), 'swarm-board-pj-'))
  const dataDir = join(root2, 'data')
  writeFileSync(join(root2, 'projects.json'), '{"default":3,"projects":[3],"publish":"/x"}')
  setup({ dataDir, projects: [2] })
  assert.equal(readFileSync(join(root2, 'projects.json'), 'utf8'), '{"default":3,"projects":[3],"publish":"/x"}')
})

test('no projects: projects.json is not written', () => {
  const { root } = setup({ projects: [] })
  assert.equal(existsSync(join(root, 'projects.json')), false)
})
