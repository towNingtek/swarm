import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createRunning, kickoff, ownersFromBoards, parseRunning } from '../lib/running.js'
import { createHandler } from '../lib/index.js'

const node = (over = {}) => ({
  number: 7, title: 'a bee', repository: { nameWithOwner: 'acme/app' }, parent: { number: 3 },
  comments: { nodes: [{ body: 'hi' }, { body: '🐝 開工：引擎 claude，2026-10-20T01:00:00+00:00' }] }, ...over,
})

test('kickoff reads engine and start time from the last 🐝 開工 comment', () => {
  assert.deepEqual(kickoff([{ body: '🐝 開工：引擎 `codex`，2026-10-20T01:00:00Z。' }]),
    { engine: 'codex', since: '2026-10-20T01:00:00Z' })
  assert.deepEqual(kickoff([{ body: 'nothing' }]), { engine: null, since: null })
})

test('parseRunning keeps Wolffia\'s item shape', () => {
  assert.deepEqual(parseRunning({ search: { nodes: [node(), {}] } }), [
    { repo: 'acme/app', number: 7, title: 'a bee', engine: 'claude', since: '2026-10-20T01:00:00+00:00', epic: 3 },
  ])
})

test('one GitHub query per TTL; searches the owners with the swarm:running label', async () => {
  let t = 0, calls = []
  const running = createRunning({ owners: ['acme'], ttlMs: 30_000, now: () => t,
    query: async (q, v) => { calls.push(v.q); return { search: { nodes: [node()] } } } })
  const a = await running(); t = 10_000; await running()
  assert.equal(calls.length, 1)
  assert.match(calls[0], /org:acme/); assert.match(calls[0], /is:open/); assert.match(calls[0], /label:swarm:running/)
  assert.equal(a.items.length, 1)
  t = 31_000; await running(); assert.equal(calls.length, 2)
})

test('no owners: no query, empty list', async () => {
  let called = false
  const r = await createRunning({ owners: [], query: async () => { called = true } })()
  assert.equal(called, false); assert.deepEqual(r.items, [])
})

test('a failed query serves the last good answer, else an empty list with an error', async () => {
  let t = 0, fail = false
  const running = createRunning({ owners: ['acme'], ttlMs: 1, now: () => t,
    query: async () => { if (fail) throw new Error('boom'); return { search: { nodes: [node()] } } } })
  await running(); fail = true; t = 5
  const stale = await running()
  assert.equal(stale.items.length, 1); assert.equal(stale.stale, true)
  const fresh = await createRunning({ owners: ['acme'], query: async () => { throw new Error('nope') } })()
  assert.deepEqual(fresh.items, []); assert.match(fresh.error, /nope/)
})

test('owners come from the board project urls', () => {
  assert.deepEqual(ownersFromBoards([{ url: 'https://github.com/orgs/acme/projects/2' },
    { url: 'https://github.com/users/bob/projects/1' }, { url: 'x' }]), ['acme', 'bob'])
})

function fakeRes() {
  return { status: 0, headers: {}, body: '',
    writeHead(s, h) { this.status = s; Object.assign(this.headers, h ?? {}) },
    setHeader(k, v) { this.headers[k] = v }, end(b) { if (b) this.body += b } }
}

test('GET /board/api/running returns items plus data_at per project file', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'board-'))
  try {
    writeFileSync(join(dir, 'p2.js'), 'window.PROJECTS=window.PROJECTS||{};window.PROJECTS[2]=' +
      JSON.stringify({ num: 2, title: 'T', url: 'https://github.com/orgs/acme/projects/2', fetched: '2026-10-20T01:02:03Z', items: [] }) + ';\n')
    const handler = createHandler({ html: '<html>', launcher: '', dataDir: dir,
      running: async () => ({ at: 'now', items: [{ repo: 'acme/app', number: 7 }] }) })
    const res = fakeRes()
    await handler({ method: 'GET', url: '/board/api/running' }, res)
    assert.equal(res.status, 200)
    const body = JSON.parse(res.body)
    assert.equal(body.items[0].number, 7)
    assert.deepEqual(body.data_at, { 2: '2026-10-20T01:02:03Z' })
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('GET /board/api/think/reply: only board sessions; done:false while running; text once the turn ended', async () => {
  const sessions = { 2: 'session-aaaaaaaa-1111' }
  const store = { get: async (p) => sessions[p] ?? null, all: async () => sessions, set: async () => {} }
  let events = [
    { type: 'user/message', seq: 5, data: { source: { kind: 'user' }, content: [{ type: 'text', text: '哪些可以合併？\n---' }] } },
  ]
  const gateway = { invoke: async ({ method }) => {
    if (method === 'projections') return { asOfSeq: events.at(-1).seq }
    if (method === 'page') return { records: events.map((event) => ({ type: 'event', event })), hasMore: false }
    throw new Error('unexpected ' + method)
  } }
  const handler = createHandler({ html: '', launcher: '', dataDir: '/nonexistent', gateway, store })
  const call = async (q) => { const res = fakeRes(); await handler({ method: 'GET', url: '/board/api/think/reply?' + q }, res); return { status: res.status, json: JSON.parse(res.body || '{}') } }
  assert.equal((await call('session=session-bbbbbbbb-2222&after=-1&q=x')).status, 404, 'a session the board did not create')
  assert.deepEqual((await call('session=session-aaaaaaaa-1111&after=4&q=' + encodeURIComponent('哪些可以合併？'))).json, { done: false })
  events.push({ type: 'assistant/message', seq: 6, data: { message: { role: 'assistant', content: [{ type: 'text', text: '建議兩組' }] } } },
              { type: 'turn/end', seq: 7, data: { reason: { kind: 'completed' } } })
  const r = await call('session=session-aaaaaaaa-1111&after=4&q=' + encodeURIComponent('哪些可以合併？'))
  assert.deepEqual(r.json, { done: true, text: '建議兩組' })
  assert.deepEqual((await call('session=session-aaaaaaaa-1111&after=7&q=x')).json, { done: false }, 'nothing after `after` yet')
})
