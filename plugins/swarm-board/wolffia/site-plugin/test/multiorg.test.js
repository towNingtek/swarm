// 看板認得其他組織的 project：config.projects 接受整數（acme）與 "組織/編號"；
// 抓資料、/board/api/status（拖曳改狀態）、運行中都用該 project 自己的組織。假 gh、假 fetcher，不連網。
import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import * as plugin from '../lib/index.js'
import { dataFile, projectKey } from '../lib/actions.js'
import { startFetcher } from '../lib/fetcher.js'

const BRIGHT = 'https://github.com/other/site/issues/7'
const TOWN = 'https://github.com/acme/swarm/issues/9'

const boardFile = (key, board) => 'window.PROJECTS=window.PROJECTS||{};window.PROJECTS[' + JSON.stringify(key) + ']=' + JSON.stringify(board) + ';\n'

// Fake gh: records every call; the project query answers per org, so a wrong org is visible.
function fakeGh() {
  const calls = []
  const gh = async (args) => {
    calls.push(args)
    if (args[0] !== 'api') return ''
    const query = args.find((a) => a.startsWith('query=')).slice(6)
    const v = (k) => args.find((a) => a.startsWith(`${k}=`))?.slice(k.length + 1)
    if (query.includes('organization(login')) {
      const id = `P:${v('o')}/${v('n')}`
      return JSON.stringify({ data: { organization: { projectV2: { id, field: { id: 'F1', options: [
        { id: 'O1', name: '待辦' }, { id: 'O2', name: '進行中' }, { id: 'O3', name: '完成' }] } } } } })
    }
    if (query.includes('resource(url')) {
      return JSON.stringify({ data: { resource: { id: 'I1', state: 'OPEN', projectItems: { nodes: [
        { id: 'X-town', project: { id: 'P:acme/1' } }, { id: 'X-bright', project: { id: 'P:other/1' } }] } } } })
    }
    return JSON.stringify({ data: {} })
  }
  gh.calls = calls
  gh.projectQueries = () => calls.filter((a) => a[0] === 'api' && a.some((x) => x.includes('organization(login')))
    .map((a) => [a.find((x) => x.startsWith('o='))?.slice(2), a.find((x) => x.startsWith('n='))?.slice(2)])
  gh.mutations = () => calls.filter((a) => a[0] === 'api' && a.some((x) => x.startsWith('query=mutation')))
  return gh
}

const settle = () => new Promise((r) => setImmediate(r))

function setup({ projects = [1, 'other/1'], gh = fakeGh(), runningQuery } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'swarm-board-multiorg-'))
  const dataDir = join(root, 'data')
  mkdirSync(dataDir)
  // acme 1 and other 1: same number, different org.
  writeFileSync(join(dataDir, 'p1.js'), boardFile(1, { num: 1, title: 'TI', url: 'https://github.com/orgs/acme/projects/1',
    fetched: '2026-10-20T01:00:00Z', items: [{ n: 9, t: 't', u: TOWN, status: '待辦', state: 'OPEN' }] }))
  writeFileSync(join(dataDir, 'p-other-1.js'), boardFile('other/1', { num: 1, owner: 'other', key: 'other/1',
    title: 'Bright', url: 'https://github.com/orgs/other/projects/1', fetched: '2026-10-20T02:00:00Z',
    items: [{ n: 7, t: 'b', u: BRIGHT, status: '待辦', state: 'OPEN' }] }))
  const registered = [], fetchNow = [], started = []
  const ctx = {
    logger: () => ({ warn() {}, error() {}, info() {} }),
    effect: (fn) => { fn() },
    connection: {},
    webServer: { register: (r) => { registered.push(r); return () => {} } },
  }
  plugin.apply(ctx, { dataDir, projects, digestEveryMs: 0 }, {
    hasGitHub: () => true, gh, hostname: () => 'site.example', runningQuery,
    startFetcher: (o) => { started.push(o); return { stop() {}, fetchNow: (n) => { fetchNow.push(n); return true } } },
  })
  return { route: registered[0], gh, fetchNow, started, dataDir, root }
}

function request(route, method, path, body) {
  const req = new EventEmitter()
  req.method = method
  req.url = path
  req.headers = { 'content-type': 'application/json', host: 'site.example' }
  const res = {
    status: 0, headers: {}, body: undefined, headersSent: false,
    writeHead(status, h) { this.status = status; this.headers = h; this.headersSent = true; return this },
    end(b) { this.body = b === undefined ? undefined : String(b) },
    destroy() {},
  }
  const done = route.handler(req, res)
  if (body !== undefined) setImmediate(() => { req.emit('data', Buffer.from(JSON.stringify(body))); req.emit('end') })
  return done.then(settle).then(() => res)
}

const post = async (route, path, body) => {
  const res = await request(route, 'POST', path, body)
  return { status: res.status, json: res.body ? JSON.parse(res.body) : undefined }
}

test('projectKey: integers are acme; "org/number" for another org; anything else null', () => {
  assert.equal(projectKey(2), 2)
  assert.equal(projectKey('2'), 2)
  assert.equal(projectKey('acme/2'), 2)
  assert.equal(projectKey('Acme/2'), 2)
  assert.equal(projectKey('other/1'), 'other/1')
  assert.equal(projectKey(' Other/1 '), 'other/1')
  for (const bad of [0, -1, 1.5, '', 'x', 'other/x', 'other/0', '../1', '/1', 'a/b/1', '-a/1', 'a_b/1', null, {}, true]) {
    assert.equal(projectKey(bad), null, JSON.stringify(bad))
  }
  assert.equal(dataFile(2), 'p2.js')
  assert.equal(dataFile('other/1'), 'p-other-1.js')
})

test('config.projects accepts "other/1" beside integers: fetcher and projects.json get both', async () => {
  const { started, dataDir, root } = setup({ projects: [3, 'other/1', 'Acme/3', '../1', 'x/y', 0] })
  await settle()
  assert.equal(started.length, 1)
  assert.deepEqual(started[0].projects, [3, 'other/1'])
  assert.equal(started[0].outDir, dataDir)
  assert.deepEqual(JSON.parse(readFileSync(join(root, 'projects.json'), 'utf8')), { default: 3, projects: [3, 'other/1'] })
})

test('the fetcher runs fetch-project.py --one other/1', async () => {
  const runs = []
  const f = startFetcher({ run: async (file, args) => { runs.push(args) }, script: 'fetch-project.py', projects: [2, 'other/1'],
    outDir: '/d', setInterval: () => ({}), clearInterval: () => {} })
  await settle(); await settle()
  f.stop()
  assert.deepEqual(runs, [['fetch-project.py', '--one', '2', '/d'], ['fetch-project.py', '--one', 'other/1', '/d']])
})

test('/board/data/p-other-1.js is served; look-alike names are 404', async () => {
  const { route } = setup()
  const ok = await request(route, 'GET', '/board/data/p-other-1.js')
  assert.equal(ok.status, 200)
  assert.match(ok.body, /window\.PROJECTS\["other\/1"\]=/)
  for (const bad of ['p-other.js', 'p-..-1.js', 'p-Bright-1.js', 'p-bright_ideass-1.js', 'p-other-1.json']) {
    assert.equal((await request(route, 'GET', `/board/data/${bad}`)).status, 404, bad)
  }
})

test('status for a "other/1" card uses other\'s project 1, not acme 1', async () => {
  const { route, gh, fetchNow } = setup()
  const r = await post(route, '/board/api/status', { project: 'other/1', url: BRIGHT, status: '進行中' })
  assert.equal(r.status, 200, JSON.stringify(r.json))
  assert.equal(r.json.ok, true)
  assert.deepEqual(gh.projectQueries(), [['other', '1']])
  const m = gh.mutations()[0]
  assert.ok(m.includes('p=P:other/1') && m.includes('i=X-bright') && m.includes('o=O2'), m.join(' '))
  assert.deepEqual(fetchNow, ['other/1'])
})

test('status for acme 1 (integer) still uses acme', async () => {
  const { route, gh, fetchNow } = setup()
  for (const project of [1, '1']) {
    gh.calls.length = 0
    const r = await post(route, '/board/api/status', { project, url: TOWN, status: '完成' })
    assert.equal(r.status, 200, JSON.stringify(r.json))
    assert.deepEqual(gh.projectQueries(), [['acme', '1']])
    assert.ok(gh.mutations()[0].includes('p=P:acme/1'))
  }
  assert.deepEqual(fetchNow, [1, 1])
})

test('a project not in config.projects is still refused, with no GitHub call', async () => {
  const { route, gh, fetchNow } = setup({ projects: ['other/1'] })
  for (const project of [1, '1', 'acme/1', 'other-org/1', 'other/2', 'other', '../1', null]) {
    const r = await post(route, '/board/api/status', { project, url: BRIGHT, status: '進行中' })
    assert.equal(r.status, 400, JSON.stringify(project))
    assert.equal(r.json.error, '不允許的 project')
  }
  assert.equal(gh.calls.length, 0)
  assert.deepEqual(fetchNow, [])
})

test('an epic action on a other issue re-fetches the other board', async () => {
  const gh = fakeGh()
  const wrapped = async (args) => args[0] === 'issue' && args[1] === 'view'
    ? JSON.stringify({ labels: [{ name: 'epic' }], state: 'OPEN' }) : gh(args)
  const { route, fetchNow } = setup({ gh: wrapped })
  const r = await post(route, '/board/api/epic/go', { url: BRIGHT })
  assert.equal(r.status, 200, JSON.stringify(r.json))
  assert.deepEqual(fetchNow, ['other/1'])
})

test('running asks about other too, and data_at carries "other/1"', async () => {
  const asked = []
  const { route } = setup({ projects: [1, 'other/1'], runningQuery: async (q, vars) => { asked.push(vars.q); return { search: { nodes: [
    { number: 7, title: 'b', repository: { nameWithOwner: 'other/site' }, comments: { nodes: [] } }] } } } })
  const res = await request(route, 'GET', '/board/api/running')
  assert.equal(res.status, 200)
  const body = JSON.parse(res.body)
  assert.equal(asked.length, 1)
  assert.match(asked[0], /org:other\b/)
  assert.match(asked[0], /org:acme\b/)
  assert.deepEqual(body.items.map((x) => x.repo), ['other/site'])
  assert.equal(body.data_at['other/1'], '2026-10-20T02:00:00Z')
  assert.equal(body.data_at[1], '2026-10-20T01:00:00Z')
})

test('running for a site with only "other/1" and no data yet still asks other', async () => {
  const asked = []
  const root = mkdtempSync(join(tmpdir(), 'swarm-board-multiorg-empty-'))
  const registered = []
  plugin.apply({ logger: () => ({ warn() {}, error() {}, info() {} }), effect: (fn) => { fn() }, connection: {},
    webServer: { register: (r) => { registered.push(r); return () => {} } } },
  { dataDir: join(root, 'data'), projects: ['other/1'], digestEveryMs: 0, fetchEveryMs: 0 },
  { hasGitHub: () => true, runningQuery: async (q, vars) => { asked.push(vars.q); return { search: { nodes: [] } } } })
  const res = await request(registered[0], 'GET', '/board/api/running')
  assert.equal(res.status, 200)
  assert.deepEqual(asked.map((q) => q.split(' ')[0]), ['org:other'])
})

test('default org is configurable: setDefaultOrg makes bare numbers belong to it', async () => {
  const m = await import('../lib/actions.js?org-test')
  m.setDefaultOrg('Acme')
  assert.equal(m.projectKey('acme/3'), 3, 'acme/3 is the bare number 3 when acme is the default org')
  assert.equal(m.projectKey('other/3'), 'other/3')
  m.setDefaultOrg('not a valid org!')
  assert.equal(m.TOWN, 'acme', 'invalid names are ignored')
})
