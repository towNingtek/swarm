// 每日聚合: startDigest against a fake think and a fake clock; GET /board/api/suggestions.
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import * as plugin from '../lib/index.js'
import { createSuggestionStore, DAY, defaultSuggestionsFile, headOf, parseSuggestions, QUESTION, replyIn, replyingThink, startDigest } from '../lib/digest.js'

const HOUR = 60 * 60 * 1000
const T0 = Date.parse('2026-10-19T00:00:00Z')

const SUGGESTION = { title: '看板整合', kind: 'vertical', issues: ['towNingtek/swarm#520', 'towNingtek/swarm#522'], why: '都在做看板' }
const reply = (suggestions = [SUGGESTION]) => `分析如下……\n\n\`\`\`json\n${JSON.stringify({ suggestions })}\n\`\`\``

function fakeThink(answer = () => ({ sessionId: 'g1', text: reply() })) {
  const calls = []
  const fn = async (args) => { calls.push(args); return answer(args) }
  fn.calls = calls
  return fn
}

// Timers that fire only when the test says so.
function fakeTimers() {
  const pending = []
  return {
    pending,
    setTimeout(fn, ms) { const t = { fn, ms }; pending.push(t); return t },
    clearTimeout(t) { const i = pending.indexOf(t); if (i >= 0) pending.splice(i, 1) },
    fire() { const t = pending.shift(); t.fn(); return t },
  }
}

const quiet = { warn() {}, info() {} }
const tmpStore = () => createSuggestionStore(join(mkdtempSync(join(tmpdir(), 'swarm-digest-')), 'suggestions.json'))
const flush = () => new Promise((r) => setImmediate(r))

function start(opts = {}) {
  const think = opts.think ?? fakeThink()
  const store = opts.store ?? tmpStore()
  const timers = fakeTimers()
  const clock = { t: opts.t ?? T0 }
  const logs = []
  const digest = startDigest({
    think, store, timers, readBoards: async () => [{ num: 2, title: 'P2', issues: [] }], github: opts.github,
    now: () => clock.t, everyMs: opts.everyMs ?? DAY, log: { warn: (...a) => logs.push(a), ...(opts.info ? { info: opts.info } : {}) },
  })
  return { digest, think, store, timers, clock, logs }
}

test('calls think with project "*", the fixed question and every board', async () => {
  const { digest, think } = start()
  await digest.runNow()
  assert.equal(think.calls.length, 1)
  const [call] = think.calls
  assert.equal(call.project, '*')
  assert.equal(call.question, QUESTION)
  assert.deepEqual(call.context.projects, [{ num: 2, title: 'P2', issues: [] }])
  digest.stop()
})

test('the question asks for suggestions only: no new cards, no status changes, JSON at the end', () => {
  assert.match(QUESTION, /只提建議/)
  assert.match(QUESTION, /不要開卡/)
  assert.match(QUESTION, /不要改/)
  assert.match(QUESTION, /同一個 project/)
  assert.match(QUESTION, /跨 project/)
  assert.match(QUESTION, /"suggestions"/)
  assert.match(QUESTION, /vertical\|horizontal/)
  assert.match(QUESTION, /owner\/repo#N/)
})

test('parses the JSON in the reply and writes suggestions.json with generatedAt and sessionId', async () => {
  const { digest, store } = start()
  const data = await digest.runNow()
  assert.deepEqual(data, { generatedAt: new Date(T0).toISOString(), sessionId: 'g1', suggestions: [SUGGESTION] })
  assert.deepEqual(JSON.parse(readFileSync(store.file, 'utf8')), data)
  digest.stop()
})

test('a string reply works too', async () => {
  const { digest, store } = start({ think: fakeThink(() => reply()) })
  await digest.runNow()
  assert.deepEqual(store.read().suggestions, [SUGGESTION])
  assert.equal(store.read().sessionId, null)
  digest.stop()
})

test('a reply without parsable JSON keeps the previous suggestions and logs', async () => {
  const store = tmpStore()
  const previous = { generatedAt: new Date(T0 - 2 * DAY).toISOString(), sessionId: 'g0', suggestions: [SUGGESTION] }
  store.write(previous)
  const { digest, logs } = start({ store, think: fakeThink(() => ({ sessionId: 'g1', text: '今天沒有想法。{"suggestions": [壞掉' })) })
  assert.equal(await digest.runNow(), null)
  assert.deepEqual(store.read(), previous)
  assert.equal(logs.length, 1)
  assert.match(logs[0][0], /keeping the previous/)
  digest.stop()
})

test('a failing think keeps the previous suggestions and logs', async () => {
  const store = tmpStore()
  const previous = { generatedAt: new Date(T0 - 2 * DAY).toISOString(), sessionId: 'g0', suggestions: [] }
  store.write(previous)
  const { digest, logs } = start({ store, think: fakeThink(() => { throw Object.assign(new Error('x'), { code: 'gateway/internal' }) }) })
  assert.equal(await digest.runNow(), null)
  assert.deepEqual(store.read(), previous)
  assert.equal(logs.length, 1)
  digest.stop()
})

test('runs on start when the last run is more than a period ago, then every period', async () => {
  const store = tmpStore()
  store.write({ generatedAt: new Date(T0 - DAY - 1).toISOString(), sessionId: 'g0', suggestions: [] })
  const { digest, think, timers, clock } = start({ store })
  assert.equal(timers.pending.length, 1)
  timers.fire()
  await flush(); await flush()
  assert.equal(think.calls.length, 1)
  assert.equal(store.read().sessionId, 'g1')
  // next one a full period later
  assert.equal(timers.pending.length, 1)
  assert.equal(timers.pending[0].ms, DAY)
  clock.t += DAY
  timers.fire()
  await flush(); await flush()
  assert.equal(think.calls.length, 2)
  digest.stop()
  assert.equal(timers.pending.length, 0)
})

test('runs on start when there is no previous run', async () => {
  const { digest, think, timers } = start()
  timers.fire()
  await flush(); await flush()
  assert.equal(think.calls.length, 1)
  digest.stop()
})

test('does not run on start when the last run is within the period; waits for the rest', async () => {
  const store = tmpStore()
  store.write({ generatedAt: new Date(T0 - 3 * HOUR).toISOString(), sessionId: 'g0', suggestions: [] })
  const { digest, think, timers, clock } = start({ store })
  timers.fire()
  await flush()
  assert.equal(think.calls.length, 0)
  assert.equal(timers.pending.length, 1)
  assert.equal(timers.pending[0].ms, DAY - 3 * HOUR)
  clock.t += DAY - 3 * HOUR
  timers.fire()
  await flush(); await flush()
  assert.equal(think.calls.length, 1)
  digest.stop()
})

test('everyMs: 0 never runs', async () => {
  const { digest, think, timers } = start({ everyMs: 0 })
  assert.equal(timers.pending.length, 0)
  await flush()
  assert.equal(think.calls.length, 0)
  digest.stop()
})

test('only one run at a time', async () => {
  let release
  const think = fakeThink(() => new Promise((r) => { release = () => r({ sessionId: 'g1', text: reply() }) }))
  const { digest } = start({ think })
  const a = digest.runNow()
  const b = digest.runNow()
  assert.equal(a, b)
  await flush()
  assert.equal(think.calls.length, 1)
  release()
  await a
  const c = digest.runNow()
  assert.notEqual(c, a)
  await flush()
  release()
  await c
  assert.equal(think.calls.length, 2)
  digest.stop()
})

test('parseSuggestions takes the last JSON block and drops malformed entries', () => {
  const text = `例子 {"suggestions":[{"title":"舊","kind":"vertical","issues":["a/b#1"]}]}\n最後：\n${JSON.stringify({ suggestions: [
    SUGGESTION,
    { title: '跨站', kind: 'horizontal', issues: ['a/b#1', 'c/d#2', 'not an issue'], why: '共用' },
    { title: '沒 kind', issues: ['a/b#1'] },
    { title: '沒 issue', kind: 'vertical', issues: [] },
  ] })}`
  assert.deepEqual(parseSuggestions(text), [SUGGESTION, { title: '跨站', kind: 'horizontal', issues: ['a/b#1', 'c/d#2'], why: '共用' }])
  assert.deepEqual(parseSuggestions('{"suggestions":[]}'), [])
  assert.equal(parseSuggestions('沒有 JSON'), null)
  assert.equal(parseSuggestions('{"suggestions": 1}'), null)
})

test('suggestions file defaults to $DSH_HOME/swarm-board/suggestions.json', () => {
  const before = process.env.DSH_HOME
  process.env.DSH_HOME = '/x/dsh-home'
  try {
    assert.equal(defaultSuggestionsFile(), '/x/dsh-home/swarm-board/suggestions.json')
  } finally {
    if (before === undefined) delete process.env.DSH_HOME
    else process.env.DSH_HOME = before
  }
})

// --- reading the reply back from the session ---

const ev = (seq, type, data) => ({ type: 'event', event: { seq, type, data } })
const userMsg = (seq, text) => ev(seq, 'user/message', { source: { kind: 'user' }, content: [{ type: 'text', text }] })
const modelMsg = (seq, text) => ev(seq, 'assistant/message', { message: { role: 'assistant', source: { kind: 'model' }, content: [{ type: 'text', text }] } })

test('replyIn waits for the turn to end, then returns the model text after the question', () => {
  const events = [userMsg(5, '別的問題'), modelMsg(6, '別的回答'), userMsg(7, `${QUESTION}\n---`), modelMsg(8, '想一下'), modelMsg(9, reply())].map((r) => r.event)
  assert.equal(replyIn(events, QUESTION.split('\n')[0]), null)
  events.push({ seq: 10, type: 'turn/end', data: { reason: { kind: 'stop' } } })
  assert.equal(replyIn(events, QUESTION.split('\n')[0]), `想一下\n${reply()}`)
  assert.throws(() => replyIn([...events.slice(0, 4), { seq: 10, type: 'turn/end', data: { reason: { kind: 'error' } } }], QUESTION.split('\n')[0]), /error/)
  // DSH refused the turn (an archived session): finished, nothing will answer — not "still working".
  assert.throws(() => replyIn([...events.slice(0, 3), { seq: 10, type: 'turn/end', data: { reason: { kind: 'blocked' } } }], QUESTION.split('\n')[0]), { code: 'digest/turn-blocked' })
})

test('replyingThink sends, then polls session/projections + session/page until the turn ends', async () => {
  const records = [userMsg(3, '舊的'), modelMsg(4, '舊答')]
  let polls = 0
  const gateway = {
    calls: [],
    async invoke({ namespace, method, args }) {
      this.calls.push([namespace, method, args.request])
      if (method === 'projections') return { asOfSeq: records.at(-1).event.seq }
      if (method === 'page') {
        if (++polls === 2) records.push(modelMsg(7, reply()), ev(8, 'turn/end', { reason: { kind: 'stop' } }))
        return { records: records.filter((r) => r.event.seq <= args.request.throughSeq), hasMore: false }
      }
      throw new Error(method)
    },
  }
  const store = { async get(p) { return p === '*' ? 'g1' : null } }
  const sent = []
  const send = async (q) => { sent.push(q); records.push(userMsg(5, `${q.question}\n---`), modelMsg(6, '開始')); return { sessionId: 'g1', created: false } }
  const t = replyingThink({ gateway, store, send, pollMs: 1 })
  const r = await t({ project: '*', question: QUESTION, context: { projects: [] } })
  assert.equal(sent.length, 1)
  assert.equal(r.sessionId, 'g1')
  assert.equal(r.text, `開始\n${reply()}`)
  assert.ok(gateway.calls.every(([ns]) => ns === 'session'))
  const pages = gateway.calls.filter(([, m]) => m === 'page')
  assert.deepEqual(pages[0][2].address, { kind: 'session', sessionId: 'g1' })
  assert.equal(typeof pages[0][2].throughSeq, 'number')
})

test('headOf: session/projections of DSH 0.1.7 and 0.2', () => {
  assert.equal(headOf({ asOfSeq: 12, projections: {} }), 12) // 0.1.7
  assert.equal(headOf({ kind: 'sequenced', asOfSeq: 12, values: {} }), 12) // 0.2
  assert.equal(headOf({ kind: 'migration-required', values: {} }), -1) // 0.2, cold session: no cut yet
  assert.equal(headOf(null), -1)
  assert.equal(headOf(undefined), -1)
})

test('replyingThink works with DSH 0.2 sequenced projections (and a cold first read)', async () => {
  const records = []
  let reads = 0
  const gateway = {
    async invoke({ method, args }) {
      if (method === 'projections') {
        if (++reads === 1) return { kind: 'migration-required', values: {} }
        return { kind: 'sequenced', asOfSeq: records.at(-1)?.event.seq ?? 0, values: {} }
      }
      if (method === 'page') return { records: records.filter((r) => r.event.seq <= args.request.throughSeq), hasMore: false }
      throw new Error(method)
    },
  }
  const store = { async get() { return null } }
  const send = async (q) => { records.push(userMsg(1, `${q.question}\n---`), modelMsg(2, reply()), ev(3, 'turn/end', { reason: { kind: 'stop' } })); return { sessionId: 'g2', created: true } }
  const r = await replyingThink({ gateway, store, send, pollMs: 1 })({ project: '*', question: QUESTION, context: {} })
  assert.equal(r.text, reply())
})

// --- GET /board/api/suggestions ---

function route(config) {
  const registered = []
  const disposers = []
  const ctx = {
    logger: () => quiet,
    effect: (fn) => { const d = fn(); if (typeof d === 'function') disposers.push(d) },
    connection: {},
    typertGateway: {},
    webServer: { register: (r) => { registered.push(r); return () => {} } },
  }
  plugin.apply(ctx, { dataDir: mkdtempSync(join(tmpdir(), 'swarm-digest-data-')), ...config })
  return { route: registered[0], disposers }
}

async function get(r, url, method = 'GET') {
  const res = {
    status: 0, headers: {}, body: undefined, headersSent: false,
    writeHead(status, headers) { this.status = status; this.headers = headers; this.headersSent = true; return this },
    end(body) { this.body = body === undefined ? undefined : String(body) },
    destroy() {},
  }
  await r.handler({ method, url, headers: {} }, res)
  return res
}

test('GET /board/api/suggestions: {"suggestions":[]} before the first digest, the file after', async () => {
  const file = join(mkdtempSync(join(tmpdir(), 'swarm-digest-')), 'suggestions.json')
  const { route: r, disposers } = route({ suggestionsFile: file, digestEveryMs: 0 })
  let res = await get(r, '/board/api/suggestions')
  assert.equal(res.status, 200)
  assert.match(res.headers['content-type'], /^application\/json/)
  assert.equal(res.headers['cache-control'], 'no-store')
  assert.deepEqual(JSON.parse(res.body), { suggestions: [] })
  const data = { generatedAt: new Date(T0).toISOString(), sessionId: 'g1', suggestions: [SUGGESTION] }
  writeFileSync(file, JSON.stringify(data))
  res = await get(r, '/board/api/suggestions')
  assert.deepEqual(JSON.parse(res.body), data)
  res = await get(r, '/board/api/suggestions', 'POST')
  assert.equal(res.status, 405)
  disposers.forEach((d) => d())
})

test('apply starts the digest after the delay and stops it on dispose', async () => {
  const file = join(mkdtempSync(join(tmpdir(), 'swarm-digest-')), 'suggestions.json')
  const off = route({ suggestionsFile: file, digestEveryMs: 0 })
  const { disposers } = route({ suggestionsFile: file })
  assert.equal(disposers.length, off.disposers.length + 1)  // + the digest
  off.disposers.forEach((d) => d())
  disposers.forEach((d) => d())
})

// --- 沒有 GitHub 不跑每日聚合 ---

test('without a logged-in gh the digest skips the round, does not call think, and logs one line', async () => {
  for (const github of [async () => false, async () => { throw new Error('gh missing') }]) {
    const store = tmpStore()
    const info = []
    const { digest, think, logs } = start({ store, github, info: (...a) => info.push(a) })
    assert.equal(await digest.runNow(), null)
    assert.equal(think.calls.length, 0)
    assert.equal(store.read(), null)
    assert.equal(info.length, 1)
    assert.match(info[0][0], /gh is not logged in.*skipping/)
    assert.equal(logs.length, 0)
    digest.stop()
  }
})

test('gh is checked before every round: logged out today, logged in tomorrow runs', async () => {
  let ok = false
  let asked = 0
  const { digest, think, timers } = start({ github: async () => { asked++; return ok } })
  timers.fire()
  await flush(); await flush()
  assert.equal(think.calls.length, 0)
  assert.equal(timers.pending.length, 1, 'next round still scheduled')
  assert.equal(timers.pending[0].ms, DAY)
  ok = true
  timers.fire()
  await flush(); await flush()
  assert.equal(think.calls.length, 1)
  assert.equal(asked, 2)
  digest.stop()
})

test('apply wires the gh check into the digest: logged out → think is never sent', async () => {
  const registered = []
  const logs = []
  const calls = []
  const ctx = {
    logger: () => ({ warn: (...a) => logs.push(a), info: (...a) => logs.push(a), error() {} }),
    effect: (fn) => { fn() },
    connection: {},
    typertGateway: { invoke: async (a) => { calls.push(a); return {} } },
    webServer: { register: (r) => { registered.push(r); return () => {} } },
  }
  const file = join(mkdtempSync(join(tmpdir(), 'swarm-digest-')), 'suggestions.json')
  plugin.apply(ctx, { dataDir: mkdtempSync(join(tmpdir(), 'swarm-digest-data-')), suggestionsFile: file, digestDelayMs: 0 },
    { hasGitHub: async () => false, startFetcher: () => ({ stop() {} }) })
  await new Promise((r) => setTimeout(r, 50))
  assert.equal(calls.length, 0)
  assert.ok(logs.some((l) => /digest: gh is not logged in/.test(l[0])))
})
