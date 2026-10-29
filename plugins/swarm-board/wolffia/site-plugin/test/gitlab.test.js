// GitLab projects (EPIC #42): key, data file route, 改狀態 written back to GitLab, token handling.
// A fake fetch stands in for GitLab; no network, no gh, no python.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import * as plugin from '../lib/index.js'
import { dataFile, projectKey } from '../lib/actions.js'
import { columnOf, gitlabBase, labelsFor } from '../lib/gitlab.js'

const TOKEN = 'glpat-SECRET-test-token-1234567890'
const KEY = 'gitlab:acme/general'
const HASH = createHash('sha1').update('acme/general').digest('hex').slice(0, 12)
const ISSUE = 'https://gitlab.com/acme/general/-/issues/7'

// A fake GitLab: one issue per "<path>#<iid>", GET and PUT /api/v4/projects/:id/issues/:iid.
function fakeGitlab(issues = {}, { fail } = {}) {
  const calls = []
  const fetch = async (url, init = {}) => {
    calls.push({ url, method: init.method, headers: init.headers, body: init.body ? JSON.parse(init.body) : undefined })
    if (fail) return new Response(JSON.stringify({ message: fail }), { status: 500 })
    const m = /\/api\/v4\/projects\/([^/]+)\/issues\/(\d+)$/.exec(url)
    const issue = m && issues[`${decodeURIComponent(m[1])}#${m[2]}`]
    if (!issue) return new Response('{"message":"404 Not found"}', { status: 404 })
    if (init.method === 'PUT') {
      const b = JSON.parse(init.body)
      const add = b.add_labels ? b.add_labels.split(',') : []
      const remove = b.remove_labels ? b.remove_labels.split(',') : []
      issue.labels = [...issue.labels.filter((l) => !remove.includes(l)), ...add]
      if (b.state_event === 'close') issue.state = 'closed'
      if (b.state_event === 'reopen') issue.state = 'opened'
    }
    return new Response(JSON.stringify(issue), { status: 200 })
  }
  return { fetch, calls, issues }
}

function fakeFetcher() {
  const calls = []
  const fn = (opts) => { const f = { opts, stop() {}, fetchNow(k) { f.now.push(k) }, now: [] }; calls.push(f); return f }
  fn.calls = calls
  return fn
}

function setup({ config = {}, hooks = {}, token = TOKEN, projectsJson } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'swarm-board-gl-'))
  const dataDir = join(root, 'data')
  mkdirSync(dataDir)
  writeFileSync(join(dataDir, 'index.js'), 'window.PROJECTS={}')
  writeFileSync(join(dataDir, `g-${HASH}.js`), `window.PROJECTS["${KEY}"]={"key":"${KEY}","fetched":"2026-10-29T00:00:00Z","items":[]}`)
  const tokenFile = join(root, 'gitlab.token')
  if (token) writeFileSync(tokenFile, token + '\n')
  if (projectsJson) writeFileSync(join(root, 'projects.json'), JSON.stringify(projectsJson))
  const logs = []
  const registered = []
  const startFetcher = hooks.startFetcher ?? fakeFetcher()
  const ctx = {
    logger: () => ({ warn: (...a) => logs.push(a.join(' ')), error: (...a) => logs.push(a.join(' ')), info: (...a) => logs.push(a.join(' ')) }),
    effect: (fn) => { fn() },
    connection: {},
    typertGateway: {},
    webServer: { register: (r) => { registered.push(r); return () => {} } },
  }
  plugin.apply(ctx, { dataDir, gitlabTokenFile: tokenFile, githubRecheckMs: 0, digestEveryMs: 0, projects: [2, 'gitlab:Acme/General'], ...config },
    { hasGitHub: () => true, ...hooks, startFetcher })
  return { root, dataDir, tokenFile, logs, route: registered[0], startFetcher }
}

function newRes() {
  return {
    status: 0, headers: {}, body: '', headersSent: false,
    writeHead(status, headers) { this.status = status; this.headers = headers; this.headersSent = true; return this },
    end(body) { this.body = body === undefined ? '' : String(body) },
    destroy() {},
  }
}

async function get(route, url) {
  const res = newRes()
  await route.handler({ method: 'GET', url, headers: {} }, res)
  return res
}

async function postStatus(route, body) {
  const res = newRes()
  const raw = Buffer.from(JSON.stringify(body))
  const req = { method: 'POST', url: '/board/api/status', headers: { 'content-type': 'application/json', host: 'h' },
    on(ev, fn) { if (ev === 'data') fn(raw); if (ev === 'end') fn() } }
  await route.handler(req, res)
  let json = null
  try { json = JSON.parse(res.body) } catch {}
  return { status: res.status, json, body: res.body }
}

test('projectKey: gitlab:<path> is a key (lowercased); bad paths are not', () => {
  assert.equal(projectKey('gitlab:Acme/Sub/App'), 'gitlab:acme/sub/app')
  assert.equal(projectKey(' GitLab:Acme/General '), 'gitlab:acme/general')
  assert.equal(projectKey('gitlab:acme/my.repo_x-1'), 'gitlab:acme/my.repo_x-1')
  for (const bad of ['gitlab:', 'gitlab:/x', 'gitlab:a/../b', 'gitlab:acme', 'gitlab:a//b', 'gitlab:a/b/', 'gitlab:a/./b', 'gitlab:a/b c', 'gitlab:a/%2e%2e', 'gitlab:a/.hidden'])
    assert.equal(projectKey(bad), null, bad)
  // GitHub keys unchanged.
  assert.equal(projectKey(2), 2)
  assert.equal(projectKey('other/1'), 'other/1')
  assert.equal(projectKey('acme/3'), 3)
})

test('dataFile: g-<first 12 hex of sha1(path)>.js', () => {
  assert.equal(dataFile(KEY), `g-${HASH}.js`)
  assert.equal(dataFile('gitlab:acme/sub/app'), `g-${createHash('sha1').update('acme/sub/app').digest('hex').slice(0, 12)}.js`)
  assert.equal(dataFile(2), 'p2.js')
  assert.equal(dataFile('other/1'), 'p-other-1.js')
})

test('/board/data/g-<12 hex>.js is served; other g- names 404', async () => {
  const { route } = setup()
  const ok = await get(route, `/board/data/g-${HASH}.js`)
  assert.equal(ok.status, 200)
  assert.match(ok.body, /window\.PROJECTS\["gitlab:acme\/general"\]/)
  assert.equal((await get(route, '/board/data/g-0123456789ab.js')).status, 404) // well-formed, not there
  for (const bad of [`g-${HASH.slice(0, 11)}.js`, `g-${HASH}0.js`, `g-${HASH.toUpperCase()}.js`, 'g-zzzzzzzzzzzz.js', `g-${HASH}.json`, `../g-${HASH}.js`, 'gitlab.token'])
    assert.equal((await get(route, `/board/data/${bad}`)).status, 404, bad)
})

test('/api/running data_at includes the GitLab project under its key', async () => {
  const { route } = setup({ hooks: { running: async () => ({ at: 'now', items: [] }) } })
  const res = await get(route, '/board/api/running')
  assert.equal(JSON.parse(res.body).data_at[KEY], '2026-10-29T00:00:00Z')
})

test('改狀態: 待辦 -> 進行中 adds the progress label (default names)', async () => {
  const gl = fakeGitlab({ 'acme/general#7': { iid: 7, state: 'opened', labels: ['bug'] } })
  const { route } = setup({ hooks: { fetch: gl.fetch } })
  const r = await postStatus(route, { project: 'gitlab:Acme/General', url: ISSUE, status: '進行中 (7 天一輪)' })
  assert.equal(r.status, 200, r.body)
  assert.equal(r.json.ok, true)
  assert.equal(r.json.state, 'OPEN')
  const put = gl.calls.find((c) => c.method === 'PUT')
  assert.equal(put.url, 'https://gitlab.com/api/v4/projects/acme%2Fgeneral/issues/7')
  assert.deepEqual(put.body, { add_labels: '進行中' })
  assert.deepEqual(gl.issues['acme/general#7'].labels, ['bug', '進行中'])
  // 進行中 -> 審核後請關閉 swaps the labels.
  const r2 = await postStatus(route, { project: KEY, url: ISSUE, status: '審核後請關閉' })
  assert.equal(r2.status, 200)
  assert.deepEqual(gl.calls.filter((c) => c.method === 'PUT')[1].body, { add_labels: '審核後請關閉', remove_labels: '進行中' })
  assert.deepEqual(gl.issues['acme/general#7'].labels, ['bug', '審核後請關閉'])
  // Moving to the column it is already in sends no PUT.
  const before = gl.calls.length
  assert.equal((await postStatus(route, { project: KEY, url: ISSUE, status: '審核後請關閉' })).status, 200)
  assert.equal(gl.calls.filter((c) => c.method === 'PUT').length, 2)
  assert.equal(gl.calls.length, before + 1)
})

test('改狀態: 移到「完成」closes the issue; 從「完成」移回 reopens it', async () => {
  const gl = fakeGitlab({ 'acme/general#7': { iid: 7, state: 'opened', labels: ['進行中'] } })
  const { route } = setup({ hooks: { fetch: gl.fetch } })
  const done = await postStatus(route, { project: KEY, url: ISSUE, status: '完成' })
  assert.equal(done.status, 200, done.body)
  assert.equal(done.json.state, 'CLOSED')
  assert.deepEqual(gl.calls.at(-1).body, { remove_labels: '進行中', state_event: 'close' })
  assert.equal(gl.issues['acme/general#7'].state, 'closed')
  const back = await postStatus(route, { project: KEY, url: ISSUE, status: '待辦' })
  assert.equal(back.status, 200, back.body)
  assert.equal(back.json.state, 'OPEN')
  assert.deepEqual(gl.calls.at(-1).body, { state_event: 'reopen' })
  assert.equal(gl.issues['acme/general#7'].state, 'opened')
  // Closed -> 進行中: reopened with the label.
  await postStatus(route, { project: KEY, url: ISSUE, status: '完成' })
  const doing = await postStatus(route, { project: KEY, url: ISSUE, status: '進行中 (7 天一輪)' })
  assert.equal(doing.json.state, 'OPEN')
  assert.deepEqual(gl.calls.at(-1).body, { add_labels: '進行中', state_event: 'reopen' })
})

test('projects.json gitlabLabels: the configured names are added and removed (case-insensitive match)', async () => {
  const gl = fakeGitlab({ 'acme/general#7': { iid: 7, state: 'opened', labels: ['review', '進行中'] } })
  const { route } = setup({
    hooks: { fetch: gl.fetch },
    projectsJson: { projects: [KEY], gitlabLabels: { 'gitlab:Acme/General': { progress: 'In Progress', review: 'Review' } } },
  })
  const r = await postStatus(route, { project: KEY, url: ISSUE, status: '進行中 (7 天一輪)' })
  assert.equal(r.status, 200, r.body)
  // Adds "In Progress", removes the issue's own "review" (matches "Review"); the default 進行中 is not a status label here.
  assert.deepEqual(gl.calls.at(-1).body, { add_labels: 'In Progress', remove_labels: 'review' })
  assert.deepEqual(gl.issues['acme/general#7'].labels, ['進行中', 'In Progress'])
  assert.deepEqual(labelsFor(null, KEY), { progress: '進行中', review: '審核後請關閉' })
  assert.deepEqual(labelsFor({ gitlabLabels: { [KEY]: { review: 'QA' } } }, KEY), { progress: '進行中', review: 'QA' })
})

test('only projects on the board, only that project\'s issues, only known columns', async () => {
  const gl = fakeGitlab({ 'acme/general#7': { iid: 7, state: 'opened', labels: [] }, 'acme/other#7': { iid: 7, state: 'opened', labels: [] } })
  const { route } = setup({ hooks: { fetch: gl.fetch } })
  const other = await postStatus(route, { project: 'gitlab:acme/other', url: 'https://gitlab.com/acme/other/-/issues/7', status: '完成' })
  assert.equal(other.status, 400)
  assert.equal(other.json.error, '不允許的 project')
  for (const url of ['https://gitlab.com/acme/other/-/issues/7', 'https://evil.example/acme/general/-/issues/7',
    'https://gitlab.com/acme/general/-/merge_requests/7', 'https://gitlab.com/acme/general/-/issues/0', 'https://github.com/acme/general/issues/7']) {
    const r = await postStatus(route, { project: KEY, url, status: '完成' })
    assert.equal(r.status, 400, url)
  }
  assert.equal((await postStatus(route, { project: KEY, url: ISSUE, status: 'Blocked' })).status, 400)
  assert.equal(gl.calls.length, 0)
  assert.equal(columnOf('In Progress'), 'progress')
})

test('config.projects and projects.json both accept gitlab keys; fetch-project.py gets the key', async () => {
  const f = fakeFetcher()
  const { route } = setup({
    config: { projects: [2] },
    hooks: { startFetcher: f, fetch: fakeGitlab({ 'acme/sub/app#1': { iid: 1, state: 'opened', labels: [] } }).fetch },
    projectsJson: { projects: [2, 'gitlab:Acme/Sub/App'] },
  })
  assert.deepEqual(f.calls[0].opts.projects, [2, 'gitlab:acme/sub/app'])
  const r = await postStatus(route, { project: 'gitlab:acme/sub/app', url: 'https://gitlab.com/Acme/Sub/App/-/issues/1', status: '完成' })
  assert.equal(r.status, 200, r.body)
  assert.deepEqual(f.calls[0].now, ['gitlab:acme/sub/app']) // re-fetched after the change
})

test('no token: 改狀態 answers 409 (not 502) and GitLab is not called; a token put there later counts', async () => {
  const gl = fakeGitlab({ 'acme/general#7': { iid: 7, state: 'opened', labels: [] } })
  const { route, tokenFile } = setup({ token: null, hooks: { fetch: gl.fetch } })
  const r = await postStatus(route, { project: KEY, url: ISSUE, status: '完成' })
  assert.equal(r.status, 409)
  assert.match(r.json.error, /GitLab/)
  assert.equal(gl.calls.length, 0)
  writeFileSync(tokenFile, TOKEN)
  assert.equal((await postStatus(route, { project: KEY, url: ISSUE, status: '完成' })).status, 200)
})

test('without gh: GitLab 改狀態 still works; GitHub 改狀態 is still 409', async () => {
  const gl = fakeGitlab({ 'acme/general#7': { iid: 7, state: 'opened', labels: [] } })
  const { route } = setup({ hooks: { hasGitHub: () => false, relay: null, fetch: gl.fetch } })
  assert.equal((await postStatus(route, { project: KEY, url: ISSUE, status: '完成' })).status, 200)
  assert.equal((await postStatus(route, { project: 2, url: 'https://github.com/acme/x/issues/1', status: '完成' })).status, 409)
})

test('GitLab project with a token starts the fetcher without gh; the child gets GITLAB_TOKEN and GITLAB_URL', async () => {
  const f = fakeFetcher()
  setup({ config: { gitlabUrl: 'https://git.example.com/gl/' }, hooks: { hasGitHub: () => false, relay: null, startFetcher: f } })
  assert.equal(f.calls.length, 1)
  const env = await f.calls[0].opts.env()
  assert.equal(env.GITLAB_TOKEN, TOKEN)
  assert.equal(env.GITLAB_URL, 'https://git.example.com/gl')
  assert.equal(gitlabBase('ftp://x'), 'https://gitlab.com')
  assert.equal(gitlabBase('https://u:p@x.example'), 'https://gitlab.com')
  // Without a GitLab project, no GitLab env at all.
  const g = fakeFetcher()
  setup({ config: { projects: [2] }, hooks: { startFetcher: g } })
  await new Promise((r) => setImmediate(r)) // started once the gh check answers
  const env2 = await g.calls[0].opts.env()
  assert.equal(env2.GITLAB_TOKEN, undefined)
  assert.equal(env2.GITLAB_URL, undefined)
})

test('token only in GitLab request headers and the fetch env: never in responses or logs', async () => {
  const gl = fakeGitlab({ 'acme/general#7': { iid: 7, state: 'opened', labels: [] } })
  const bodies = []
  const { route, logs } = setup({ config: { gitlabUrl: 'https://git.example.com' }, hooks: { fetch: gl.fetch } })
  const url = 'https://git.example.com/acme/general/-/issues/7'
  bodies.push((await postStatus(route, { project: KEY, url, status: '完成' })).body)
  for (const p of ['/board/data/status.json', `/board/data/g-${HASH}.js`, '/board/data/index.js', '/board/api/suggestions'])
    bodies.push((await get(route, p)).body)
  for (const c of gl.calls) {
    assert.ok(c.url.startsWith('https://git.example.com/api/v4/'))
    assert.equal(c.headers['PRIVATE-TOKEN'], TOKEN)
    assert.ok(!c.url.includes(TOKEN))
    assert.ok(!JSON.stringify(c.body ?? '').includes(TOKEN))
  }
  // A GitLab error that echoes the token: 502, scrubbed in the answer and the log.
  const bad = fakeGitlab({}, { fail: `boom ${TOKEN}` })
  const s2 = setup({ config: { gitlabUrl: 'https://git.example.com' }, hooks: { fetch: bad.fetch } })
  const r = await postStatus(s2.route, { project: KEY, url, status: '完成' })
  assert.equal(r.status, 502)
  assert.match(r.json.error, /^GitLab 更新失敗/)
  bodies.push(r.body)
  // A fetch that throws with the token in its message.
  const s3 = setup({ hooks: { fetch: async () => { throw new Error(`refused ${TOKEN}`) } } })
  const r3 = await postStatus(s3.route, { project: KEY, url: ISSUE, status: '完成' })
  assert.equal(r3.status, 502)
  bodies.push(r3.body)
  assert.ok(s2.logs.length + s3.logs.length > 0)
  for (const text of [...bodies, ...logs, ...s2.logs, ...s3.logs]) assert.ok(!String(text).includes(TOKEN), text)
})

test('改狀態: a /-/work_items/<iid> URL (what gitlab.com now returns as web_url) is the same issue', async () => {
  // Found on a real project: every issue's web_url was …/-/work_items/<iid>, and 改狀態 refused all of them.
  const gl = fakeGitlab({ 'acme/general#7': { iid: 7, state: 'opened', labels: [] } })
  const { route } = setup({ hooks: { fetch: gl.fetch } })
  const r = await postStatus(route, { project: KEY, url: 'https://gitlab.com/acme/general/-/work_items/7', status: '進行中' })
  assert.equal(r.status, 200, r.body)
  assert.equal(gl.calls.find((c) => c.method === 'PUT').url, 'https://gitlab.com/api/v4/projects/acme%2Fgeneral/issues/7')
  // Still only this project's issues, and still not merge requests.
  for (const url of ['https://gitlab.com/acme/other/-/work_items/7', 'https://gitlab.com/acme/general/-/work_items/0',
    'https://gitlab.com/acme/general/-/work_items/7/x']) {
    assert.equal((await postStatus(route, { project: KEY, url, status: '完成' })).status, 400, url)
  }
})
