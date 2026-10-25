import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, mkdirSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { EventEmitter } from 'node:events'
import { apply, replyIn, models, requestError } from '../lib/index.js'

// A fake DSH: records calls, answers modelCatalog/create/selectModel/prompt/projections/page.
function fakeGateway({ reply = 'EPIC 只能接到 Project。' } = {}) {
  const calls = []
  let seq = 10
  const events = []
  const gw = {
    calls,
    async invoke({ namespace, method, args }) {
      calls.push({ namespace, method, request: args?.request })
      if (method === 'modelCatalog') return {
        default: { provider: 'claude', model: 'claude-opus-5-5' },
        routableProviders: ['claude', 'openai'],
        groups: [
          { id: 'claude', name: 'Claude', models: [{ id: 'claude-opus-5-5', name: 'Claude Opus 5.5' }] },
          { id: 'openai', name: 'ChatGPT', models: [{ id: 'gpt-5.6-luna', name: 'GPT-5.6 Luna' }] },
          { id: 'offline', name: 'Not routable', models: [{ id: 'x', name: 'x' }] },
        ],
      }
      if (method === 'create') return { sessionId: 'session-0000aaaa-1111' }
      if (method === 'rename' || method === 'selectModel') return {}
      if (method === 'projections') return { asOfSeq: seq }
      if (method === 'prompt') {
        const text = args.request.content[0].text
        events.push({ seq: ++seq, type: 'user/message', data: { content: [{ type: 'text', text }] } })
        events.push({ seq: ++seq, type: 'assistant/message', data: { message: { role: 'assistant', content: [{ type: 'text', text: reply }] } } })
        events.push({ seq: ++seq, type: 'turn/end', data: { reason: { kind: 'completed' } } })
        return {}
      }
      if (method === 'page') return { records: events.filter((e) => e.seq <= args.request.throughSeq), hasMore: false }
      throw new Error('unexpected ' + method)
    },
  }
  return gw
}

function mountPlugin(gw, dataDir) {
  let handler
  const ctx = {
    typertGateway: gw,
    webServer: { register: (r) => { handler = r.handler; return () => {} } },
    effect: (fn) => fn(),
    logger: () => ({ warn() {}, error() {} }),
  }
  apply(ctx, { dataDir, page: join(dataDir, 'page.html') })
  return handler
}

function request(handler, { method = 'GET', url, headers = {}, body }) {
  const req = new EventEmitter()
  Object.assign(req, { method, url, headers: { host: 'site.example', ...headers } })
  return new Promise((resolve) => {
    const res = {
      headersSent: false, status: 0, headers: {},
      writeHead(s, h) { this.status = s; this.headers = h; this.headersSent = true },
      end(b) { resolve({ status: this.status, headers: this.headers, body: b === undefined ? '' : String(b) }) },
      destroy() {},
    }
    handler(req, res)
    queueMicrotask(() => { if (body !== undefined) req.emit('data', Buffer.from(body)); req.emit('end') })
  })
}

function setup(opts) {
  const dir = mkdtempSync(join(tmpdir(), 'synapse-'))
  writeFileSync(join(dir, 'page.html'), '<html>synapse</html>')
  writeFileSync(join(dir, 'hub.json'), '{"markdown":"x"}')
  const gw = fakeGateway(opts)
  return { gw, h: mountPlugin(gw, dir), dir }
}

test('serves the page, redirects /synapse to /synapse/', async () => {
  const { h } = setup()
  assert.equal((await request(h, { url: '/synapse/' })).body, '<html>synapse</html>')
  const r = await request(h, { url: '/synapse' })
  assert.equal(r.status, 301); assert.equal(r.headers.location, '/synapse/')
})

test('serves only hub.json / projects.json from data, no traversal', async () => {
  const { h } = setup()
  assert.equal((await request(h, { url: '/synapse/data/hub.json' })).status, 200)
  assert.equal((await request(h, { url: '/synapse/data/projects.json' })).status, 404) // not written
  assert.equal((await request(h, { url: '/synapse/data/../page.html' })).status, 404)
  assert.equal((await request(h, { url: '/synapse/data/secret.txt' })).status, 404)
})

test('every response is no-store', async () => {
  const { h } = setup()
  assert.equal((await request(h, { url: '/synapse/' })).headers['cache-control'], 'no-store')
})

test('model menu = the site DSH catalog, only routable providers', async () => {
  const { h } = setup()
  const j = JSON.parse((await request(h, { url: '/synapse/api/models' })).body)
  assert.deepEqual(j.groups.map((g) => g.provider), ['claude', 'openai'])
  assert.deepEqual(j.default, { provider: 'claude', model: 'claude-opus-5-5' })
})

test('ask → reply round trip through one DSH session with the chosen model', async () => {
  const { h, gw } = setup()
  const ask = await request(h, { method: 'POST', url: '/synapse/api/ask', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ question: 'EPIC 可以接 hive 嗎？', model: { provider: 'openai', model: 'gpt-5.6-luna' }, context: { rules: { epic: '只能接到 Project' } } }) })
  assert.equal(ask.status, 200, ask.body)
  const a = JSON.parse(ask.body)
  const sel = gw.calls.find((c) => c.method === 'selectModel')
  assert.deepEqual({ p: sel.request.provider, m: sel.request.model }, { p: 'openai', m: 'gpt-5.6-luna' })
  const prompt = gw.calls.find((c) => c.method === 'prompt').request.content[0].text
  assert.match(prompt, /只能接到 Project/)
  assert.match(prompt, /不要使用工具/)
  const r = JSON.parse((await request(h, { url: `/synapse/api/reply?session=${a.sessionId}&after=${a.after}&marker=${a.marker}` })).body)
  assert.deepEqual(r, { done: true, text: 'EPIC 只能接到 Project。' })
  // second question reuses the same session
  await request(h, { method: 'POST', url: '/synapse/api/ask', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ question: '再一題' }) })
  assert.equal(gw.calls.filter((c) => c.method === 'create').length, 1)
})

test('rejects a model the site does not have, and non-routable providers', async () => {
  const { h } = setup()
  for (const model of [{ provider: 'openai', model: 'nope' }, { provider: 'offline', model: 'x' }]) {
    const r = await request(h, { method: 'POST', url: '/synapse/api/ask', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ question: 'q', model }) })
    assert.equal(r.status, 400, JSON.stringify(model))
  }
})

test('ask guards: JSON only, same origin, non-empty question', async () => {
  const { h } = setup()
  assert.equal((await request(h, { method: 'POST', url: '/synapse/api/ask', headers: { 'content-type': 'text/plain' }, body: '{}' })).status, 415)
  assert.equal((await request(h, { method: 'POST', url: '/synapse/api/ask', headers: { 'content-type': 'application/json', origin: 'https://evil.example' }, body: '{"question":"q"}' })).status, 403)
  assert.equal((await request(h, { method: 'POST', url: '/synapse/api/ask', headers: { 'content-type': 'application/json' }, body: '{"question":"  "}' })).status, 400)
})

test('reply only reads sessions this plugin created', async () => {
  const { h } = setup()
  const r = await request(h, { url: '/synapse/api/reply?session=session-other-1234&after=0&marker=synapse-00000000-0000-0000-0000-000000000000' })
  assert.equal(r.status, 404)
})

test('replyIn: waits for turn end, surfaces errors', () => {
  const m = 'synapse-x'
  const ask = { seq: 1, type: 'user/message', data: { content: [{ type: 'text', text: 'hi ' + m }] } }
  const said = { seq: 2, type: 'assistant/message', data: { message: { role: 'assistant', content: [{ type: 'text', text: 'ok' }] } } }
  assert.equal(replyIn([ask, said], m), null)
  assert.equal(replyIn([ask, said, { seq: 3, type: 'turn/end', data: { reason: { kind: 'completed' } } }], m), 'ok')
  assert.throws(() => replyIn([ask, { seq: 3, type: 'turn/end', data: { reason: { kind: 'error' } } }], m))
})

import { readHives, fetchHub, fetchProjects, cleanHub } from '../lib/fetcher.js'

test('readHives: kind, title, state machine', () => {
  const dir = mkdtempSync(join(tmpdir(), 'hives-'))
  const mk = (n, t) => { mkdirSync(join(dir, n)); writeFileSync(join(dir, n, 'HIVE.md'), t) }
  mk('town-intelligent', '# 小鎮智能 — 專案規則\n> town-intelligent 組織規則\n')
  mk('jtb-tech', '# JTB Tech 雙向翻譯 — 專案規則\n\n## 狀態機\n- 旅客開口\n- 翻譯\n\n## SCM\n- x\n')
  mk('esp32-hi', '# esp32-hi — 專案規則\n')
  mkdirSync(join(dir, 'no-hive-md'))
  const h = Object.fromEntries(readHives(dir).map((x) => [x.id, x]))
  assert.deepEqual(Object.keys(h).sort(), ['esp32-hi', 'jtb-tech', 'town-intelligent'])
  assert.equal(h['town-intelligent'].kind, 'org'); assert.equal(h['town-intelligent'].title, '小鎮智能')
  assert.equal(h['esp32-hi'].kind, 'device')
  assert.deepEqual(h['jtb-tech'].states, ['旅客開口', '翻譯'])
})

test('fetchHub: strips preview banner, unchanged → not rewritten', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'hub-'))
  writeFileSync(join(dir, 'tok'), 'secret\n')
  let auth
  const fetchImpl = async (u, o) => { auth = o.headers.authorization; return { ok: true, json: async () => ({ title: 'T', content: '> 🧪 預覽\n\n# hub' }) } }
  const out = join(dir, 'hub.json')
  assert.deepEqual(await fetchHub({ note: 'n', tokenFile: join(dir, 'tok'), out, fetchImpl }), { changed: true })
  assert.equal(auth, 'Bearer secret')
  assert.equal(JSON.parse(readFileSync(out, 'utf8')).markdown, '# hub')
  assert.ok(!readFileSync(out, 'utf8').includes('secret'))
  assert.deepEqual(await fetchHub({ note: 'n', tokenFile: join(dir, 'tok'), out, fetchImpl }), { changed: false })
  assert.equal(cleanHub('# no banner'), '# no banner')
})

test('fetchProjects: open projects, EPIC = [EPIC] + sub-issues', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'proj-'))
  const graphql = async (q, v) => q.includes('projectsV2')
    ? { organization: { projectsV2: { nodes: [{ number: 2, title: 'Swarm', url: 'u', closed: false, items: { totalCount: 9 } }, { number: 1, title: 'old', url: 'u', closed: true, items: { totalCount: 1 } }] } } }
    : { search: { pageInfo: { hasNextPage: false }, nodes: [
        { number: 13, title: '[EPIC] LLMTwins 退場', url: 'u', state: 'OPEN', repository: { nameWithOwner: 'o/office' }, subIssuesSummary: { total: 5, completed: 2 }, projectItems: { nodes: [] } },
        { number: 120, title: '[EPIC] 沒有子 issue', url: 'u', state: 'OPEN', repository: { nameWithOwner: 'o/ai-eva' }, subIssuesSummary: { total: 0, completed: 0 }, projectItems: { nodes: [] } },
        { number: 7, title: '不是 EPIC', url: 'u', state: 'OPEN', repository: { nameWithOwner: 'o/x' }, subIssuesSummary: { total: 3, completed: 1 }, projectItems: { nodes: [] } }] } }
  const out = join(dir, 'projects.json')
  await fetchProjects({ orgs: ['o'], out, graphql })
  const d = JSON.parse(readFileSync(out, 'utf8'))
  assert.deepEqual(d.projects.map((p) => p.id), ['o#2'])
  assert.deepEqual(d.epics.map((e) => [e.id, e.title, e.done, e.total]), [['o/office#13', 'LLMTwins 退場', 2, 5]])
})
