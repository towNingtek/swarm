// Which GitHub credential the board's gh runs with.
//
//   1. The site's own gh login (`gh auth login`): used as is, nothing changes.
//   2. Otherwise the platform relay key (.swarm/keys/shared/github-relay.token, "scs_…"):
//      it is exchanged at the platform relay for a short-lived GitHub App installation
//      token, which is handed to gh as GH_TOKEN in the child's environment only.
//      The token is never written to disk, logged or returned to the page.
//
// The relay narrows the token to the site's repositories and permissions; the board
// needs organization_projects for Projects, issues for labels. When the platform did
// not grant Projects the board shows the same「尚未連接 GitHub」as without gh.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

export const RELAY_URL = 'http://172.17.0.1:8212/scm/v1/github/token'
export const RELAY_KEY_FILE = join('.swarm', 'keys', 'shared', 'github-relay.token')
const MARGIN_MS = 5 * 60_000      // refresh a token five minutes before it expires
const FAIL_BACKOFF_MS = 60_000    // after a refused/failed exchange, wait before asking again
const KEY_RE = /^scs_[A-Za-z0-9_-]{20,100}$/

// workspace: the site workspace (where .swarm/ lives). Missing or malformed key -> null.
export function readRelayKey(workspace) {
  if (!workspace) return null
  try {
    const key = readFileSync(join(workspace, RELAY_KEY_FILE), 'utf8').trim()
    return KEY_RE.test(key) ? key : null
  } catch {
    return null
  }
}

// relayKey: () => key or null (read each time: a key put in later counts).
// fetch: the global fetch (a hook for tests). now: clock (tests).
export function createRelayCredential({ relayKey, url = RELAY_URL, fetch = globalThis.fetch, now = Date.now, log = () => {} } = {}) {
  let token = null, expires = 0, failedAt = -Infinity, inflight = null, lastError = ''

  async function mint(key) {
    let response
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
        body: '',
        signal: AbortSignal.timeout(30_000),
      })
    } catch (error) {
      throw new Error(`relay unreachable (${error?.name === 'TimeoutError' ? 'timeout' : error?.code || 'network'})`)
    }
    let data = {}
    try { data = await response.json() } catch {}
    if (!response.ok) throw new Error(`relay refused: ${String(data?.error?.code || data?.error?.message || `HTTP ${response.status}`).slice(0, 80)}`)
    const exp = Date.parse(data?.expires_at)
    // Installation tokens are "ghs_" + an opaque body: long, and since 2026 JWT-like
    // (letters, digits, "_", "." and "-"). Anything else (spaces, newlines, quotes) is refused.
    if (typeof data?.token !== 'string' || !/^ghs_[A-Za-z0-9_.-]{20,2048}$/.test(data.token) || !Number.isFinite(exp)) {
      throw new Error('relay answered something unexpected')
    }
    return { token: data.token, expires: exp, permissions: data.permissions ?? {} }
  }

  // A valid token or null. Never throws.
  async function get() {
    if (token && expires - now() > MARGIN_MS) return token
    if (inflight) return inflight
    if (now() - failedAt < FAIL_BACKOFF_MS) return null
    const key = relayKey()
    if (!key) return null
    inflight = mint(key).then((got) => {
      // The board reads Projects; without that permission this token cannot run it.
      if (!got.permissions.organization_projects) {
        throw new Error('relay token has no organization_projects permission (ask the platform to grant Projects)')
      }
      token = got.token; expires = got.expires; lastError = ''
      return token
    }, (error) => { throw error }).catch((error) => {
      token = null; failedAt = now()
      const msg = String(error?.message ?? error)
      if (msg !== lastError) log(`swarm-board: GitHub relay: ${msg}`)
      lastError = msg
      return null
    }).finally(() => { inflight = null })
    return inflight
  }

  return { get, has: () => Boolean(relayKey()) }
}

// The env every board gh (and fetch-project.py, which runs gh) gets: {} when the site's
// own gh login is used, { GH_TOKEN } when the relay supplies the credential.
// ghLogin: async () => boolean, the site's own `gh auth status` (with no GH_TOKEN set).
export function createGhEnv({ ghLogin, relay }) {
  return async function ghEnv() {
    if (await ghLogin()) return {}
    const token = relay ? await relay.get() : null
    return token ? { GH_TOKEN: token } : null // null: no credential at all
  }
}
