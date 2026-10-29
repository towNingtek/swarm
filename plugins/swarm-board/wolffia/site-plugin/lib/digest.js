// 每日聚合: ask the global 「想」 session (project "*") to read every board and
// suggest EPICs — issues in one project that belong together (vertical) and
// needs shared across projects (horizontal). Suggestions only: the question
// forbids opening cards or changing status.
//
// The reply ends with {"suggestions":[...]}; that JSON goes to
// $DSH_HOME/swarm-board/suggestions.json with generatedAt and sessionId, and
// GET /board/api/suggestions serves it. A reply without parsable JSON keeps
// the previous file.
import { randomUUID } from 'node:crypto'
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'

export const DAY = 24 * 60 * 60 * 1000
const MAX_TIMER = 2 ** 31 - 1
const MAX_SUGGESTIONS = 50
const ISSUE = /^[\w.-]+\/[\w.-]+#\d{1,9}$/

export const QUESTION = [
  '【每日聚合】請讀下面全部 project 的協作星系資料，找出：',
  '（1）同一個 project 裡，應該合成一個 EPIC 的 issue（縱向，kind: "vertical"）；',
  '（2）跨 project 的共同需求，應該合成一個跨 project EPIC 的 issue（橫向，kind: "horizontal"）。',
  '只提建議，不要開卡、不要建 EPIC、不要改任何 issue 的狀態或欄位。',
  '回覆最後附一段 JSON（不要省略），格式：',
  '{"suggestions":[{"title":"建議的 EPIC 標題","kind":"vertical|horizontal","issues":["owner/repo#N"],"why":"理由"}]}',
  '沒有建議時回 {"suggestions":[]}。',
].join('\n')

export const defaultSuggestionsFile = () => join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'swarm-board', 'suggestions.json')

/** suggestions.json: { generatedAt, sessionId, suggestions }. read() is null when missing or broken. */
export function createSuggestionStore(file = defaultSuggestionsFile()) {
  return {
    file,
    read() {
      try {
        const data = JSON.parse(readFileSync(file, 'utf8'))
        return data && Array.isArray(data.suggestions) ? data : null
      } catch {
        return null
      }
    },
    write(data) {
      mkdirSync(dirname(file), { recursive: true })
      const tmp = `${file}.${process.pid}.${randomUUID()}.tmp`
      writeFileSync(tmp, JSON.stringify(data, null, 2) + '\n', { mode: 0o600 })
      renameSync(tmp, file)
    },
  }
}

// The balanced {...} starting at `start`, string-aware; null if it never closes.
function objectAt(text, start) {
  let depth = 0
  let inString = false
  for (let i = start; i < text.length; i++) {
    const c = text[i]
    if (inString) {
      if (c === '\\') i++
      else if (c === '"') inString = false
    } else if (c === '"') inString = true
    else if (c === '{') depth++
    else if (c === '}' && --depth === 0) return text.slice(start, i + 1)
  }
  return null
}

const str = (v, n) => (typeof v === 'string' ? v.trim().slice(0, n) : '')

function cleanSuggestion(s) {
  if (!s || typeof s !== 'object') return null
  const title = str(s.title, 200)
  const kind = s.kind === 'vertical' || s.kind === 'horizontal' ? s.kind : null
  const issues = Array.isArray(s.issues) ? [...new Set(s.issues.map((x) => str(x, 200)).filter((x) => ISSUE.test(x)))] : []
  if (!title || !kind || !issues.length) return null
  return { title, kind, issues, why: str(s.why, 2000) }
}

/** The last {"suggestions":[...]} in a reply, cleaned; null when there is none. */
export function parseSuggestions(reply) {
  const text = String(reply ?? '')
  for (let key = text.lastIndexOf('"suggestions"'); key >= 0; key = key ? text.lastIndexOf('"suggestions"', key - 1) : -1) {
    for (let start = text.lastIndexOf('{', key); start >= 0; start = start ? text.lastIndexOf('{', start - 1) : -1) {
      const raw = objectAt(text, start)
      if (!raw) continue
      let data
      try { data = JSON.parse(raw) } catch { continue }
      if (data && Array.isArray(data.suggestions)) return data.suggestions.map(cleanSuggestion).filter(Boolean).slice(0, MAX_SUGGESTIONS)
    }
  }
  return null
}

const replyText = (r) => (typeof r === 'string' ? r : typeof r?.text === 'string' ? r.text : '')

/**
 * Run the digest now, then every `everyMs` (0 = never). On start, runs at once
 * (after `delayMs`) when the last suggestions are older than `everyMs`.
 *   think({ project, question, context }) -> { sessionId, text } | string
 *   readBoards() -> every project's board summary
 *   store: { read(), write(data) }  (createSuggestionStore)
 *   github() -> boolean (optional): checked before every run; false skips that run with one log line
 * One run at a time: runNow() during a run returns that run.
 */
export function startDigest({ think, readBoards, github, store, everyMs = DAY, delayMs = 0, now = Date.now, log = console, timers = globalThis }) {
  let timer = null
  let running = null
  let stopped = false

  const schedule = (ms) => {
    if (stopped || !(everyMs > 0)) return
    timers.clearTimeout(timer)
    timer = timers.setTimeout(tick, Math.max(0, Math.min(ms, MAX_TIMER)))
    timer?.unref?.()
  }

  function due() {
    const last = Date.parse(store.read()?.generatedAt ?? '')
    return Number.isNaN(last) ? 0 : Math.max(0, last + everyMs - now())
  }

  function tick() {
    timer = null
    const wait = due()
    if (wait > 0) return schedule(wait)
    runNow().finally(() => schedule(everyMs))
  }

  async function run() {
    if (github) {
      let ok = false
      try { ok = (await github()) === true } catch {}
      if (!ok) {
        (log.info ?? log.warn)?.call(log, 'swarm-board digest: gh is not logged in (gh auth status failed); skipping this round')
        return null
      }
    }
    let result
    try {
      const projects = await readBoards()
      result = await think({ project: '*', question: QUESTION, context: { digest: true, projects } })
    } catch (error) {
      log.warn?.('swarm-board digest: think failed: %s', error?.code ?? error?.message ?? String(error))
      return null
    }
    const suggestions = parseSuggestions(replyText(result))
    if (!suggestions) {
      log.warn?.('swarm-board digest: no {"suggestions":[...]} JSON in the reply; keeping the previous suggestions')
      return null
    }
    const data = { generatedAt: new Date(now()).toISOString(), sessionId: result?.sessionId ?? null, suggestions }
    try {
      store.write(data)
    } catch (error) {
      log.warn?.('swarm-board digest: cannot write suggestions: %s', error?.message ?? String(error))
      return null
    }
    return data
  }

  function runNow() {
    if (!running) running = run().finally(() => { running = null })
    return running
  }

  if (everyMs > 0) schedule(delayMs)

  return {
    runNow,
    stop() {
      stopped = true
      timers.clearTimeout(timer)
      timer = null
    },
  }
}

// ---------------------------------------------------------------------------
// The reply, read back from the DSH session (public Host API only):
//   session/projections -> asOfSeq (the head), session/page -> events.

const sessionCall = (gateway, method, request, signal) => gateway.invoke({ namespace: 'session', method, args: { request }, signal })

function eventOf(record) {
  if (record?.type === 'event' && record.event) return record.event
  if (record && typeof record.type === 'string' && Number.isInteger(record.seq)) return record
  return undefined
}

const textOf = (content) => (Array.isArray(content)
  ? content.filter((p) => p && p.type === 'text' && typeof p.text === 'string').map((p) => p.text).join('\n')
  : '')

export async function headSeq(gateway, sessionId, signal) {
  const value = await sessionCall(gateway, 'projections', { sessionId }, signal)
  return Number.isInteger(value?.asOfSeq) ? value.asOfSeq : -1
}

// Events with seq > after, oldest first; at most `maxPages` pages back from `head`.
export async function eventsSince(gateway, sessionId, after, head, signal, maxPages = 20) {
  const pages = []
  let beforeSeq
  for (let i = 0; i < maxPages; i++) {
    const request = { address: { kind: 'session', sessionId }, throughSeq: head, maxMessages: 200, ...(beforeSeq === undefined ? {} : { beforeSeq }) }
    const page = await sessionCall(gateway, 'page', request, signal)
    const events = (page?.records ?? []).map(eventOf).filter(Boolean)
    pages.unshift(events)
    if (!page?.hasMore || !events.length || events[0].seq <= after + 1) break
    beforeSeq = events[0].seq
  }
  return pages.flat().filter((e) => e.seq > after)
}

/** The model's text for the first user message after `after` that contains `marker`, once its turn ended; null while still running. */
export function replyIn(events, marker) {
  const ask = events.findIndex((e) => e.type === 'user/message' && e.data?.source?.kind === 'user' && textOf(e.data.content).includes(marker))
  if (ask < 0) return null
  const parts = []
  for (const e of events.slice(ask + 1)) {
    if (e.type === 'assistant/message' && e.data?.message?.role === 'assistant') parts.push(textOf(e.data.message.content))
    if (e.type === 'turn/end') {
      // blocked: DSH refused the turn (e.g. the session is archived) and nothing will answer.
      if (e.data?.reason?.kind === 'error' || e.data?.reason?.kind === 'cancelled' || e.data?.reason?.kind === 'blocked') {
        throw Object.assign(new Error(`turn ${e.data.reason.kind}`), { code: `digest/turn-${e.data.reason.kind}` })
      }
      return parts.filter(Boolean).join('\n')
    }
  }
  return null
}

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  if (signal?.aborted) return reject(signal.reason ?? new Error('aborted'))
  const t = setTimeout(done, ms)
  function done() { signal?.removeEventListener('abort', abort); resolve() }
  function abort() { clearTimeout(t); reject(signal.reason ?? new Error('aborted')) }
  signal?.addEventListener('abort', abort, { once: true })
})

/**
 * A think() for startDigest: send through lib/think.js `send` (which queues the
 * prompt and returns { sessionId }), then poll the session until that turn ends.
 */
export function replyingThink({ gateway, store, send, signal, pollMs = 5000, timeoutMs = 60 * 60 * 1000, now = Date.now }) {
  return async ({ project, question, context }) => {
    const known = await store.get(project)
    const after = known ? await headSeq(gateway, known, signal) : -1
    const { sessionId } = await send({ project, question, context })
    const from = sessionId === known ? after : -1
    const marker = question.split('\n')[0]
    const deadline = now() + timeoutMs
    for (;;) {
      const head = await headSeq(gateway, sessionId, signal)
      if (head > from) {
        const text = replyIn(await eventsSince(gateway, sessionId, from, head, signal), marker)
        if (text !== null) return { sessionId, text }
      }
      if (now() >= deadline) throw Object.assign(new Error('no reply in time'), { code: 'digest/timeout' })
      await sleep(pollMs, signal)
    }
  }
}
