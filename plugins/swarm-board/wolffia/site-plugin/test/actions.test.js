// 放行／重新放行／自動合併／改狀態 through the /board route, with a fake gh. No DSH, no network.
import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import * as plugin from '../lib/index.js'

const EPIC = 'https://github.com/acme/swarm/issues/543'

// Fake gh: records every call; `issue view` answers labels/state, graphql answers by query.
function fakeGh({ labels = ['epic'], state = 'OPEN', issueState = 'OPEN', inProject = true } = {}) {
  const calls = []
  const gh = async (args) => {
    calls.push(args)
    if (args[0] === 'issue' && args[1] === 'view') return JSON.stringify({ labels: labels.map((name) => ({ name })), state })
    if (args[0] === 'issue') return ''
    const query = args.find((a) => a.startsWith('query=')).slice(6)
    if (query.includes('organization(login')) {
      return JSON.stringify({ data: { organization: { projectV2: { id: 'P1', field: { id: 'F1', options: [
        { id: 'O1', name: '待辦' }, { id: 'O2', name: '進行中' }, { id: 'O3', name: '完成' }] } } } } })
    }
    if (query.includes('resource(url')) {
      return JSON.stringify({ data: { resource: { id: 'I1', state: issueState, projectItems: { nodes: [
        { id: 'X0', project: { id: 'P9' } }, ...(inProject ? [{ id: 'X1', project: { id: 'P1' } }] : [])] } } } })
    }
    return JSON.stringify({ data: {} })
  }
  gh.calls = calls
  gh.edits = () => calls.filter((a) => a[0] === 'issue' && a[1] === 'edit').map((a) => a.slice(3))
  gh.comments = () => calls.filter((a) => a[0] === 'issue' && a[1] === 'comment').map((a) => a[4])
  gh.queries = () => calls.filter((a) => a[0] === 'api').map((a) => a.find((x) => x.startsWith('query=')))
  return gh
}

const settle = () => new Promise((r) => setImmediate(r))

function setup({ gh = fakeGh(), github = true, projects = [3] } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'swarm-board-actions-'))
  const dataDir = join(root, 'data')
  mkdirSync(dataDir)
  writeFileSync(join(dataDir, 'p3.js'), 'window.PROJECTS=window.PROJECTS||{};window.PROJECTS[3]=' + JSON.stringify({
    title: 'T', url: 'https://github.com/orgs/acme/projects/3', items: [{ n: 543, t: 'EPIC', u: EPIC, status: '進行中' }],
  }) + ';')
  const registered = []
  const fetchNow = []
  const ctx = {
    logger: () => ({ warn() {}, error() {}, info() {} }),
    effect: (fn) => { fn() },
    connection: {},
    webServer: { register: (r) => { registered.push(r); return () => {} } },
  }
  plugin.apply(ctx, { dataDir, projects, digestEveryMs: 0 }, {
    hasGitHub: () => github, gh, hostname: () => 'site.example',
    startFetcher: () => ({ stop() {}, fetchNow: (n) => { fetchNow.push(n); return true } }),
  })
  return { route: registered[0], gh, fetchNow }
}

async function post(route, path, body, headers = {}) {
  const raw = typeof body === 'string' ? body : JSON.stringify(body)
  const req = new EventEmitter()
  req.method = 'POST'
  req.url = path
  req.headers = { 'content-type': 'application/json', host: 'site.example', ...headers }
  const res = {
    status: 0, headers: {}, body: undefined, headersSent: false,
    writeHead(status, h) { this.status = status; this.headers = h; this.headersSent = true; return this },
    end(b) { this.body = b === undefined ? undefined : String(b) },
    destroy() {},
  }
  const done = route.handler(req, res)
  setImmediate(() => { req.emit('data', Buffer.from(raw)); req.emit('end') })
  await done
  await settle()
  return { status: res.status, json: res.body ? JSON.parse(res.body) : undefined }
}

test('go adds swarm:go and comments who released it', async () => {
  const { route, gh, fetchNow } = setup()
  await settle()
  const r = await post(route, '/board/api/epic/go', { url: EPIC })
  assert.equal(r.status, 200)
  assert.deepEqual(r.json, { ok: true, by: 'site.example', already: false, auto_merge: false })
  assert.deepEqual(gh.edits(), [['--add-label', 'swarm:go']])
  assert.equal(gh.comments().length, 1)
  assert.match(gh.comments()[0], /由 站台任務星系（site\.example）放行/)
  assert.deepEqual(fetchNow, [3])
})

test('go with auto_merge adds both labels', async () => {
  const { route, gh } = setup()
  const r = await post(route, '/board/api/epic/go', { url: EPIC, auto_merge: true })
  assert.equal(r.status, 200)
  assert.equal(r.json.auto_merge, true)
  assert.deepEqual(gh.edits(), [['--add-label', 'swarm:go', '--add-label', 'swarm:auto-merge']])
})

test('go on an already released EPIC answers already:true and changes nothing', async () => {
  const { route, gh } = setup({ gh: fakeGh({ labels: ['epic', 'swarm:go'] }) })
  const r = await post(route, '/board/api/epic/go', { url: EPIC })
  assert.equal(r.status, 200)
  assert.equal(r.json.already, true)
  assert.deepEqual(gh.edits(), [])
  assert.deepEqual(gh.comments(), [])
})

test('go on a non-EPIC or closed EPIC is 400', async () => {
  let { route, gh } = setup({ gh: fakeGh({ labels: ['bug'] }) })
  let r = await post(route, '/board/api/epic/go', { url: EPIC })
  assert.equal(r.status, 400)
  assert.match(r.json.error, /不是 EPIC/)
  assert.deepEqual(gh.edits(), [])
  ;({ route } = setup({ gh: fakeGh({ state: 'CLOSED' }) }))
  r = await post(route, '/board/api/epic/go', { url: EPIC })
  assert.equal(r.status, 400)
  assert.match(r.json.error, /已關閉/)
})

test('go with a non-boolean auto_merge is 400', async () => {
  const { route } = setup()
  assert.equal((await post(route, '/board/api/epic/go', { url: EPIC, auto_merge: 'yes' })).status, 400)
})

test('rego removes swarm:go and adds it back', async () => {
  const { route, gh } = setup({ gh: fakeGh({ labels: ['epic', 'swarm:go'] }) })
  const r = await post(route, '/board/api/epic/rego', { url: EPIC })
  assert.equal(r.status, 200)
  assert.deepEqual(r.json, { ok: true, by: 'site.example', already: false })
  assert.deepEqual(gh.edits(), [['--remove-label', 'swarm:go'], ['--add-label', 'swarm:go']])
  assert.match(gh.comments()[0], /由 站台任務星系（site\.example）重新放行/)
})

test('rego on a never released EPIC is 400', async () => {
  const { route, gh } = setup()
  const r = await post(route, '/board/api/epic/rego', { url: EPIC })
  assert.equal(r.status, 400)
  assert.match(r.json.error, /還沒放行過/)
  assert.deepEqual(gh.edits(), [])
})

test('automerge turns swarm:auto-merge on and off', async () => {
  let { route, gh } = setup({ gh: fakeGh({ labels: ['epic', 'swarm:go'] }) })
  let r = await post(route, '/board/api/epic/automerge', { url: EPIC, on: true })
  assert.equal(r.status, 200)
  assert.deepEqual(r.json, { ok: true, by: 'site.example', already: false, on: true })
  assert.deepEqual(gh.edits(), [['--add-label', 'swarm:auto-merge']])
  assert.match(gh.comments()[0], /由 站台任務星系（site\.example）開啟自動合併/)

  ;({ route, gh } = setup({ gh: fakeGh({ labels: ['epic', 'swarm:go', 'swarm:auto-merge'] }) }))
  r = await post(route, '/board/api/epic/automerge', { url: EPIC, on: false })
  assert.equal(r.status, 200)
  assert.equal(r.json.on, false)
  assert.deepEqual(gh.edits(), [['--remove-label', 'swarm:auto-merge']])
  assert.match(gh.comments()[0], /關閉自動合併/)

  r = await post(route, '/board/api/epic/automerge', { url: EPIC, on: true })
  assert.equal(r.json.already, true)
  assert.equal((await post(route, '/board/api/epic/automerge', { url: EPIC })).status, 400)
})

test('status sets the Status field of the issue in that project', async () => {
  const { route, gh, fetchNow } = setup()
  await settle()
  const r = await post(route, '/board/api/status', { project: 3, url: EPIC, status: '進行中' })
  assert.equal(r.status, 200)
  assert.deepEqual(r.json, { ok: true, by: 'site.example', state: 'OPEN' })
  const calls = gh.calls.filter((a) => a[0] === 'api')
  assert.ok(calls[0].includes('o=acme') && calls[0].includes('n=3'))
  const update = calls.find((a) => a.some((x) => x.includes('updateProjectV2ItemFieldValue')))
  assert.ok(update.includes('p=P1') && update.includes('i=X1') && update.includes('f=F1') && update.includes('o=O2'))
  assert.ok(!gh.queries().some((q) => q.includes('closeIssue')))
  assert.deepEqual(fetchNow, [3])
})

test('status 完成 also closes an open issue', async () => {
  const { route, gh } = setup()
  const r = await post(route, '/board/api/status', { project: 3, url: EPIC, status: '完成' })
  assert.equal(r.status, 200)
  assert.equal(r.json.state, 'CLOSED')
  assert.ok(gh.queries().some((q) => q.includes('closeIssue')))
})

test('status errors: unknown column, issue not in project, project not configured', async () => {
  let { route, gh } = setup()
  let r = await post(route, '/board/api/status', { project: 3, url: EPIC, status: '不存在' })
  assert.equal(r.status, 400)
  assert.match(r.json.error, /沒有「不存在」欄/)
  ;({ route } = setup({ gh: fakeGh({ inProject: false }) }))
  r = await post(route, '/board/api/status', { project: 3, url: EPIC, status: '進行中' })
  assert.equal(r.status, 400)
  assert.match(r.json.error, /不在這個 project/)
  ;({ route, gh } = setup())
  r = await post(route, '/board/api/status', { project: 7, url: EPIC, status: '進行中' })
  assert.equal(r.status, 400)
  assert.deepEqual(gh.calls, [])
})

test('without a logged-in gh every action is 409', async () => {
  const { route, gh } = setup({ github: false })
  for (const path of ['/board/api/epic/go', '/board/api/epic/rego', '/board/api/epic/automerge', '/board/api/status']) {
    const r = await post(route, path, { url: EPIC, on: true, project: 3, status: '完成' })
    assert.equal(r.status, 409)
    assert.equal(r.json.error, '尚未連接 GitHub：請在站台的 AI 對話說「幫我連接 GitHub」')
  }
  assert.deepEqual(gh.calls, [])
})

test('wrong Content-Type is 415, other site Origin is 403, GET is 405', async () => {
  const { route, gh } = setup()
  assert.equal((await post(route, '/board/api/epic/go', { url: EPIC }, { 'content-type': 'text/plain' })).status, 415)
  assert.equal((await post(route, '/board/api/epic/go', { url: EPIC }, { origin: 'https://evil.example' })).status, 403)
  assert.equal((await post(route, '/board/api/epic/go', { url: EPIC }, { origin: 'https://site.example' })).status, 200)
  const res = { writeHead(s) { this.status = s }, end() {} }
  await route.handler({ method: 'GET', url: '/board/api/status', headers: {} }, res)
  assert.equal(res.status, 405)
  assert.equal(gh.edits().length, 1)
})

test('URLs that are not GitHub issues are 400; bodies over 4KB are 413', async () => {
  const { route, gh } = setup()
  for (const url of ['https://example.com/o/r/issues/1', 'https://github.com/o/r/pull/1', 'https://github.com/o/r/issues/1/x', 42]) {
    const r = await post(route, '/board/api/epic/go', { url })
    assert.equal(r.status, 400)
    assert.match(r.json.error, /不是 GitHub issue 網址/)
  }
  assert.equal((await post(route, '/board/api/status', { project: 3, url: 'https://example.com/x', status: '完成' })).status, 400)
  assert.equal((await post(route, '/board/api/epic/go', { url: EPIC, pad: 'x'.repeat(5000) })).status, 413)
  assert.equal((await post(route, '/board/api/epic/go', '{nope')).status, 400)
  assert.deepEqual(gh.calls, [])
})

test('gh failures are 502', async () => {
  const gh = async () => { throw new Error('HTTP 401: Bad credentials') }
  const { route } = setup({ gh })
  const r = await post(route, '/board/api/epic/go', { url: EPIC })
  assert.equal(r.status, 502)
  assert.match(r.json.error, /GitHub 更新失敗：HTTP 401/)
})
