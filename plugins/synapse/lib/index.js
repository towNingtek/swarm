// dsh-synapse Host plugin: synapse（策略神經系統）as a page of the DSH site.
//
//   /synapse/                 the page (read once at start)
//   /synapse/data/hub.json    the HackMD hub; projects.json Projects + EPICs; hives.json the site's swarm hives
//                             (read from dataDir on every request; only these three names; kept fresh by lib/fetcher.js)
//   /synapse/_/launcher.js    the "🧠 策略中樞" button script (also the ./client bundle)
//   GET  /synapse/api/models  the site's own DSH model catalog (same menu as DSH)
//   POST /synapse/api/ask     {model:{provider,model}, question, context} → {sessionId, after}
//   GET  /synapse/api/reply   ?session=&after=&marker= → {done, text}
//   GET  /synapse/api/canvas  the canvas (deleted hives, projects, EPICs, links), stored in the site
//   PUT  /synapse/api/canvas  {removedHives, projects, epics, links, rev} → {rev}; stale rev → 409
//                             (so every browser of this site sees the same canvas)
//
// Everything sits behind the site login (dsh-web-auth); /synapse must not be a public prefix.
// Public Host API only: webServer.register, typertGateway.invoke (same as dsh-swarm-board).
// The Copilot only answers; it never writes HackMD, swarm or GitHub.
import { randomUUID } from 'node:crypto'
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { startFetcher } from './fetcher.js'

export const name = 'dsh-synapse'
export const inject = ['webServer', 'typertGateway']

const PREFIX = '/synapse'
const DATA_FILES = new Set(['hub.json', 'projects.json', 'hives.json'])
const MAX_BODY = 256 * 1024
const MAX_QUESTION = 4000
const BASE_HEADERS = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' }
const DEFAULT_PAGE = fileURLToPath(new URL('../page/index.html', import.meta.url))
const LAUNCHER = fileURLToPath(new URL('./launcher.js', import.meta.url))
const SESSION_TITLE = '🧠 策略中樞 Copilot'
const MAX_CANVAS = 128 * 1024

// The canvas is user data, not fetched data: it lives beside dataDir, never inside it
// (data/ is served read-only and rewritten by the fetcher).
export const defaultCanvasFile = () => join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'synapse', 'canvas.json')

const ID = /^[\w.@/#-]{1,200}$/
const NODE = /^(s|h|p|e):[\w.@/#-]{1,200}$/
/** Validate a canvas from the page; returns a clean copy or throws. Only known shapes, bounded sizes. */
export function cleanCanvas(v) {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw new Error('canvas 必須是物件')
  const list = (x, test, max, label) => {
    if (x === undefined) return []
    if (!Array.isArray(x) || x.length > max) throw new Error(`${label} 格式錯誤`)
    return [...new Set(x.map((y) => { if (!test(y)) throw new Error(`${label} 有不合法的值`); return y }))]
  }
  const ids = (x) => typeof x === 'string' && ID.test(x)
  const links = (v.links ?? []).length > 2000 || !Array.isArray(v.links ?? []) ? null : (v.links ?? []).map((l) => {
    if (!l || typeof l !== 'object' || !NODE.test(l.a ?? '') || !NODE.test(l.b ?? '')) throw new Error('links 有不合法的值')
    return { a: l.a, b: l.b }
  })
  if (!links) throw new Error('links 格式錯誤')
  return {
    removedHives: list(v.removedHives, ids, 500, 'removedHives'),
    projects: list(v.projects, ids, 500, 'projects'),
    epics: list(v.epics, ids, 1000, 'epics'),
    links: links.filter((l, i) => links.findIndex((m) => m.a === l.a && m.b === l.b) === i),
  }
}

export function readCanvas(file) {
  try {
    const v = JSON.parse(readFileSync(file, 'utf8'))
    return { canvas: cleanCanvas(v.canvas ?? {}), rev: Number.isInteger(v.rev) ? v.rev : 0, savedAt: v.savedAt ?? null }
  } catch { return { canvas: cleanCanvas({}), rev: 0, savedAt: null } }
}

export const defaultDataDir = () => join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'synapse', 'data')

const RULE_PREAMBLE = `你是 synapse 的策略 Copilot，用繁體中文回答，簡短、直接。
策略中樞（synapse）是一張策略神經系統圖。只根據下面的「規則」與「目前這張圖」回答；不知道就說不知道，不要編。
你不能修改任何東西（HackMD、swarm、GitHub、這張圖都不行），也不要使用工具；使用者要改，就告訴他在哪裡改、怎麼改。`

export function send(res, status, type, body, extra = {}) {
  res.writeHead(status, { 'content-type': type, ...BASE_HEADERS, ...extra })
  res.end(body)
}
const sendJson = (res, status, value) => send(res, status, 'application/json; charset=utf-8', JSON.stringify(value))

// Same-origin JSON only: a cross-site form cannot send application/json without a preflight.
export function requestError(req) {
  if (!/^application\/json\b/i.test(req.headers?.['content-type'] ?? '')) return [415, '需要 Content-Type: application/json。']
  const origin = req.headers?.origin
  if (origin) {
    let host
    try { host = new URL(origin).host } catch {}
    if (host !== req.headers?.host) return [403, '不接受其他網站送來的請求。']
  }
  return null
}

function readBody(req, limit) {
  if (Number(req.headers?.['content-length']) > limit) return Promise.reject(Object.assign(new Error('too large'), { status: 413 }))
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', (c) => {
      size += c.length
      if (size > limit) { reject(Object.assign(new Error('too large'), { status: 413 })); req.destroy?.(); return }
      chunks.push(c)
    })
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

const call = (gateway, method, request, signal) => gateway.invoke({ namespace: 'session', method, args: { request }, signal })

/** The site's DSH model catalog, flattened to what the page needs. */
export async function models(gateway, signal) {
  const cat = await gateway.invoke({ namespace: 'session', method: 'modelCatalog', args: {}, signal })
  const routable = new Set(cat?.routableProviders ?? [])
  const groups = (cat?.groups ?? []).filter((g) => !routable.size || routable.has(g.id)).map((g) => ({
    provider: g.id, name: g.name,
    models: (g.models ?? []).map((m) => ({ id: m.id, name: m.name || m.id })),
  })).filter((g) => g.models.length)
  return { default: cat?.default ?? null, groups }
}

export function buildPrompt({ question, context, marker }) {
  return `${RULE_PREAMBLE}\n\n<!-- ${marker} -->\n## 規則與目前這張圖（JSON）\n${JSON.stringify(context).slice(0, 60000)}\n\n## 問題\n${question}`
}

const textOf = (content) => (Array.isArray(content)
  ? content.filter((p) => p && p.type === 'text' && typeof p.text === 'string').map((p) => p.text).join('\n') : '')
const eventOf = (r) => (r?.type === 'event' && r.event ? r.event : r && typeof r.type === 'string' && Number.isInteger(r.seq) ? r : undefined)

export async function headSeq(gateway, sessionId, signal) {
  const v = await call(gateway, 'projections', { sessionId }, signal)
  return Number.isInteger(v?.asOfSeq) ? v.asOfSeq : -1
}

export async function eventsSince(gateway, sessionId, after, head, signal, maxPages = 10) {
  const pages = []
  let beforeSeq
  for (let i = 0; i < maxPages; i++) {
    const page = await call(gateway, 'page', { address: { kind: 'session', sessionId }, throughSeq: head, maxMessages: 200, ...(beforeSeq === undefined ? {} : { beforeSeq }) }, signal)
    const events = (page?.records ?? []).map(eventOf).filter(Boolean)
    pages.unshift(events)
    if (!page?.hasMore || !events.length || events[0].seq <= after + 1) break
    beforeSeq = events[0].seq
  }
  return pages.flat().filter((e) => e.seq > after)
}

/** Reply text for the user message carrying `marker`, once its turn ended; null while running. */
export function replyIn(events, marker) {
  const ask = events.findIndex((e) => e.type === 'user/message' && textOf(e.data?.content).includes(marker))
  if (ask < 0) return null
  const parts = []
  for (const e of events.slice(ask + 1)) {
    if (e.type === 'assistant/message' && e.data?.message?.role === 'assistant') parts.push(textOf(e.data.message.content))
    if (e.type === 'turn/end') {
      const k = e.data?.reason?.kind
      if (k === 'error' || k === 'cancelled') throw Object.assign(new Error(`turn ${k}`), { code: k })
      return parts.filter(Boolean).join('\n')
    }
  }
  return null
}

export function apply(ctx, config = {}) {
  const logger = ctx.logger?.('synapse') ?? console
  const dataDir = config.dataDir || defaultDataDir()
  const pagePath = config.page || DEFAULT_PAGE
  let page = null
  try { page = readFileSync(pagePath, 'utf8') } catch (e) { logger.warn?.('synapse: page not found at %s', pagePath) }
  const launcher = readFileSync(LAUNCHER, 'utf8')
  const gateway = ctx.typertGateway
  const canvasFile = config.canvasFile || defaultCanvasFile()
  // 資料抓取在站台內：HackMD（站台的 HackMD token）、GitHub（站台的 gh）、hives（站台的 swarm）。
  // Under `node --test` nothing starts unless the test asks for it.
  if (config.fetch !== false && !process.env.NODE_TEST_CONTEXT) {
    const stop = startFetcher({
      dataDir, logger,
      hub: { note: config.hackmdNote, tokenFile: config.hackmdTokenFile, url: config.hubUrl },
      projects: { orgs: Array.isArray(config.orgs) ? config.orgs : [] },
      hivesDir: config.hivesDir,
      devices: Array.isArray(config.devices) ? config.devices : [],
    })
    ctx.effect(() => stop, 'synapse: fetcher')
  }
  let sessionId = null                // one Copilot session per site process; created on first question
  const mine = new Set()              // sessions this plugin created (reply only reads these)
  let creating = null

  async function ensureSession(signal) {
    if (sessionId) return sessionId
    if (!creating) {
      creating = (async () => {
        const v = await call(gateway, 'create', config.cwd ? { cwd: config.cwd } : {}, signal)
        if (typeof v?.sessionId !== 'string') throw new Error('DSH 沒有回傳 session')
        try { await call(gateway, 'rename', { sessionId: v.sessionId, title: SESSION_TITLE }, signal) } catch {}
        mine.add(v.sessionId)
        sessionId = v.sessionId
        return sessionId
      })().finally(() => { creating = null })
    }
    return creating
  }

  async function handler(req, res) {
    const url = new URL(req.url, 'http://x')
    const p = url.pathname
    const head = req.method === 'HEAD'
    if ((req.method === 'GET' || head) && (p === PREFIX || p === PREFIX + '/')) {
      if (p === PREFIX) return send(res, 301, 'text/plain; charset=utf-8', '', { location: PREFIX + '/' })
      if (!page) return send(res, 503, 'text/plain; charset=utf-8', 'synapse page missing')
      return send(res, 200, 'text/html; charset=utf-8', head ? undefined : page)
    }
    if ((req.method === 'GET' || head) && p === PREFIX + '/_/launcher.js') return send(res, 200, 'text/javascript; charset=utf-8', head ? undefined : launcher)
    if ((req.method === 'GET' || head) && p.startsWith(PREFIX + '/data/')) {
      const f = p.slice((PREFIX + '/data/').length)
      if (!DATA_FILES.has(f)) return send(res, 404, 'text/plain; charset=utf-8', 'not found')
      try { return send(res, 200, 'application/json; charset=utf-8', head ? undefined : await readFile(join(dataDir, f), 'utf8')) }
      catch { return send(res, 404, 'text/plain; charset=utf-8', 'not found') }
    }
    if (req.method === 'GET' && p === PREFIX + '/api/canvas') return sendJson(res, 200, readCanvas(canvasFile))
    if (req.method === 'PUT' && p === PREFIX + '/api/canvas') {
      const bad = requestError(req)
      if (bad) return sendJson(res, bad[0], { error: bad[1] })
      let body, canvas
      try { body = JSON.parse(await readBody(req, MAX_CANVAS)) } catch (e) { return sendJson(res, e?.status ?? 400, { error: e?.status === 413 ? '畫布太大。' : '內容不是 JSON。' }) }
      try { canvas = cleanCanvas(body?.canvas) } catch (e) { return sendJson(res, 400, { error: e.message }) }
      const cur = readCanvas(canvasFile)
      // Another browser saved in between: refuse, so the page reloads instead of silently overwriting.
      if (body?.rev !== cur.rev) return sendJson(res, 409, { error: '畫布在別的地方被改過了。', ...cur })
      const next = { canvas, rev: cur.rev + 1, savedAt: new Date().toISOString() }
      mkdirSync(dirname(canvasFile), { recursive: true })
      const tmp = `${canvasFile}.tmp-${process.pid}`
      writeFileSync(tmp, JSON.stringify(next), { mode: 0o600 })
      renameSync(tmp, canvasFile)
      return sendJson(res, 200, { rev: next.rev, savedAt: next.savedAt })
    }
    if (req.method === 'GET' && p === PREFIX + '/api/models') {
      if (!gateway) return sendJson(res, 503, { error: '這個站台沒有接上 DSH。' })
      try { return sendJson(res, 200, await models(gateway)) } catch (e) { return sendJson(res, 502, { error: '讀不到模型清單：' + (e?.message ?? e) }) }
    }
    if (req.method === 'POST' && p === PREFIX + '/api/ask') {
      if (!gateway) return sendJson(res, 503, { error: '這個站台沒有接上 DSH。' })
      const bad = requestError(req)
      if (bad) return sendJson(res, bad[0], { error: bad[1] })
      let body
      try { body = JSON.parse(await readBody(req, MAX_BODY)) } catch (e) { return sendJson(res, e?.status ?? 400, { error: e?.status === 413 ? '內容太大。' : '內容不是 JSON。' }) }
      const question = typeof body?.question === 'string' ? body.question.trim() : ''
      if (!question) return sendJson(res, 400, { error: '請先寫下問題。' })
      if (question.length > MAX_QUESTION) return sendJson(res, 400, { error: `問題最多 ${MAX_QUESTION} 個字。` })
      const m = body?.model
      try {
        const sid = await ensureSession()
        if (m && typeof m.provider === 'string' && typeof m.model === 'string') {
          const cat = await models(gateway)
          if (!cat.groups.some((g) => g.provider === m.provider && g.models.some((x) => x.id === m.model))) return sendJson(res, 400, { error: '這個站台沒有這個模型。' })
          await call(gateway, 'selectModel', { sessionId: sid, provider: m.provider, model: m.model })
        }
        const after = await headSeq(gateway, sid)
        const marker = 'synapse-' + randomUUID()
        const context = body.context && typeof body.context === 'object' && !Array.isArray(body.context) ? body.context : {}
        await call(gateway, 'prompt', { requestId: randomUUID(), sessionId: sid, mode: 'queue', content: [{ type: 'text', text: buildPrompt({ question, context, marker }) }] })
        return sendJson(res, 200, { sessionId: sid, after, marker })
      } catch (e) {
        logger.warn?.('synapse ask failed: %s', e?.stack ?? String(e))
        return sendJson(res, 502, { error: 'DSH 沒有接受這個問題：' + (e?.message ?? e) })
      }
    }
    if (req.method === 'GET' && p === PREFIX + '/api/reply') {
      if (!gateway) return sendJson(res, 503, { error: '這個站台沒有接上 DSH。' })
      const sid = url.searchParams.get('session') ?? ''
      const after = Number(url.searchParams.get('after'))
      const marker = url.searchParams.get('marker') ?? ''
      if (!mine.has(sid) || !/^synapse-[0-9a-f-]{36}$/.test(marker)) return sendJson(res, 404, { error: '不是 synapse 開的對話。' })
      try {
        const text = replyIn(await eventsSince(gateway, sid, Number.isInteger(after) ? after : -1, await headSeq(gateway, sid)), marker)
        return sendJson(res, 200, text === null ? { done: false } : { done: true, text })
      } catch (e) {
        return sendJson(res, 200, { done: true, error: '模型沒有完成：' + (e?.code ?? e?.message ?? e) })
      }
    }
    return send(res, 404, 'text/plain; charset=utf-8', 'not found')
  }

  ctx.effect(() => ctx.webServer.register({
    kind: 'prefix',
    path: PREFIX,
    handler: async (req, res) => {
      try { await handler(req, res) } catch (error) {
        logger.error?.('synapse route failed: %s', error?.stack ?? String(error))
        if (!res.headersSent) send(res, 500, 'text/plain; charset=utf-8', 'internal error')
        else res.destroy()
      }
    },
  }), 'synapse: /synapse')
}
