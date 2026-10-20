// GET /board/api/running inside a site: which issues have `swarm:running` right now
// (the bees), with the engine and start time from the last "🐝 開工" comment.
// Same response shape as Wolffia's /api/running: { at, items: [{repo, number, title, engine, since, epic}] }.
// Uses the site's own gh. One GitHub query per TTL (30 s) whoever asks; a failed query
// serves the last good answer, or { items: [] } with an error — the board keeps working.
import { execFile } from 'node:child_process'

export const RUNNING_TTL_MS = 30_000
const LABEL = 'swarm:running'
const KICKOFF = /🐝\s*開工\s*[：:]\s*引擎\s*`?([^\s`，,]+)`?\s*[，,]\s*(\S+)/

const QUERY = 'query($q:String!){search(query:$q,type:ISSUE,first:100){nodes{... on Issue{number title ' +
  'repository{nameWithOwner} parent{number} comments(last:50){nodes{body}}}}}}'

function iso(s) {
  return s && !Number.isNaN(Date.parse(s)) ? s : null
}

// (engine, since) from the last 🐝 開工 comment; nulls when there is none or it does not parse.
export function kickoff(comments) {
  for (const c of [...(comments ?? [])].reverse()) {
    const body = c?.body ?? ''
    if (body.includes('開工') && body.includes('🐝')) {
      const m = KICKOFF.exec(body)
      return m ? { engine: m[1], since: iso(m[2].replace(/[。.]+$/, '')) } : { engine: null, since: null }
    }
  }
  return { engine: null, since: null }
}

export function parseRunning(data) {
  const items = []
  for (const n of data?.search?.nodes ?? []) {
    if (!n?.number || !n.repository?.nameWithOwner) continue
    const { engine, since } = kickoff(n.comments?.nodes)
    items.push({ repo: n.repository.nameWithOwner, number: n.number, title: n.title ?? '',
                 engine, since, epic: n.parent?.number ?? null })
  }
  return items
}

function ghGraphql(query, vars, timeoutMs = 30_000) {
  const args = ['api', 'graphql', '-f', `query=${query}`]
  for (const [k, v] of Object.entries(vars)) args.push('-f', `${k}=${v}`)
  return new Promise((resolve, reject) => {
    execFile('gh', args, { timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) return reject(new Error(String(stderr || err.message).trim().split('\n').pop()))
      try { resolve(JSON.parse(stdout).data) } catch (e) { reject(e) }
    })
  })
}

// owners: GitHub orgs/users to search (the owners of the board's projects). Empty = no query.
// hooks.query (tests): async (query, vars) => data.
export function createRunning({ owners = [], ttlMs = RUNNING_TTL_MS, now = Date.now, query = ghGraphql } = {}) {
  let cache = null // { at: ms, body }
  let inflight = null
  const search = () => owners.map((o) => `${/^[A-Za-z0-9-]+$/.test(o) ? 'org' : 'user'}:${o}`).join(' ') +
    ` is:issue is:open label:${LABEL}`

  async function refresh() {
    const at = new Date(now()).toISOString()
    if (!owners.length) return { at, items: [] }
    try {
      const data = await query(QUERY, { q: search() })
      return { at, items: parseRunning(data) }
    } catch (error) {
      if (cache?.body && !cache.body.error) return { ...cache.body, stale: true }
      return { at, items: [], error: String(error?.message ?? error).slice(0, 200) }
    }
  }

  return async function running() {
    if (cache && now() - cache.at < ttlMs) return cache.body
    if (!inflight) {
      inflight = refresh().then((body) => { cache = { at: now(), body }; return body })
        .finally(() => { inflight = null })
    }
    return inflight
  }
}

// Owners of the board's projects, from the fetched data files (window.PROJECTS[N].url
// is https://github.com/orgs/<owner>/projects/<N> or /users/<owner>/...).
export function ownersFromBoards(boards) {
  const owners = new Set()
  for (const b of boards ?? []) {
    const m = /github\.com\/(?:orgs|users)\/([^/]+)\/projects\//.exec(b?.url ?? '')
    if (m) owners.add(m[1])
  }
  return [...owners]
}
