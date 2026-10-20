// 「＋ 加入 project」：GET /board/api/projects/available 列出站台 gh 登入者與所屬組織開著的 project（依卡片數排序、
// 已加入的 added:true、快取 10 分鐘），POST /board/api/projects/add 只收清單裡有的、寫進 projects.json、立刻抓一次。
// 假 gh（GraphQL 回固定資料）、假 fetchOne（寫 index.js），不連網。
import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import * as plugin from '../lib/index.js'
import { AVAILABLE_TTL_MS, createProjectPicker, listProjects } from '../lib/projects.js'

const page = (nodes) => ({ nodes: nodes.map(([number, title, items, closed = false]) => ({ number, title, closed, items: { totalCount: items } })) })
const VIEWER = {
  login: 'Alice',
  projectsV2: page([[5, 'Alice 自己的', 4], [6, '關掉的', 99, true]]),
  organizations: { nodes: [
    { login: 'acme', projectsV2: page([[1, 'TI 一號', 30], [2, 'TI 二號', 10], [9, 'TI 舊的', 500, true]]) },
    { login: 'Other', projectsV2: page([[1, 'Bright 一號', 20]]) },
  ] },
}

function fakeGh(viewer = VIEWER) {
  const calls = []
  const gh = async (args) => {
    calls.push(args)
    assert.equal(args[0], 'api')
    assert.equal(args[1], 'graphql')
    assert.ok(!args.includes('project'), '站台的 gh 沒有 gh project')
    return JSON.stringify({ data: { viewer: gh.viewer } })
  }
  gh.viewer = viewer
  gh.calls = calls
  return gh
}

const settle = () => new Promise((r) => setImmediate(r))

function setup({ projects = [2], gh = fakeGh(), github = true, projectsJson } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'swarm-board-addproject-'))
  const dataDir = join(root, 'data')
  mkdirSync(dataDir)
  if (projectsJson) writeFileSync(join(root, 'projects.json'), JSON.stringify(projectsJson))
  const fetched = [], started = []
  // Fake fetch-project.py --one: writes index.js from projects.json, as the real script does.
  const fetchOne = async (key) => {
    fetched.push(key)
    const conf = JSON.parse(readFileSync(join(root, 'projects.json'), 'utf8'))
    const rows = conf.projects.map((k) => ({ num: typeof k === 'string' ? Number(k.split('/')[1]) : k,
      owner: typeof k === 'string' ? k.split('/')[0] : 'acme', key: k, title: `T${k}`, open: 1, total: 1 }))
    writeFileSync(join(dataDir, 'index.js'), 'window.PROJECT_INDEX=' + JSON.stringify({ default: conf.default, projects: rows }) + ';\n')
  }
  const registered = []
  const ctx = {
    logger: () => ({ warn() {}, error() {}, info() {} }),
    effect: (fn) => { fn() },
    connection: {},
    webServer: { register: (r) => { registered.push(r); return () => {} } },
  }
  plugin.apply(ctx, { dataDir, projects, digestEveryMs: 0 }, {
    hasGitHub: () => github, gh, fetchOne, hostname: () => 'site.example',
    startFetcher: (o) => { started.push(o); return { stop() {}, fetchNow() { return true } } },
  })
  return { route: registered[0], gh, fetched, started, dataDir, root, projectsFile: join(root, 'projects.json') }
}

function request(route, method, path, body, headers = {}) {
  const req = new EventEmitter()
  req.method = method
  req.url = path
  req.headers = { 'content-type': 'application/json', host: 'site.example', ...headers }
  const res = {
    status: 0, headers: {}, body: undefined, headersSent: false,
    writeHead(status, h) { this.status = status; this.headers = h; this.headersSent = true; return this },
    end(b) { this.body = b === undefined ? undefined : String(b) },
    destroy() {},
  }
  const done = route.handler(req, res)
  if (body !== undefined) setImmediate(() => { req.emit('data', Buffer.from(JSON.stringify(body))); req.emit('end') })
  return done.then(settle).then(() => ({ status: res.status, headers: res.headers, json: /json/.test(res.headers['content-type'] ?? '') && res.body ? JSON.parse(res.body) : undefined }))
}

const get = (route) => request(route, 'GET', '/board/api/projects/available')
const add = (route, body, headers) => request(route, 'POST', '/board/api/projects/add', body, headers)

test('available: only open projects of the gh login and its orgs, most cards first, {owner, number, title, items, added}', async () => {
  const { route } = setup({ projects: [2, 'other/1'] })
  await settle()
  const r = await get(route)
  assert.equal(r.status, 200)
  assert.equal(r.headers['cache-control'], 'no-store')
  assert.equal(r.json.github, true)
  assert.deepEqual(r.json.projects, [
    { owner: 'acme', number: 1, title: 'TI 一號', items: 30, added: false },
    { owner: 'Other', number: 1, title: 'Bright 一號', items: 20, added: true },
    { owner: 'acme', number: 2, title: 'TI 二號', items: 10, added: true },
    { owner: 'Alice', number: 5, title: 'Alice 自己的', items: 4, added: false },
  ])
  assert.ok(r.json.projects.every((p) => !/關掉的|舊的/.test(p.title)), '關閉的 project 不列')
})

test('available is cached for 10 minutes; added is always current', async () => {
  const gh = fakeGh()
  let t = 1_000
  const projects = [2]
  const picker = createProjectPicker({ gh, projects, file: '/nonexistent/projects.json', dataDir: '/nonexistent', fetchOne: async () => {}, now: () => t })
  await picker.available()
  t += AVAILABLE_TTL_MS - 1
  projects.push(1)
  const again = await picker.available()
  assert.equal(gh.calls.length, 1, '10 分鐘內不重查')
  assert.equal(again.projects.find((p) => p.number === 1 && p.owner === 'acme').added, true)
  t += 2
  await picker.available()
  assert.equal(gh.calls.length, 2, '過了 10 分鐘重查')
})

test('available without a logged-in gh: {github:false}, gh never asked', async () => {
  const { route, gh } = setup({ github: false })
  await settle()
  const r = await get(route)
  assert.equal(r.status, 200)
  assert.deepEqual(r.json, { github: false })
  assert.equal(gh.calls.length, 0)
  assert.equal((await add(route, { owner: 'acme', number: 1 })).status, 409)
})

test('add writes projects.json (other entries kept), fetches at once and answers the new index', async () => {
  const { route, fetched, projectsFile, started } = setup({ projectsJson: { default: 2, projects: [2, 'other-org/7'], note: 'keep me' } })
  await settle()
  const r = await add(route, { owner: 'Other', number: 1 })
  assert.equal(r.status, 200, JSON.stringify(r.json))
  assert.equal(r.json.ok, true)
  assert.equal(r.json.key, 'other/1')
  assert.equal(r.json.already, false)
  assert.deepEqual(JSON.parse(readFileSync(projectsFile, 'utf8')), { default: 2, projects: [2, 'other-org/7', 'other/1'], note: 'keep me' })
  assert.deepEqual(fetched, ['other/1'], '立刻抓一次')
  assert.deepEqual(r.json.index.projects.map((x) => x.key), [2, 'other-org/7', 'other/1'])
  // The running fetcher shares the live list: the next round includes it.
  assert.ok(started[0].projects.includes('other/1'))
  // And the list now marks it.
  const list = await get(route)
  assert.equal(list.json.projects.find((p) => p.owner === 'Other').added, true)
  // api/status accepts it now (an unknown project would be 400 before reaching gh).
  const s = await request(route, 'POST', '/board/api/status', { project: 'other/1', url: 'https://github.com/other/r/issues/1', status: '待辦' })
  assert.notEqual(s.json.error, '不允許的 project')
})

test('add: acme N is written as N; projects.json is created when missing', async () => {
  const { route, projectsFile, fetched } = setup({ projects: [] })
  await settle()
  assert.equal(existsSync(projectsFile), false)
  const r = await add(route, { owner: 'Acme', number: 1 })
  assert.equal(r.status, 200, JSON.stringify(r.json))
  assert.equal(r.json.key, 1)
  assert.deepEqual(JSON.parse(readFileSync(projectsFile, 'utf8')), { default: 1, projects: [1] })
  assert.deepEqual(fetched, [1])
})

test('add twice: no error, projects.json unchanged, already:true', async () => {
  const { route, projectsFile, fetched } = setup()
  await settle()
  const a = await add(route, { owner: 'acme', number: 1 })
  const before = readFileSync(projectsFile, 'utf8')
  const b = await add(route, { owner: 'acme', number: 1 })
  assert.equal(a.status, 200)
  assert.equal(b.status, 200, JSON.stringify(b.json))
  assert.equal(b.json.already, true)
  assert.equal(readFileSync(projectsFile, 'utf8'), before)
  assert.deepEqual(JSON.parse(before).projects, [2, 1])
  assert.deepEqual(fetched, [1])
  // Already on the board from config: also fine.
  const c = await add(route, { owner: 'acme', number: 2 })
  assert.equal(c.status, 200)
  assert.equal(c.json.already, true)
})

test('add rejects a project not in the list (asked again on GitHub, not trusted from the page)', async () => {
  const { route, gh, projectsFile, fetched } = setup()
  await settle()
  await get(route) // the list is cached now
  const n = gh.calls.length
  for (const body of [
    { owner: 'acme', number: 9 },  // closed
    { owner: 'Alice', number: 6 },             // closed
    { owner: 'someone-else', number: 1 },      // not ours
    { owner: 'acme', number: 77 }, // no such project
    { owner: '../x', number: 1 }, { owner: 'acme' }, { number: 1 }, { owner: 'a', number: 1.5 }, [],
  ]) {
    const r = await add(route, body)
    assert.equal(r.status, 400, JSON.stringify(body))
    assert.ok(r.json.error)
  }
  assert.ok(gh.calls.length > n, 'add 重查 GitHub，不用快取')
  // Gone from GitHub since the list was cached: rejected too.
  gh.viewer = { ...VIEWER, organizations: { nodes: [] } }
  assert.equal((await add(route, { owner: 'Other', number: 1 })).status, 400)
  assert.deepEqual(JSON.parse(readFileSync(projectsFile, 'utf8')).projects, [2])
  assert.deepEqual(fetched, [])
})

test('add: JSON only, same origin only, POST only', async () => {
  const { route } = setup()
  await settle()
  assert.equal((await add(route, { owner: 'acme', number: 1 }, { 'content-type': 'text/plain' })).status, 415)
  assert.equal((await add(route, { owner: 'acme', number: 1 }, { origin: 'https://evil.example' })).status, 403)
  assert.equal((await request(route, 'GET', '/board/api/projects/add')).status, 405)
  assert.equal((await request(route, 'POST', '/board/api/projects/available', {})).status, 405)
})

test('projects added earlier (projects.json) are fetched after a restart', async () => {
  const { started } = setup({ projects: [2], projectsJson: { default: 2, projects: [2, 'other/1'] } })
  await settle()
  assert.deepEqual(started[0].projects, [2, 'other/1'])
})

test('a site with no projects yet starts fetching once one is added', async () => {
  const { route, started } = setup({ projects: [] })
  await settle()
  assert.equal(started.length, 0)
  assert.equal((await add(route, { owner: 'Alice', number: 5 })).status, 200)
  assert.equal(started.length, 1)
  assert.deepEqual(started[0].projects, ['alice/5'])
})

test('listProjects uses one GraphQL query and no `gh project`', async () => {
  const gh = fakeGh()
  const list = await listProjects(gh)
  assert.equal(gh.calls.length, 1)
  assert.match(gh.calls[0].find((a) => a.startsWith('query=')), /viewer\{login projectsV2.*organizations/)
  assert.deepEqual(list.map((p) => p.items), [30, 20, 10, 4])
})
