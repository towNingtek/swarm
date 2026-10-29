// dsh-swarm-board Host plugin: the swarm board as a page of the DSH site.
//
//   /board/                   the board page (wolffia/v8.html, read once at start)
//   /board/data/<file>.js     board data, read from dataDir on every request;
//                             only index.js, p<number>.js (default org),
//                             p-<org>-<number>.js (another org's project) and
//                             g-<12 hex>.js (a GitLab project, lib/gitlab.js), anything else 404
//   /board/_/launcher.js      the "🌌 協作星系" button script (also the ./client bundle)
//   POST /board/api/think     「想」: question + board context into the site's DSH
//                             session (lib/think.js); JSON body up to 64KB.
//                             Full path /board/api/think|api/think relative to
//                             the page — v8.html posts to the relative one.
//   GET /board/data/status.json {"github": true|false}: whether the site's gh is logged in
//                             (`gh auth status`, rechecked at most every 30s, so a login
//                             after DSH started counts without a restart). Without it the
//                             board fetcher does not run and v8.html shows「尚未連接 GitHub」.
//   GET /board/api/suggestions 每日聚合的「💡 建議」(lib/digest.js), suggestions.json
//                             as is; {"suggestions":[]} when there is none yet.
//   POST /board/api/epic/go | api/epic/rego | api/epic/automerge | api/status
//                             放行／重新放行／自動合併開關／拖曳改狀態 with the site's gh
//                             (lib/actions.js); JSON body up to 4KB; 409 without a logged-in gh.
//                             api/status for a "gitlab:<path>" project goes to GitLab instead (no gh
//                             needed; 409 without a GitLab token).
//   GET /board/api/projects/available | POST /board/api/projects/add {owner, number}
//                             「＋ 加入 project」(lib/projects.js): the gh login's and its orgs' open
//                             projects (cached 10 minutes; {"github":false} without gh), and adding one
//                             to projects.json (fetched at once; the new index.js in the answer).
//
// /board is NOT public: it stays behind whatever login gate the site runs.
// On start, the bundled `epic` skill (skills/epic) is installed into $DSH_HOME/skills/epic (lib/skill.js).
//
// Public Host API only: webServer.register, typertGateway.invoke. No DSH core patch.
import { execFile } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { homedir, hostname } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createSuggestionStore, DAY, eventsSince, headSeq, replyIn, replyingThink, startDigest } from './digest.js'
import { ActionError, createActions, dataFile, projectKey, runGh, setDefaultOrg, splitKey, TOWN, columnMap } from './actions.js'
import { createGitlabMover, defaultTokenFile, gitlabBase, isGitlabKey, labelsFor, readToken } from './gitlab.js'
import { startFetcher as realStartFetcher } from './fetcher.js'
import { boardWorkspace, createStore, readBoards, think, ThinkError } from './think.js'
import { createRunning, ghGraphql, ownersFromBoards } from './running.js'
import { createProjectPicker, readProjectsJson } from './projects.js'
import { createGhEnv, createRelayCredential, readRelayKey } from './credential.js'
import { defaultSkillsDir, installSkill as realInstallSkill } from './skill.js'

export const name = 'dsh-swarm-board'
export const inject = ['connection', 'webServer', 'typertGateway']

const PREFIX = '/board'
const DATA_FILE = /^(index|p\d{1,9}|p-[a-z0-9][a-z0-9-]{0,38}-\d{1,9}|g-[0-9a-f]{12})\.js$/
export const MAX_THINK_BODY = 64 * 1024
export const MAX_ACTION_BODY = 4 * 1024
const NO_GITHUB = '尚未連接 GitHub：請在站台的 AI 對話說「幫我連接 GitHub」'
const ACTIONS = { '/api/epic/go': 'go', '/api/epic/rego': 'rego', '/api/epic/automerge': 'automerge', '/api/status': 'status' }
const BASE_HEADERS = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' }
const DEFAULT_HTML = fileURLToPath(new URL('../../v8.html', import.meta.url))
const LAUNCHER = fileURLToPath(new URL('./launcher.js', import.meta.url))
const DEFAULT_FETCH_SCRIPT = fileURLToPath(new URL('../../fetch-project.py', import.meta.url))
const FETCH_EVERY_MS = 10 * 60_000
const FETCH_ONE_TIMEOUT_MS = 180_000

export const defaultDataDir = () => join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'swarm-board', 'data')

function send(res, status, type, body, head = false, extra = {}) {
  res.writeHead(status, { 'content-type': type, ...BASE_HEADERS, ...extra })
  res.end(head ? undefined : body)
}

const notFound = (res, head) => send(res, 404, 'text/plain; charset=utf-8', 'not found', head)
const sendJson = (res, status, value, extra) => send(res, status, 'application/json; charset=utf-8', JSON.stringify(value), false, extra)

function readBody(req, limit) {
  const declared = Number(req.headers?.['content-length'])
  if (declared > limit) return Promise.reject(new ThinkError('think/too-large', `內容超過 ${limit / 1024}KB。`, 413))
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > limit) { reject(new ThinkError('think/too-large', `內容超過 ${limit / 1024}KB。`, 413)); req.destroy?.(); return }
      chunks.push(chunk)
    })
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

// Same-origin JSON only: a cross-site form cannot send application/json without a preflight.
function thinkRequestError(req) {
  if (!/^application\/json\b/i.test(req.headers?.['content-type'] ?? '')) return [415, 'think/content-type', '需要 Content-Type: application/json。']
  const origin = req.headers?.origin
  if (origin) {
    let host
    try { host = new URL(origin).host } catch {}
    if (host !== req.headers?.host) return [403, 'think/origin', '不接受其他網站送來的請求。']
  }
  return null
}

async function thinkRoute(req, res, opts) {
  const bad = thinkRequestError(req)
  if (bad) return sendJson(res, bad[0], { error: bad[1], message: bad[2] })
  let body
  try {
    body = JSON.parse(await readBody(req, MAX_THINK_BODY))
  } catch (error) {
    if (error instanceof ThinkError) return sendJson(res, error.status, { error: error.code, message: error.message })
    return sendJson(res, 400, { error: 'think/bad-json', message: '內容不是 JSON。' })
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) return sendJson(res, 400, { error: 'think/bad-json', message: '內容不是 JSON 物件。' })
  const context = body.context && typeof body.context === 'object' && !Array.isArray(body.context) ? { ...body.context } : {}
  if (Array.isArray(body.selection)) context.selection = body.selection.filter((n) => Number.isInteger(n)).slice(0, 500)
  try {
    // Where the session was before this question, so the page can poll for just this reply.
    let storeKey = null
    try { storeKey = normalizeProject(body.project) } catch {}
    const known = opts.store && storeKey !== null ? await opts.store.get(storeKey) : null
    let after = -1
    if (known) { try { after = await headSeq(opts.gateway, known, opts.signal) } catch { after = -1 } }
    const { sessionId, created, replaced } = await think({
      gateway: opts.gateway, store: opts.store, project: body.project, question: body.question, context,
      boards: () => readBoards(opts.dataDir), cwd: opts.cwd, workspace: opts.workspace, archived: opts.archived, signal: opts.signal,
    })
    return sendJson(res, 200, { sessionId, created, after: sessionId === known ? after : -1, ...(replaced ? { replaced: true } : {}) })
  } catch (error) {
    if (error instanceof ThinkError) return sendJson(res, error.status, { error: error.code, message: error.message })
    opts.logger?.warn?.('swarm-board: think failed: %s', error?.code ?? error?.message ?? String(error))
    return sendJson(res, 502, { error: 'think/gateway', message: `送不進站台 DSH：${error?.code ?? error?.message ?? '未知錯誤'}` })
  }
}

// 放行／重新放行／自動合併／改狀態: same request checks as 「想」, then the site's gh
// (改狀態 of a GitLab project: GitLab, with or without gh).
const forGitlab = (kind, body) => kind === 'status' && body && typeof body === 'object' && isGitlabKey(projectKey(body.project))

async function actionRoute(req, res, kind, opts) {
  const bad = thinkRequestError(req)
  if (bad) return sendJson(res, bad[0], { error: bad[2] })
  if (!opts.act) return sendJson(res, 409, { error: NO_GITHUB })
  const hasGitHub = await githubNow(opts.github)
  if (!hasGitHub && kind !== 'status') return sendJson(res, 409, { error: NO_GITHUB })
  let body
  try {
    body = JSON.parse(await readBody(req, MAX_ACTION_BODY))
  } catch (error) {
    if (!hasGitHub) return sendJson(res, 409, { error: NO_GITHUB })
    if (error instanceof ThinkError) return sendJson(res, error.status, { error: error.message })
    return sendJson(res, 400, { error: '內容不是 JSON。' })
  }
  const gitlab = forGitlab(kind, body)
  if (!hasGitHub && !gitlab) return sendJson(res, 409, { error: NO_GITHUB })
  let out
  try {
    out = await opts.act(kind, body)
  } catch (error) {
    if (error instanceof ActionError) return sendJson(res, error.status, { error: error.message })
    opts.logger?.warn?.('swarm-board: %s failed: %s', kind, error?.message ?? String(error))
    return sendJson(res, 502, { error: `${gitlab ? 'GitLab' : 'GitHub'} 更新失敗：${String(error?.message ?? error).slice(0, 200)}` })
  }
  try { await opts.refetch?.(kind, body) } catch {}
  return sendJson(res, 200, out)
}

function readOnce(file, logger) {
  try {
    return readFileSync(file)
  } catch (error) {
    logger?.warn?.('swarm-board: cannot read %s: %s', file, error?.message ?? String(error))
    return null
  }
}

// `gh auth status` exits 0 only when gh is installed and logged in.
export function ghLoggedIn() {
  return new Promise((resolve) => {
    execFile('gh', ['auth', 'status'], { timeout: 15_000 }, (err) => resolve(!err))
  })
}

// opts.github: a function returning (a promise of) true/false, or (older callers) a promise.
const githubNow = async (github) => {
  try { return (typeof github === 'function' ? await github() : await github) === true } catch { return false }
}

export const GITHUB_RECHECK_MS = 30_000

// The site may log gh in (or out) while DSH is running: ask again, but at most once per
// ttlMs, sharing one in-flight check. onChange(ok) runs whenever the answer differs from
// the previous one (the first answer counts as a change).
export function githubGate(hasGitHub, { ttlMs = GITHUB_RECHECK_MS, now = Date.now, onChange } = {}) {
  let last = null, at = -Infinity, inflight = null
  return function github() {
    if (inflight) return inflight
    if (last !== null && now() - at < ttlMs) return Promise.resolve(last)
    inflight = Promise.resolve().then(() => hasGitHub()).then((ok) => ok === true, () => false).then((ok) => {
      const changed = ok !== last
      last = ok; at = now(); inflight = null
      if (changed) { try { onChange?.(ok) } catch {} }
      return ok
    })
    return inflight
  }
}

// fetch-project.py --one reads projects.json from beside itself; the site layout puts
// it one level above dataDir. Written once from config.projects, never overwritten.
export function ensureProjectsJson(dataDir, projects, logger) {
  if (!projects.length) return false
  const file = join(dirname(dataDir), 'projects.json')
  try {
    mkdirSync(dirname(file), { recursive: true })
    writeFileSync(file, JSON.stringify({ default: projects[0], projects }, null, 2) + '\n', { flag: 'wx' })
    return true
  } catch (error) {
    if (error?.code !== 'EEXIST') logger?.warn?.('swarm-board: cannot write %s: %s', file, error?.message ?? String(error))
    return false
  }
}

// window.PROJECTS["org/N"]=<json>; from p-<org>-<N>.js (fetch-project.py). Parsed as JSON, never evaluated.
export async function readKeyedBoard(dataDir, key) {
  let text
  try { text = await readFile(join(dataDir, dataFile(key)), 'utf8') } catch { return null }
  const m = /window\.PROJECTS\[("[^"]*"|\d+)\]\s*=\s*([\s\S]*?);?\s*$/.exec(text)
  if (!m) return null
  try {
    const board = JSON.parse(m[2])
    return board && typeof board === 'object' ? board : null
  } catch {
    return null
  }
}

// keys: config.projects as keys (projectKey); only used for other orgs' data_at.
export function createHandler({ html, launcher, dataDir, gateway, store, suggestions, cwd, workspace, archived, signal, logger, github, running, act, refetch, keys, picker }) {
  return async function boardRoute(req, res) {
    const method = req.method ?? 'GET'
    const head = method === 'HEAD'

    let pathname
    try {
      pathname = new URL(req.url ?? '/', 'http://board.invalid').pathname
    } catch {
      return notFound(res, head)
    }

    if (pathname === `${PREFIX}/api/think/reply`) {
      // GET ?session=<id>&after=<seq>&q=<first line of the question>: the harness reply once the turn ended.
      // { done:false } while it is still working. Only sessions this board created (in the store).
      if (method !== 'GET' && !head) return send(res, 405, 'text/plain; charset=utf-8', 'method not allowed', head, { allow: 'GET, HEAD' })
      if (!gateway || !store) return sendJson(res, 503, { error: 'think/unavailable', message: '這個站台沒有接上 DSH session。' })
      const u = new URL(req.url ?? '/', 'http://board.invalid')
      const sessionId = u.searchParams.get('session') ?? ''
      const after = Number.parseInt(u.searchParams.get('after') ?? '-1', 10)
      const marker = (u.searchParams.get('q') ?? '').split('\n')[0].slice(0, 200)
      const mine = Object.values((await store.all?.()) ?? {})
      if (!/^session-[0-9a-f-]{8,64}$/.test(sessionId) || !mine.includes(sessionId)) {
        return sendJson(res, 404, { error: 'think/unknown-session', message: '不是協作星系開的對話。' })
      }
      try {
        const headAt = await headSeq(gateway, sessionId, signal)
        if (headAt <= after) return sendJson(res, 200, { done: false })
        const text = replyIn(await eventsSince(gateway, sessionId, Number.isInteger(after) ? after : -1, headAt, signal), marker)
        return sendJson(res, 200, text === null ? { done: false } : { done: true, text })
      } catch (error) {
        const code = String(error?.code ?? '')
        if (code === 'digest/turn-blocked') {
          return sendJson(res, 200, { done: true, error: 'blocked', text: '', message: 'DSH 沒有執行這個對話（它可能已被封存）。再按一次「📊 分析」會開一個新的分析對話。' })
        }
        if (code.startsWith('digest/turn-')) return sendJson(res, 200, { done: true, error: 'turn', text: '' })
        return sendJson(res, 502, { error: 'think/gateway', message: `讀不到站台 DSH 的回覆：${error?.code ?? error?.message ?? '未知錯誤'}` })
      }
    }
    if (pathname === `${PREFIX}/api/think`) {
      if (method !== 'POST') return send(res, 405, 'text/plain; charset=utf-8', 'method not allowed', head, { allow: 'POST' })
      if (!gateway || !store) return sendJson(res, 503, { error: 'think/unavailable', message: '這個站台沒有接上 DSH session。' })
      return thinkRoute(req, res, { gateway, store, dataDir, cwd, workspace, archived, signal, logger })
    }
    const action = pathname.startsWith(`${PREFIX}/api/`) ? ACTIONS[pathname.slice(PREFIX.length)] : undefined
    if (action) {
      if (method !== 'POST') return send(res, 405, 'text/plain; charset=utf-8', 'method not allowed', head, { allow: 'POST' })
      return actionRoute(req, res, action, { github, act, refetch, logger })
    }
    if (pathname === `${PREFIX}/api/projects/available`) {
      if (method !== 'GET' && !head) return send(res, 405, 'text/plain; charset=utf-8', 'method not allowed', head, { allow: 'GET, HEAD' })
      if (!picker || !(await githubNow(github))) return send(res, 200, 'application/json; charset=utf-8', JSON.stringify({ github: false }), head)
      let body
      try {
        body = await picker.available()
      } catch (error) {
        logger?.warn?.('swarm-board: projects/available failed: %s', error?.message ?? String(error))
        return sendJson(res, 502, { error: `讀不到 GitHub 的 project：${String(error?.message ?? error).slice(0, 200)}` })
      }
      return send(res, 200, 'application/json; charset=utf-8', JSON.stringify(body), head)
    }
    if (pathname === `${PREFIX}/api/projects/add`) {
      if (method !== 'POST') return send(res, 405, 'text/plain; charset=utf-8', 'method not allowed', head, { allow: 'POST' })
      return actionRoute(req, res, 'projects/add', { github, act: picker && ((kind, body) => picker.add(body)), logger })
    }
    if (pathname === `${PREFIX}/api/running`) {
      // Bees: issues with swarm:running (site gh), plus data_at = fetched time per project file,
      // so an open board refreshes itself when the fetcher wrote newer data.
      if (method !== 'GET' && !head) return send(res, 405, 'text/plain; charset=utf-8', 'method not allowed', head, { allow: 'GET, HEAD' })
      const body = running ? await running() : { at: new Date().toISOString(), items: [] }
      const dataAt = {}
      for (const b of await readBoards(dataDir)) if (b.num && b.fetched) dataAt[b.num] = b.fetched
      // Other orgs' projects (p-<org>-<N>.js): keyed "org/N", the page's PROJECT_NUM for them.
      for (const k of keys ?? []) {
        if (typeof k !== 'string') continue
        const at = (await readKeyedBoard(dataDir, k))?.fetched
        if (at) dataAt[k] = at
      }
      return send(res, 200, 'application/json; charset=utf-8', JSON.stringify({ ...body, data_at: dataAt }), head)
    }
    if (pathname === `${PREFIX}/api/suggestions`) {
      if (method !== 'GET' && !head) return send(res, 405, 'text/plain; charset=utf-8', 'method not allowed', head, { allow: 'GET, HEAD' })
      return send(res, 200, 'application/json; charset=utf-8', JSON.stringify(suggestions?.read() ?? { suggestions: [] }), head)
    }
    if (method !== 'GET' && !head) return send(res, 405, 'text/plain; charset=utf-8', 'method not allowed', head, { allow: 'GET, HEAD' })

    // Relative data/... URLs in the page need the trailing slash.
    if (pathname === PREFIX) return send(res, 308, 'text/plain; charset=utf-8', '', head, { location: `${PREFIX}/` })
    if (pathname === `${PREFIX}/` || pathname === `${PREFIX}/index.html`) {
      if (html === null) return send(res, 503, 'text/plain; charset=utf-8', 'board page unavailable', head)
      return send(res, 200, 'text/html; charset=utf-8', html, head)
    }
    if (pathname === `${PREFIX}/_/launcher.js`) {
      if (launcher === null) return notFound(res, head)
      return send(res, 200, 'text/javascript; charset=utf-8', launcher, head)
    }

    if (pathname === `${PREFIX}/data/status.json`) {
      const ok = await githubNow(github)
      return send(res, 200, 'application/json; charset=utf-8', JSON.stringify({ github: ok }), head)
    }

    const parts = pathname.split('/')
    // ['', 'board', 'data', file]
    if (parts.length === 4 && parts[2] === 'data' && DATA_FILE.test(parts[3])) {
      let body
      try {
        body = await readFile(join(dataDir, parts[3]))
      } catch {
        return notFound(res, head)
      }
      return send(res, 200, 'text/javascript; charset=utf-8', body, head)
    }
    return notFound(res, head)
  }
}

// /board/api/running: only with a logged-in gh; owners are the board projects' owners
// (from the fetched data files, plus the org of every "org/N" in config.projects),
// so a site only ever asks about its own projects.
function runningFor(github, dataDir, projects = [], query = undefined) {
  let current = null, key = ''
  const orgs = projects.filter((k) => typeof k === 'string' && !isGitlabKey(k)).map((k) => splitKey(k).owner)
  return async () => {
    if (!(await githubNow(github))) return { at: new Date().toISOString(), items: [] }
    const owners = [...new Set([...ownersFromBoards(await readBoards(dataDir)).map((o) => o.toLowerCase()), ...orgs])].sort()
    if (owners.join(',') !== key) { key = owners.join(','); current = createRunning({ owners, query }) }
    return current()
  }
}

// Which projects to re-fetch after an action: the status one, or the boards showing that issue
// (all configured projects when no board shows it yet).
function refetchFor(projects, dataDir, fetcher) {
  return async (kind, body) => {
    const f = fetcher()
    if (typeof f?.fetchNow !== 'function') return
    let keys
    if (kind === 'status') keys = [projectKey(body.project)]
    else {
      const url = String(body.url).trim()
      keys = (await readBoards(dataDir)).filter((b) => b.issues?.some((x) => x.url === url)).map((b) => b.num)
      for (const k of projects) {
        if (typeof k !== 'string') continue
        const b = await readKeyedBoard(dataDir, k)
        if (b?.items?.some?.((x) => x?.u === url)) keys.push(k)
      }
      if (!keys.length) keys = projects
    }
    for (const k of keys) if (projects.includes(k)) f.fetchNow(k)
  }
}

// Owner (org or user login) of project num, from its fetched board file (window.PROJECTS[N].url).
function ownerFor(dataDir, org) {
  return async (num) => {
    const b = (await readBoards(dataDir)).find((x) => x.num === num)
    const m = /github\.com\/(?:orgs|users)\/([^/]+)\/projects\//.exec(b?.url ?? '')
    return m ? m[1] : org || null
  }
}

// thinkCwd default: $HOME (on the site /home/dsh/workspace), else DSH's default.
export const defaultThinkCwd = () => process.env.HOME || undefined

// fetch-project.py --one <key> <dataDir> once, waiting for it (「＋ 加入 project」).
function fetchOnceWith({ python, script, dataDir, env }) {
  return (key) => Promise.resolve(typeof env === 'function' ? env() : env).then((e) => new Promise((resolve, reject) => {
    execFile(python, [script, '--one', String(key), dataDir], { timeout: FETCH_ONE_TIMEOUT_MS, env: { ...process.env, ...e } }, (err, stdout, stderr) => {
      if (err) reject(new Error(String(stderr || err.message).trim().split('\n').pop()))
      else resolve(stdout)
    })
  }))
}

// hooks (tests only): { startFetcher, fetchOne, hasGitHub, ghLogin, relay, running, runningQuery, gh, hostname, installSkill, skillsDir }.
export function apply(ctx, config = {}, hooks = {}) {
  const logger = ctx.logger?.('swarm-board') ?? console
  const dataDir = config.dataDir || defaultDataDir()
  // config.projects: integers (default org), "org/number" strings (another org) and "gitlab:<path>"
  // (a GitLab project); as keys, without duplicates.
  // config.org: the default org (bare numbers in config.projects); fetch-project.py gets it as WOLFFIA_ORG.
  if (config.org) setDefaultOrg(config.org)
  const projects = [...new Set((Array.isArray(config.projects) ? config.projects : []).map(projectKey).filter((k) => k !== null))]
  // Projects added from the board (「＋ 加入 project」) live in projects.json, beside dataDir; they count too.
  const projectsFile = join(dirname(dataDir), 'projects.json')
  for (const x of readProjectsJson(projectsFile)?.projects ?? []) {
    const k = projectKey(x)
    if (k !== null && !projects.includes(k)) projects.push(k)
  }
  const fetchEveryMs = config.fetchEveryMs ?? FETCH_EVERY_MS
  const startFetcher = hooks.startFetcher ?? realStartFetcher
  // GitHub credential: the site's own gh login, else the platform relay key (credential.js).
  // hooks.hasGitHub (tests) replaces both: true means "gh works as is".
  const relay = hooks.relay !== undefined ? hooks.relay
    : hooks.hasGitHub ? null
    : createRelayCredential({ relayKey: () => readRelayKey(config.workspace || process.env.HOME), log: (m) => logger.warn?.(m) })
  const ghEnv = createGhEnv({ ghLogin: hooks.hasGitHub ?? hooks.ghLogin ?? ghLoggedIn, relay })
  const hasGitHub = async () => (await ghEnv()) !== null
  // Every gh the board runs gets the current credential env ({} = the site's own login).
  const envOrEmpty = async () => (await ghEnv()) ?? {}
  const gh = hooks.gh ?? (async (args, timeoutMs) => runGh(args, timeoutMs, await envOrEmpty()))
  const runningQuery = hooks.runningQuery ?? (async (query, vars) => ghGraphql(query, vars, undefined, await envOrEmpty()))
  // One gate for the whole board: status.json, actions, running, the fetcher and the digest.
  // Rechecked (at most every githubRecheckMs), so logging gh in later needs no DSH restart.
  let onGitHub = () => {}
  const github = githubGate(hasGitHub, {
    ttlMs: config.githubRecheckMs ?? GITHUB_RECHECK_MS,
    now: hooks.now,
    onChange: (ok) => onGitHub(ok),
  })
  const lifetime = new AbortController()
  ctx.effect(() => () => lifetime.abort(), 'swarm-board: lifetime')
  const store = createStore(config.sessionsFile || undefined)
  const suggestions = createSuggestionStore(config.suggestionsFile || undefined)
  // 新的「分析」對話開在工作區：thinkCwd，否則 $HOME，否則 DSH 預設。
  const cwd = config.thinkCwd || defaultThinkCwd()
  // DSH Workspace（側欄分組）只在 thinkWorkspace 明確給目錄時才建。
  const workspace = typeof config.thinkWorkspace !== 'string' || !config.thinkWorkspace || !ctx.typertGateway ? undefined
    : boardWorkspace({ gateway: ctx.typertGateway, dir: config.thinkWorkspace,
                       title: config.thinkWorkspaceTitle || '🌌 協作星系分析', signal: lifetime.signal, logger })

  // 站台層級的 epic skill → $DSH_HOME/skills/epic（不存在或和外掛版本不同時才寫）。
  // Under `node --test` only an explicit skillsDir installs, so tests never touch the real DSH home.
  const skillsDir = hooks.skillsDir ?? config.skillsDir ?? (process.env.NODE_TEST_CONTEXT ? null : defaultSkillsDir())
  if (skillsDir) {
    try {
      const done = (hooks.installSkill ?? realInstallSkill)({ name: 'epic', to: skillsDir })
      if (done !== 'unchanged') logger.info?.('swarm-board: epic skill %s at %s', done, join(skillsDir, 'epic'))
    } catch (error) {
      logger.warn?.('swarm-board: cannot install the epic skill into %s: %s', skillsDir, error?.message ?? String(error))
    }
  }
  let fetcher = null, fetcherStarted = false
  const python = config.python || 'python3'
  const fetchScript = config.fetchScript || DEFAULT_FETCH_SCRIPT
  // GitLab: config.gitlabUrl (default https://gitlab.com) and the token file, read on every use
  // (config.gitlabTokenFile, default <workspace>/.swarm/keys/shared/gitlab.token). The token only goes
  // into GitLab request headers (gitlab.js) and the fetch child's GITLAB_TOKEN; never into a response or log.
  const gitlabUrl = gitlabBase(config.gitlabUrl)
  const gitlabTokenFile = config.gitlabTokenFile || defaultTokenFile(config.workspace || process.env.HOME)
  const gitlabToken = () => readToken(gitlabTokenFile)
  const hasGitlab = () => projects.some(isGitlabKey)
  const gitlabEnv = () => {
    if (!hasGitlab()) return {}
    const token = gitlabToken()
    return { GITLAB_URL: gitlabUrl, ...(token ? { GITLAB_TOKEN: token } : {}) }
  }
  // fetch-project.py looks for projects.json beside itself unless told; ours sits above dataDir.
  const fetchEnv = async () => ({ WOLFFIA_PROJECTS: projectsFile, WOLFFIA_ORG: TOWN, ...gitlabEnv(), ...(await envOrEmpty()) })
  function startFetching() {
    if (fetcher || !fetcherStarted || !projects.length || !(fetchEveryMs > 0)) return
    try { mkdirSync(dataDir, { recursive: true }) } catch {}
    fetcher = startFetcher({
      python,
      script: fetchScript,
      projects, // the live list: a project added from the board joins the next round
      outDir: dataDir,
      intervalMs: fetchEveryMs,
      env: fetchEnv,
      log: (msg) => logger.warn?.('swarm-board: %s', msg),
    })
  }
  const picker = createProjectPicker({
    gh, projects, file: projectsFile, dataDir,
    fetchOne: hooks.fetchOne ?? ((key) => { try { mkdirSync(dataDir, { recursive: true }) } catch {}
      return fetchOnceWith({ python, script: fetchScript, dataDir, env: fetchEnv })(key) }),
    onAdd: () => startFetching(),
  })
  const act = createActions({
    gh, by: config.siteName || (hooks.hostname ?? hostname)(), projects,
    ownerOf: ownerFor(dataDir, config.org),
    columns: (key) => columnMap(readProjectsJson(projectsFile), key),
    gitlab: createGitlabMover({
      fetch: hooks.fetch ?? globalThis.fetch,
      base: () => gitlabUrl,
      token: gitlabToken,
      labels: (key) => labelsFor(readProjectsJson(projectsFile), key),
    }),
  })
  // Archived sessions: DSH runs none of their turns. Read live from the Host's workspace registry.
  const archived = (sessionId) => {
    try {
      const registry = ctx.get?.('workspaceRegistry') ?? ctx.workspaceRegistry
      return registry?.archivedSessionIds?.map(String).includes(sessionId) === true
    } catch { return false }
  }
  const handler = createHandler({
    html: readOnce(config.html || DEFAULT_HTML, logger),
    launcher: readOnce(LAUNCHER, logger),
    dataDir,
    gateway: ctx.typertGateway,
    store,
    suggestions,
    cwd,
    workspace,
    archived,
    signal: lifetime.signal,
    logger,
    github,
    running: hooks.running ?? runningFor(github, dataDir, projects, runningQuery),
    act,
    refetch: refetchFor(projects, dataDir, () => fetcher),
    keys: projects,
    picker,
  })

  // 抓看板資料: every fetchEveryMs (default 10 minutes; 0 = off) run fetch-project.py --one
  // for each of config.projects into dataDir. Needs a logged-in gh (or, with a GitLab project
  // on the board, a GitLab token); without it nothing runs and /board/data/status.json says
  // {"github": false}. When gh is logged in later, the next check (a page load asks
  // status.json; also every githubRecheckMs) starts the fetcher.
  ensureProjectsJson(dataDir, projects, logger)
  ctx.effect(() => {
    let stopped = false, warned = false
    onGitHub = (ok) => {
      if (stopped) return
      if (!ok) {
        if (!warned) logger.info?.('swarm-board: gh is not logged in (gh auth status failed); board fetcher not started')
        warned = true
        return
      }
      if (!fetcherStarted) logger.info?.('swarm-board: gh is logged in; board fetcher started')
      fetcherStarted = true
      startFetching()
    }
    // A GitLab token is enough for a board with GitLab projects (a file put there later counts too).
    const gitlabNow = () => {
      if (stopped || fetcherStarted || !hasGitlab() || !gitlabToken()) return
      logger.info?.('swarm-board: GitLab token found; board fetcher started')
      fetcherStarted = true
      startFetching()
    }
    github()
    gitlabNow()
    // While gh is not logged in, look again now and then so the fetcher starts without a page visit.
    const recheckMs = config.githubRecheckMs ?? GITHUB_RECHECK_MS
    const timer = recheckMs > 0 ? (hooks.setInterval ?? setInterval)(() => { if (!fetcherStarted) { github(); gitlabNow() } }, recheckMs) : null
    timer?.unref?.()
    return () => {
      stopped = true; fetcherStarted = false; fetcher?.stop(); fetcher = null
      if (timer) (hooks.clearInterval ?? clearInterval)(timer)
    }
  }, 'swarm-board: fetcher')

  // 每日聚合: digestEveryMs (default a day; 0 = off). The first check waits
  // digestDelayMs (default a minute) so DSH finishes starting first. Each round first
  // asks gh (`gh auth status`); without a logged-in gh that round is skipped.
  const everyMs = config.digestEveryMs ?? DAY
  if (ctx.typertGateway && everyMs > 0) {
    ctx.effect(() => {
      const digest = startDigest({
        think: replyingThink({
          gateway: ctx.typertGateway, store, signal: lifetime.signal,
          send: (q) => think({ ...q, gateway: ctx.typertGateway, store, cwd, workspace, archived, signal: lifetime.signal }),
        }),
        readBoards: () => readBoards(dataDir),
        github,
        store: suggestions,
        everyMs,
        delayMs: config.digestDelayMs ?? 60_000,
        log: logger,
      })
      return () => digest.stop()
    }, 'swarm-board: digest')
  }

  ctx.effect(() => ctx.webServer.register({
    kind: 'prefix',
    path: PREFIX,
    handler: async (req, res) => {
      try {
        await handler(req, res)
      } catch (error) {
        logger.error?.('board route failed: %s', error?.stack ?? String(error))
        if (!res.headersSent) send(res, 500, 'text/plain; charset=utf-8', 'internal error')
        else res.destroy()
      }
    },
  }), 'swarm-board: /board')
}
