// GitLab projects on the board (EPIC #42): key, data file, labels, token, and 改狀態 written back.
//   key        "gitlab:<full path>" (e.g. "gitlab:acme/sub/app"); the path is case-insensitive, kept lowercase
//   data file  g-<first 12 hex of sha1(path)>.js, window.PROJECTS["gitlab:<path>"] (fetch-project.py writes it)
//   labels     projects.json {"gitlabLabels": {"gitlab:<path>": {"progress": "In Progress", "review": "Review"}}};
//              unset -> 進行中 / 審核後請關閉; matched case-insensitively
//   token      read from a file on every use (default <workspace>/.swarm/keys/shared/gitlab.token); it only ever
//              goes into the PRIVATE-TOKEN header of requests to GitLab and the fetch child's GITLAB_TOKEN
//   columns    完成 = closed; 進行中 = progress label; 審核後請關閉 = review label; 待辦 = open with neither
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ActionError } from './actions.js'

export const PREFIX = 'gitlab:'
export const DEFAULT_URL = 'https://gitlab.com'
export const DEFAULT_LABELS = Object.freeze({ progress: '進行中', review: '審核後請關閉' })
const SEGMENT = /^[a-z0-9_][a-z0-9_.-]{0,254}$/
const MAX_PATH = 1024

// "gitlab:Acme/Sub/App" -> "gitlab:acme/sub/app"; null unless every segment is a plain GitLab path
// segment (no empty, ".", ".." or anything else that could leave the project), at least namespace/project.
export function gitlabKey(x) {
  if (typeof x !== 'string') return null
  const s = x.trim()
  if (s.slice(0, PREFIX.length).toLowerCase() !== PREFIX) return null
  const path = s.slice(PREFIX.length).toLowerCase()
  if (!path || path.length > MAX_PATH) return null
  const parts = path.split('/')
  if (parts.length < 2 || !parts.every((p) => SEGMENT.test(p))) return null
  return PREFIX + path
}

export const isGitlabKey = (key) => typeof key === 'string' && key.startsWith(PREFIX)
export const gitlabPath = (key) => key.slice(PREFIX.length)
export const gitlabDataFile = (key) => `g-${createHash('sha1').update(gitlabPath(key)).digest('hex').slice(0, 12)}.js`

// config.gitlabUrl -> "https://host[:port][/prefix]" without a trailing slash; the default for anything else.
export function gitlabBase(url) {
  if (typeof url !== 'string' || !url.trim()) return DEFAULT_URL
  let u
  try { u = new URL(url.trim()) } catch { return DEFAULT_URL }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return DEFAULT_URL
  if (u.username || u.password || u.search || u.hash) return DEFAULT_URL
  return (u.origin + u.pathname).replace(/\/+$/, '')
}

export const defaultTokenFile = (workspace) => join(workspace || process.env.HOME || '.', '.swarm', 'keys', 'shared', 'gitlab.token')

// The token now (a file put there after DSH started counts); null when there is none.
export function readToken(file) {
  try {
    const t = readFileSync(file, 'utf8').trim()
    return t && !/\s/.test(t) ? t : null
  } catch {
    return null
  }
}

// projects.json (already parsed, or null) -> the status label names for key.
export function labelsFor(conf, key) {
  const all = conf?.gitlabLabels
  let got
  if (all && typeof all === 'object' && !Array.isArray(all)) {
    for (const [k, v] of Object.entries(all)) if (gitlabKey(k) === key) got = v
  }
  const pick = (name) => (typeof got?.[name] === 'string' && got[name].trim() ? got[name].trim() : DEFAULT_LABELS[name])
  return { progress: pick('progress'), review: pick('review') }
}

// A board column name (page or English alias) -> 'todo' | 'progress' | 'review' | 'done'; null otherwise.
const COLUMNS = {
  '待辦': 'todo', 'todo': 'todo',
  '進行中 (7 天一輪)': 'progress', '進行中': 'progress', 'in progress': 'progress',
  '審核後請關閉': 'review', 'review': 'review',
  '完成': 'done', 'done': 'done',
}
export const columnOf = (status) => (typeof status === 'string' ? COLUMNS[status.trim().toLowerCase()] ?? null : null)

// Never let the token reach a message (responses and logs carry error messages).
const scrub = (text, token) => (token ? String(text).split(token).join('***') : String(text))

// fetch: WHATWG fetch. base: () => GitLab base URL. token: () => token or null. labels: (key) => {progress, review}.
// Returns move(key, url, status) -> 'OPEN' | 'CLOSED' (the issue's state afterwards), like the GitHub one.
export function createGitlabMover({ fetch = globalThis.fetch, base = () => DEFAULT_URL, token = () => null, labels = () => DEFAULT_LABELS }) {
  async function api(tok, method, path, body) {
    let res
    try {
      res = await fetch(`${base()}/api/v4${path}`, {
        method,
        headers: { 'PRIVATE-TOKEN': tok, accept: 'application/json', ...(body ? { 'content-type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(30_000),
      })
    } catch (error) {
      throw new Error(scrub(`連不到 GitLab：${error?.cause?.code ?? error?.message ?? error}`, tok).slice(0, 300))
    }
    const text = await res.text().catch(() => '')
    if (res.status === 404) throw new ActionError('GitLab 找不到這張 issue（或 token 看不到這個專案）', 404)
    if (res.status === 401 || res.status === 403) throw new ActionError(`GitLab 拒絕這個 token（${res.status}）`, 409)
    if (!res.ok) {
      let why = ''
      try { const j = JSON.parse(text); why = typeof j?.message === 'string' ? j.message : typeof j?.error === 'string' ? j.error : JSON.stringify(j?.message ?? '') } catch {}
      throw new Error(scrub(`GitLab ${res.status}${why ? `：${why}` : ''}`, tok).slice(0, 300))
    }
    try { return JSON.parse(text) } catch { throw new Error('GitLab 的回應不是 JSON') }
  }

  // Only an issue of this very project: <base>/<path>/-/issues/<iid>, or /-/work_items/<iid> (newer GitLab
  // gives issues that web_url; found on gitlab.com: every issue of a real project came back as work_items).
  function iidOf(key, url) {
    const u = typeof url === 'string' ? url.trim() : ''
    const prefix = `${base()}/`.toLowerCase()
    if (!u.toLowerCase().startsWith(prefix)) throw new ActionError('不是這個 GitLab 專案的 issue 網址')
    const m = /^(.+)\/-\/(?:issues|work_items)\/(\d{1,9})$/.exec(u.slice(prefix.length))
    if (!m || m[1].toLowerCase() !== gitlabPath(key) || !(Number(m[2]) > 0)) throw new ActionError('不是這個 GitLab 專案的 issue 網址')
    return Number(m[2])
  }

  return async function move(key, url, status) {
    const col = columnOf(status)
    if (!col) throw new ActionError(`GitLab 專案沒有「${status}」欄`)
    const iid = iidOf(key, url)
    const tok = token()
    if (!tok) throw new ActionError('尚未連接 GitLab：站台還沒有 GitLab token', 409)
    const where = `/projects/${encodeURIComponent(gitlabPath(key))}/issues/${iid}`
    const issue = await api(tok, 'GET', where)
    const have = (issue?.labels ?? []).map((l) => (typeof l === 'string' ? l : l?.name)).filter((l) => typeof l === 'string')
    const names = labels(key)
    const on = (name) => have.find((l) => l.toLowerCase() === name.toLowerCase())
    const want = { progress: col === 'progress', review: col === 'review' }
    const add = [], remove = []
    for (const k of ['progress', 'review']) {
      if (want[k] && !on(names[k])) add.push(names[k])
      if (!want[k] && on(names[k])) remove.push(on(names[k]))
    }
    const closed = issue?.state === 'closed'
    const stateEvent = col === 'done' ? (closed ? null : 'close') : (closed ? 'reopen' : null)
    if (!add.length && !remove.length && !stateEvent) return closed ? 'CLOSED' : 'OPEN'
    const body = {}
    if (add.length) body.add_labels = add.join(',')
    if (remove.length) body.remove_labels = remove.join(',')
    if (stateEvent) body.state_event = stateEvent
    const out = await api(tok, 'PUT', where, body)
    return (out?.state ?? (stateEvent === 'close' ? 'closed' : stateEvent === 'reopen' ? 'opened' : issue?.state)) === 'closed' ? 'CLOSED' : 'OPEN'
  }
}
