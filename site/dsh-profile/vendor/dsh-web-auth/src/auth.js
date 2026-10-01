import { createHash, randomBytes, scryptSync, timingSafeEqual } from 'node:crypto'
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

const SCRYPT_KEY_LENGTH = 64
const DEFAULT_SCRYPT_N = 16_384
const DEFAULT_SCRYPT_R = 8
const DEFAULT_SCRYPT_P = 1

function safeEqual(left, right) {
  const a = Buffer.from(left)
  const b = Buffer.from(right)
  const length = Math.max(a.length, b.length, 1)
  const paddedA = Buffer.alloc(length)
  const paddedB = Buffer.alloc(length)
  a.copy(paddedA)
  b.copy(paddedB)
  return timingSafeEqual(paddedA, paddedB) && a.length === b.length
}

export function hashPassword(password, options = {}) {
  if (typeof password !== 'string' || password.length < 8) {
    throw new Error('Password must contain at least 8 characters.')
  }
  const N = options.N ?? DEFAULT_SCRYPT_N
  const r = options.r ?? DEFAULT_SCRYPT_R
  const p = options.p ?? DEFAULT_SCRYPT_P
  const salt = options.salt ?? randomBytes(16)
  const key = scryptSync(password, salt, SCRYPT_KEY_LENGTH, {
    N,
    r,
    p,
    maxmem: Math.max(64 * 1024 * 1024, 256 * N * r),
  })
  return ['scrypt', N, r, p, Buffer.from(salt).toString('base64url'), key.toString('base64url')].join('$')
}

export function verifyPassword(password, encoded) {
  if (typeof password !== 'string' || typeof encoded !== 'string') return false
  const parts = encoded.split('$')
  if (parts.length !== 6 || parts[0] !== 'scrypt') return false
  const N = Number(parts[1])
  const r = Number(parts[2])
  const p = Number(parts[3])
  if (!Number.isSafeInteger(N) || !Number.isSafeInteger(r) || !Number.isSafeInteger(p)) return false
  if (N < 2 || N > 1_048_576 || r < 1 || r > 64 || p < 1 || p > 16) return false
  try {
    const salt = Buffer.from(parts[4], 'base64url')
    const expected = Buffer.from(parts[5], 'base64url')
    if (salt.length < 16 || expected.length !== SCRYPT_KEY_LENGTH) return false
    const actual = scryptSync(password, salt, expected.length, {
      N,
      r,
      p,
      maxmem: Math.max(64 * 1024 * 1024, 256 * N * r),
    })
    return timingSafeEqual(actual, expected)
  } catch {
    return false
  }
}

export function verifyPlainPassword(password, expected) {
  if (typeof password !== 'string' || typeof expected !== 'string') return false
  return safeEqual(createHash('sha256').update(password).digest(), createHash('sha256').update(expected).digest())
}

export function parseCookies(header) {
  const cookies = new Map()
  if (!header) return cookies
  for (const item of header.split(';')) {
    const separator = item.indexOf('=')
    if (separator <= 0) continue
    const name = item.slice(0, separator).trim()
    const value = item.slice(separator + 1).trim()
    try {
      cookies.set(name, decodeURIComponent(value))
    } catch {
      // Ignore malformed cookie values without rejecting the whole request.
    }
  }
  return cookies
}

export function sanitizeReturnPath(value) {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return '/'
  if (value.includes('\r') || value.includes('\n') || value.includes('\0')) return '/'
  // Browsers read a backslash as a slash in URLs: "/\\evil" and "/\\/evil"
  // would resolve to another origin. Also refuse tab (stripped by URL parsers).
  if (value.includes('\\') || value.includes('\t')) return '/'
  return value
}

export class SessionStore {
  // Sessions live in memory, but this deployment restarts the dsh process
  // several times a day (unattended `Restart=always` plus operator restarts).
  // A purely in-memory Map meant every restart silently logged the operator
  // out and discarded the configured sessionTtlMinutes. When `persistPath` is
  // supplied the map is mirrored to disk so a restart keeps valid sessions.
  // Persistence is strictly best-effort: any I/O failure degrades to the
  // original in-memory behaviour rather than breaking authentication.
  constructor(ttlMs, now = () => Date.now(), maxEntries = 10_000, persistPath = undefined) {
    this.ttlMs = ttlMs
    this.now = now
    this.maxEntries = maxEntries
    this.persistPath = persistPath
    this.sessions = new Map()
    this.load()
  }

  load() {
    if (!this.persistPath) return
    let raw
    try {
      raw = readFileSync(this.persistPath, 'utf8')
    } catch {
      return // absent or unreadable: start empty, exactly like before.
    }
    let parsed
    try {
      parsed = JSON.parse(raw)
    } catch {
      return // corrupt file must never block login; start empty.
    }
    if (!Array.isArray(parsed?.sessions)) return
    const now = this.now()
    for (const entry of parsed.sessions) {
      // Re-validate every field: a hand-edited or truncated file must not be
      // able to inject a non-expiring or malformed session.
      if (typeof entry?.token !== 'string') continue
      if (typeof entry.username !== 'string') continue
      if (typeof entry.expiresAt !== 'number' || !Number.isFinite(entry.expiresAt)) continue
      if (entry.expiresAt <= now) continue
      if (this.sessions.size >= this.maxEntries) break
      this.sessions.set(entry.token, { username: entry.username, expiresAt: entry.expiresAt })
    }
  }

  save() {
    if (!this.persistPath) return
    try {
      mkdirSync(dirname(this.persistPath), { recursive: true })
      const sessions = []
      for (const [token, session] of this.sessions) {
        sessions.push({ token, username: session.username, expiresAt: session.expiresAt })
      }
      // Write-then-rename so a crash mid-write cannot leave a partial file
      // that would drop every session on the next boot. Mode 0600: these
      // tokens are password-equivalent.
      const temporary = `${this.persistPath}.tmp`
      writeFileSync(temporary, JSON.stringify({ version: 1, sessions }), { mode: 0o600 })
      renameSync(temporary, this.persistPath)
    } catch {
      /* best-effort: keep serving from memory */
    }
  }

  create(username) {
    const now = this.now()
    this.prune(now)
    if (this.sessions.size >= this.maxEntries) this.sessions.delete(this.sessions.keys().next().value)
    const token = randomBytes(32).toString('base64url')
    this.sessions.set(token, { username, expiresAt: now + this.ttlMs })
    this.save()
    return token
  }

  get(token) {
    if (!token) return undefined
    const now = this.now()
    this.prune(now)
    const session = this.sessions.get(token)
    if (!session) return undefined
    const previous = session.expiresAt
    session.expiresAt = now + this.ttlMs
    // Sliding expiry would otherwise rewrite the file on every request. Only
    // persist once the renewal has moved the deadline by a meaningful amount
    // (1% of the TTL), which bounds writes without losing the extension.
    if (session.expiresAt - previous >= this.ttlMs / 100) this.save()
    return session
  }

  delete(token) {
    if (!token) return
    if (this.sessions.delete(token)) this.save()
  }

  prune(now = this.now()) {
    for (const [token, session] of this.sessions) {
      if (session.expiresAt <= now) this.sessions.delete(token)
    }
  }
}

export class AttemptLimiter {
  constructor(maxAttempts, windowMs, now = () => Date.now(), maxEntries = 10_000) {
    this.maxAttempts = maxAttempts
    this.windowMs = windowMs
    this.now = now
    this.maxEntries = maxEntries
    this.attempts = new Map()
  }

  check(key) {
    const current = this.attempts.get(key)
    const now = this.now()
    if (!current) return { allowed: true, retryAfterSeconds: 0 }
    if (current.resetAt <= now) {
      this.attempts.delete(key)
      return { allowed: true, retryAfterSeconds: 0 }
    }
    if (current.count < this.maxAttempts) return { allowed: true, retryAfterSeconds: 0 }
    return { allowed: false, retryAfterSeconds: Math.max(1, Math.ceil((current.resetAt - now) / 1000)) }
  }

  fail(key) {
    const now = this.now()
    const current = this.attempts.get(key)
    if (!current || current.resetAt <= now) {
      this.prune(now)
      if (this.attempts.size >= this.maxEntries) this.attempts.delete(this.attempts.keys().next().value)
      this.attempts.set(key, { count: 1, resetAt: now + this.windowMs })
    } else {
      current.count += 1
    }
  }

  clear(key) {
    this.attempts.delete(key)
  }

  prune(now = this.now()) {
    for (const [key, attempt] of this.attempts) {
      if (attempt.resetAt <= now) this.attempts.delete(key)
    }
  }
}
