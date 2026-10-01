import { createHmac, timingSafeEqual } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { Service } from '@deepseek-ai/cordis'
import Schema from '@deepseek-ai/schemastery'
import { renderIndexInjections } from '@deepseek-ai/dsh-host-webserver'
import {
  AttemptLimiter,
  SessionStore,
  parseCookies,
  sanitizeReturnPath,
  verifyPassword,
  verifyPlainPassword,
} from './auth.js'

export const name = 'dsh-web-auth'

export const Config = Schema.object({
  // Any bind address is allowed: a concrete LAN IPv4/IPv6, a host name, or the
  // loopback/all-interfaces literals. Authentication is forced for every
  // non-loopback bind (see authRequired below), so opening a specific network
  // address still cannot reach the control surface without a login.
  host: Schema.string().default('127.0.0.1'),
  port: Schema.number().min(0).max(65_535).default(3080),
  authMode: Schema.union(['always', 'non-loopback']).default('always'),
  username: Schema.string().default('admin'),
  password: Schema.string(),
  passwordHash: Schema.string(),
  sessionTtlMinutes: Schema.number().min(1).max(43_200).default(720),
  maxAttempts: Schema.number().min(1).max(1_000).default(5),
  attemptWindowSeconds: Schema.number().min(1).max(86_400).default(300),
  secureCookie: Schema.union(['auto', 'always', 'never']).default('auto'),
  trustProxy: Schema.boolean().default(false),
  // Shared secret the controlling platform uses to sign single-use entry
  // tickets. Deliberately NOT the password hash: a ticket signer must never be
  // able to derive or verify the site password. Empty disables the feature.
  entrySecret: Schema.string().default(''),
  // Path prefixes served WITHOUT a login, e.g. ['/share'] for dsh-share-room's
  // guest pages, which authenticate their own visitors. Only a plugin route
  // registered under one of these prefixes is reachable this way; /, /api,
  // /auth and the GUI fallback always stay behind the login. Empty (default)
  // keeps every path gated.
  publicPrefixes: Schema.array(Schema.string()).default([]),
})

const RESERVED_PUBLIC = new Set(['/api', '/auth', '/assets', '/plugins', '/static'])

/** Normalize and vet `publicPrefixes`; anything unsafe is refused loudly. */
export function publicPrefixesOf(list) {
  const out = []
  for (const raw of list ?? []) {
    const prefix = String(raw).replace(/\/+$/, '')
    if (!/^\/[a-z0-9][a-z0-9_-]*$/i.test(prefix)) throw new Error(`publicPrefixes: "${raw}" must be one path segment like /share`)
    if (RESERVED_PUBLIC.has(prefix.toLowerCase())) throw new Error(`publicPrefixes: "${raw}" is reserved and must stay behind the login`)
    out.push(prefix)
  }
  return out
}

/** A request path is public only when it is already canonical (no dot segments, no doubled slashes). */
function canonicalPath(pathname) {
  if (pathname.includes('\\')) return false
  const segs = pathname.split('/')
  return segs.every((seg, i) => {
    if (seg === '.' || seg === '..') return false
    if (seg === '') return i === 0 || i === segs.length - 1
    return true
  })
}

/** Login cookies: this gate's own, and DSH's native browser session cookie. */
function isLoginCookie(name) {
  return name === COOKIE_NAME || name.startsWith('dsh-auth-')
}

/**
 * A public route never sees the visitor's login cookies and can never set them
 * (no reading, no cookie tossing): only its own cookies pass either way.
 */
export function stripLoginCookies(req, res) {
  const header = req.headers.cookie
  if (typeof header === 'string') {
    const kept = header.split(';').filter((part) => {
      const name = part.split('=', 1)[0].trim()
      return name !== '' && !isLoginCookie(name)
    })
    if (kept.length > 0) req.headers.cookie = kept.map((part) => part.trim()).join('; ')
    else delete req.headers.cookie
  }
  const setHeader = res.setHeader.bind(res)
  const safe = (value) => {
    const list = (Array.isArray(value) ? value : [value]).filter((c) => !isLoginCookie(String(c).split('=', 1)[0].trim()))
    return Array.isArray(value) ? list : list[0]
  }
  res.setHeader = (name, value) => {
    if (String(name).toLowerCase() !== 'set-cookie') return setHeader(name, value)
    const filtered = safe(value)
    if (filtered === undefined || (Array.isArray(filtered) && filtered.length === 0)) return res
    return setHeader(name, filtered)
  }
  const writeHead = res.writeHead.bind(res)
  res.writeHead = (status, ...rest) => {
    const headers = rest.find((x) => x !== null && typeof x === 'object')
    if (headers && !Array.isArray(headers)) {
      for (const key of Object.keys(headers)) {
        if (key.toLowerCase() !== 'set-cookie') continue
        const filtered = safe(headers[key])
        if (filtered === undefined || (Array.isArray(filtered) && filtered.length === 0)) delete headers[key]
        else headers[key] = filtered
      }
    }
    return writeHead(status, ...rest)
  }
}

// Where the persisted session map lives. WEB_AUTH_SESSION_STORE takes an
// explicit path, or the literal 'off' to restore the original in-memory-only
// behaviour. Otherwise it sits beside the plugin's own state under DSH_HOME.
function resolveSessionStorePath() {
  const configured = process.env.WEB_AUTH_SESSION_STORE
  if (configured === 'off') return undefined
  if (configured) return configured
  const home = process.env.DSH_HOME
  if (!home) return undefined
  return `${home}/plugins/web-auth/sessions.json`
}

const COOKIE_NAME = 'dsh_web_auth'
const AUTH_PREFIX = '/auth'
const MAX_LOGIN_BODY_BYTES = 16 * 1024
// Swarm deployment branding (the upstream DeepSeek mark is not shown).
const BRAND_NAME = 'Swarm'
const BRAND_FAVICON = readFileSync(new URL('./assets/swarm-favicon.svg', import.meta.url), 'utf8')
const BRAND_MANIFEST = JSON.stringify({
  name: BRAND_NAME, short_name: BRAND_NAME, start_url: '/', scope: '/', display: 'fullscreen',
  icons: [{ src: '/auth/favicon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }],
})
const AUTH_BOOTSTRAP_SCRIPT = `(() => {
  // The Web client writes its product name into document.title; show ours.
  const titleDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, 'title')
  if (titleDescriptor && titleDescriptor.set) {
    Object.defineProperty(document, 'title', {
      configurable: true,
      get: () => titleDescriptor.get.call(document),
      set: (value) => titleDescriptor.set.call(document, String(value).replaceAll('DeepSeek Harness', ${JSON.stringify(BRAND_NAME)})),
    })
  }
  const nativeFetch = globalThis.fetch.bind(globalThis)
  let redirecting = false
  globalThis.fetch = async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), location.href)
    const sameOrigin = url.origin === location.origin
    const response = await nativeFetch(input, sameOrigin ? { ...init, credentials: 'same-origin' } : init)
    if (sameOrigin && response.status === 401 && !redirecting) {
      void response.clone().json().then((body) => {
        if (body?.error !== 'authentication_required' || redirecting) return
        redirecting = true
        const next = location.pathname + location.search + location.hash
        location.replace('/auth/login?next=' + encodeURIComponent(next))
      }).catch(() => {})
    }
    return response
  }
})()`

function injectAuthBootstrap(html) {
  return html.replace('<head>', '<head>\n<script src="/auth/bootstrap.js"></script>')
}

// The static index ships the upstream title, icons and manifest; point them at ours.
function rebrandIndex(html) {
  return html
    .replace(/<title>[^<]*<\/title>/, `<title>${BRAND_NAME}</title>`)
    .replace(/<link rel="icon"[^>]*>\s*/g, '')
    .replace(/<link rel="manifest"[^>]*>/, '<link rel="manifest" href="/auth/manifest.webmanifest">\n    <link rel="icon" type="image/svg+xml" href="/auth/favicon.svg">')
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function loginPage(next, message = '') {
  const safeNext = escapeHtml(sanitizeReturnPath(next))
  const feedback = message ? `<p class="error" role="alert">${escapeHtml(message)}</p>` : ''
  return `<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="icon" type="image/svg+xml" href="/auth/favicon.svg">
  <meta name="color-scheme" content="light dark">
  <title>登入 | Swarm</title>
  <style>
    :root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#1f2329;background:#f7f9fc}
    *{box-sizing:border-box}body{margin:0;min-height:100svh;display:grid;place-items:center;padding:24px;background:#f7f9fc}
    main{width:min(100%,400px);background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:34px 34px 30px;box-shadow:0 12px 30px rgba(31,35,41,.08);animation:rise .35s ease-out both}
    .brand{display:flex;align-items:center;gap:10px;color:#1f2329;font-size:18px;font-weight:700;letter-spacing:0}
    .mark{width:34px;height:34px;display:grid;place-items:center;border-radius:10px;background:#f0f3f8;box-shadow:0 4px 10px rgba(31,35,41,.1);overflow:hidden}.mark img{display:block;width:100%;height:100%;object-fit:contain}
    h1{font-size:24px;line-height:1.25;margin:28px 0 7px;letter-spacing:0;color:#1f2329}p{margin:0 0 24px;color:#697386;font-size:14px;line-height:1.5}
    label{display:block;font-size:13px;font-weight:650;margin:16px 0 7px;color:#374151}input{width:100%;height:44px;border:1px solid #d7dce5;border-radius:7px;padding:0 12px;font:inherit;background:#fff;color:#1f2329;outline:none;transition:border-color .18s ease,box-shadow .18s ease}input:focus{border-color:#e0a100;box-shadow:0 0 0 3px rgba(224,161,0,.14)}
    button{width:100%;height:44px;margin-top:22px;border:0;border-radius:7px;background:#f5b400;color:#1a1200;font:inherit;font-weight:700;cursor:pointer;transition:background .18s ease,transform .18s ease,box-shadow .18s ease;box-shadow:0 4px 10px rgba(224,161,0,.18)}button:hover{background:#c78f00;box-shadow:0 6px 14px rgba(224,161,0,.24)}button:active{transform:translateY(1px)}.error{margin:0 0 14px;padding:10px 12px;border-left:3px solid #d14343;background:#fff5f5;color:#a12d2d;border-radius:5px;font-size:13px}
    footer{margin-top:22px;color:#9aa3b2;font-size:12px;text-align:center}@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}@media(prefers-reduced-motion:reduce){main,input,button{animation:none;transition:none}}
  </style>
</head>
<body>
  <main>
    <div class="brand"><div class="mark" aria-hidden="true"><img src="/auth/favicon.svg" alt=""></div><span>SWARM</span></div>
    <h1>登入你的 AI 辦公室</h1>
    <p>請輸入站台帳號密碼。從平台設定頁按「進入我的站台」可免密碼進入。</p>
    ${feedback}
    <form method="post" action="/auth/login">
      <input type="hidden" name="next" value="${safeNext}">
      <label for="username">帳號</label>
      <input id="username" name="username" autocomplete="username" required autofocus>
      <label for="password">密碼</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">登入</button>
    </form>
  </main>
</body>
</html>`
}

// connectSelf is granted ONLY to the handoff page, which must POST to
// /auth/native-session. The login page keeps default-src 'none' with no
// connect-src, so a scripted credential exfiltration there still has nowhere
// to send anything.
function addSecurityHeaders(res, { connectSelf = false } = {}) {
  res.setHeader('Cache-Control', 'no-store')
  const policy = ["default-src 'none'", "script-src 'self'", "style-src 'unsafe-inline'",
                  "img-src 'self'", "form-action 'self'", "frame-ancestors 'none'",
                  "base-uri 'none'"]
  if (connectSelf) policy.splice(1, 0, "connect-src 'self'")
  res.setHeader('Content-Security-Policy', policy.join('; '))
  res.setHeader('Referrer-Policy', 'same-origin')
  res.setHeader('X-Content-Type-Options', 'nosniff')
  res.setHeader('X-Frame-Options', 'DENY')
}

// The page must satisfy the existing CSP (script-src 'self'): no inline script.
// The target path travels in a data attribute, and the logic is served from
// /auth/handoff.js as a same-origin script.
function handoffPage(next) {
  const target = escapeHtml(next || '/')
  return `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>正在進入工作空間</title><meta name="referrer" content="same-origin">
<script defer src="/auth/handoff.js"></script></head>
<body><main><h1>正在進入你的工作空間…</h1>
<p id="state" data-next="${target}">請稍候，正在完成登入。</p>
<noscript><p>需要 JavaScript 才能完成登入。</p></noscript></main></body></html>`
}

const HANDOFF_SCRIPT = `'use strict';
(async () => {
  const state = document.getElementById('state');
  const next = state.dataset.next || '/';
  try {
    const response = await fetch('/auth/native-session', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: {'Content-Type': 'application/json'}, body: '{}'
    });
    if (response.status === 204) { location.replace(next); return; }
    state.textContent = response.status === 401
      ? '登入已失效，請重新登入。'
      : '目前無法完成登入交接，請聯絡管理員。';
  } catch (_) {
    state.textContent = '無法連線，請重新整理再試。';
  }
})();
`

function sendHtml(res, status, html, options) {
  addSecurityHeaders(res, options)
  res.statusCode = status
  res.setHeader('Content-Type', 'text/html; charset=utf-8')
  res.setHeader('Content-Length', Buffer.byteLength(html))
  res.end(html)
}

function sendJson(res, status, value) {
  const body = JSON.stringify(value)
  addSecurityHeaders(res)
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.setHeader('Content-Length', Buffer.byteLength(body))
  res.end(body)
}

async function readLoginBody(req) {
  const chunks = []
  let size = 0
  for await (const chunk of req) {
    size += chunk.length
    if (size > MAX_LOGIN_BODY_BYTES) throw new Error('LOGIN_BODY_TOO_LARGE')
    chunks.push(chunk)
  }
  const raw = Buffer.concat(chunks).toString('utf8')
  const contentType = req.headers['content-type']?.split(';', 1)[0]?.trim()
  if (contentType === 'application/json') {
    const parsed = JSON.parse(raw)
    return {
      username: typeof parsed.username === 'string' ? parsed.username : '',
      password: typeof parsed.password === 'string' ? parsed.password : '',
      next: typeof parsed.next === 'string' ? parsed.next : '/',
    }
  }
  const parsed = new URLSearchParams(raw)
  return {
    username: parsed.get('username') ?? '',
    password: parsed.get('password') ?? '',
    next: parsed.get('next') ?? '/',
  }
}

export default class AuthenticatedWebServer extends Service {
  static Config = Config
  constructor(ctx, config) {
    super(ctx, 'webServer')
    this.config = config
    this.logger = ctx.logger('dsh-web-auth')
    this.routes = new Map()
    this.publicPrefixes = publicPrefixesOf(config.publicPrefixes)
    this.upgrades = new Map()
    this.indexTaps = [injectAuthBootstrap, rebrandIndex]
    this.fallback = undefined
    this.boundPort = config.port
    this.authRequired = config.authMode === 'always' || config.host !== '127.0.0.1'

    if (this.authRequired && !config.password && !config.passwordHash) {
      throw new Error('dsh-web-auth requires DSH_WEB_AUTH_PASSWORD_HASH or DSH_WEB_AUTH_PASSWORD when authentication is active.')
    }
    if (config.passwordHash && !config.passwordHash.startsWith('scrypt$')) {
      throw new Error('dsh-web-auth passwordHash must use the scrypt format generated by dsh-web-auth hash-password.')
    }

    // Keep logins alive across process restarts. Defaults under DSH_HOME so
    // the file follows the instance, not the checkout; opt out with
    // WEB_AUTH_SESSION_STORE=off.
    this.sessions = new SessionStore(
      config.sessionTtlMinutes * 60_000,
      undefined,
      undefined,
      resolveSessionStorePath(),
    )
    this.limiter = new AttemptLimiter(config.maxAttempts, config.attemptWindowSeconds * 1000)
    this.server = createServer((req, res) => {
      void this.handleRequest(req, res).catch((error) => this.handleRequestError(error, res))
    })
    this.server.on('upgrade', (req, socket, head) => {
      void this.handleUpgrade(req, socket, head).catch((error) => {
        this.logger.warn('WebSocket upgrade failed: %s', error instanceof Error ? error.message : String(error))
        socket.destroy()
      })
    })

  }

  async [Service.init]() {
    await new Promise((resolve, reject) => {
      const onError = (error) => reject(error)
      this.server.once('error', onError)
      this.server.listen(this.config.port, this.config.host, () => {
        this.server.off('error', onError)
        const address = this.server.address()
        if (address && typeof address === 'object') this.boundPort = address.port
        resolve()
      })
    })
    return async () => {
      this.server.closeAllConnections?.()
      await new Promise((resolve) => this.server.close(() => resolve()))
    }
  }

  get host() {
    return this.config.host
  }

  get port() {
    return this.boundPort
  }

  register(route) {
    this.validateRoute(route)
    const key = `${route.kind}:${route.path}`
    if (this.routes.has(key)) throw new Error(`Duplicate Web route: ${key}`)
    this.routes.set(key, route)
    return () => this.routes.delete(key)
  }

  registerUpgrade(route) {
    // Match stock dsh-host-webserver: absolute path required; trailing slash is allowed
    // (e.g. remote-web-ui registers exact "/m/" for the mobile SPA root).
    if (!route || typeof route.path !== 'string' || !route.path.startsWith('/')) {
      throw new Error('Web upgrade route path must be an absolute path.')
    }
    if (this.upgrades.has(route.path)) throw new Error(`Duplicate Web upgrade route: ${route.path}`)
    this.upgrades.set(route.path, route.handler)
    return () => this.upgrades.delete(route.path)
  }

  registerFallback(handler) {
    if (this.fallback) throw new Error('The Web fallback route is already registered.')
    this.fallback = handler
    return () => {
      if (this.fallback === handler) this.fallback = undefined
    }
  }

  tapIndex(transform) {
    this.indexTaps.push(transform)
    return () => {
      const index = this.indexTaps.indexOf(transform)
      if (index >= 0) this.indexTaps.splice(index, 1)
    }
  }

  applyIndexTaps(html) {
    return this.indexTaps.reduce((current, transform) => transform(current), html)
  }

// dsh-host-frontend-static (rc.2) calls the stock webServer's renderIndex
  // method. Keep compatibility with that contract while retaining the
  // plugin's existing tap-based injection behavior.
  renderIndex(html) {
    const rows = []
    // A site serves dsh on a public hostname behind
    // authenticated nginx + web-auth. dsh's client marks remote (non-loopback)
    // pages as `not an operator's own machine`, which gates the settings
    // surfaces ("settings are unavailable in this browser"). Declaring the
    // page owns the Host unlocks those surfaces; there is no bundled
    // transport here, so the RPC wiring stays the default HTTP/WS path.
    rows.push({
      kind: 'global',
      name: '__DSH_TRANSPORT__',
      value: { ownsHost: true },
    })
    this.ctx.emit('webserver/index-inject', rows)
    return this.applyIndexTaps(renderIndexInjections(html, rows))
  }

  validateRoute(route) {
    if (!route || !['exact', 'prefix'].includes(route.kind) || typeof route.handler !== 'function') {
      throw new Error('Invalid Web route registration.')
    }
    // Match stock dsh-host-webserver: absolute path required; trailing slash is allowed
    // (e.g. remote-web-ui registers exact "/m/" for the mobile SPA root).
    if (typeof route.path !== 'string' || !route.path.startsWith('/')) {
      throw new Error('Web route path must be an absolute path.')
    }
  }

  requestIp(req) {
    if (this.config.trustProxy) {
      const forwarded = req.headers['x-forwarded-for']
      const first = Array.isArray(forwarded) ? forwarded[0] : forwarded?.split(',', 1)[0]
      if (first?.trim()) return first.trim()
    }
    return req.socket.remoteAddress ?? 'unknown'
  }

  isSecureRequest(req) {
    if (this.config.secureCookie === 'always') return true
    if (this.config.secureCookie === 'never') return false
    if (req.socket.encrypted) return true
    if (!this.config.trustProxy) return false
    const proto = req.headers['x-forwarded-proto']
    return (Array.isArray(proto) ? proto[0] : proto?.split(',', 1)[0])?.trim().toLowerCase() === 'https'
  }

  cookieHeader(token, req, maxAgeSeconds) {
    // SameSite=Lax, not Strict: a customer arriving from the controlling
    // platform performs a cross-site top-level navigation, and Strict withholds
    // the cookie on that first request. The session would then be missing for
    // the WebSocket the app opens, leaving it retrying forever. Lax still
    // withholds the cookie from cross-site subresources and form POSTs, and
    // state-changing routes independently verify Origin.
    const parts = [`${COOKIE_NAME}=${encodeURIComponent(token)}`, 'Path=/', 'HttpOnly', 'SameSite=Lax', `Max-Age=${maxAgeSeconds}`]
    if (this.isSecureRequest(req)) parts.push('Secure')
    return parts.join('; ')
  }

  verifyEntryTicket(raw) {
    // Format: <expiryMs>.<nonce>.<base64url hmac>. Verified with a constant-time
    // comparison; replay is prevented by remembering spent nonces until they
    // would have expired anyway, so the set cannot grow without bound.
    if (typeof raw !== 'string' || raw.length > 512) return { ok: false }
    const parts = raw.split('.')
    if (parts.length !== 3) return { ok: false }
    const [expiry, nonce, signature] = parts
    if (!/^[0-9]{1,15}$/.test(expiry) || !/^[A-Za-z0-9_-]{16,128}$/.test(nonce)) return { ok: false }
    const expiresAt = Number(expiry)
    const now = Date.now()
    if (!Number.isSafeInteger(expiresAt) || expiresAt <= now) return { ok: false }
    // Refuse a ticket minted far in the future: a clock-skewed or malicious
    // issuer must not be able to create a long-lived credential.
    if (expiresAt - now > 300_000) return { ok: false }
    const expected = createHmac('sha256', this.config.entrySecret)
      .update(`${expiry}.${nonce}`).digest()
    let supplied
    try {
      supplied = Buffer.from(signature, 'base64url')
    } catch {
      return { ok: false }
    }
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      return { ok: false }
    }
    this.spentTickets ??= new Map()
    for (const [seen, until] of this.spentTickets) {
      if (until <= now) this.spentTickets.delete(seen)
    }
    if (this.spentTickets.has(nonce)) return { ok: false }
    this.spentTickets.set(nonce, expiresAt)
    return { ok: true }
  }

  async mintNativeSession(connection, proto, host) {
    // Drive the native authorizer with a synthetic request built here, so the
    // launch token stays in-process. We accept only the Set-Cookie it mints.
    const authenticated = connection.authenticatedUrl(`${proto}://${host}`)
    const target = new URL(authenticated)
    let cookie
    const captured = {
      writeHead(status, headers) {
        if (status === 303 && headers && headers['set-cookie']) cookie = headers['set-cookie']
      },
      end() {},
      setHeader() {},
    }
    connection.authorizeIndex(
      { method: 'GET', url: `${target.pathname}${target.search}`, headers: { host } },
      captured,
    )
    if (typeof cookie !== 'string' || !cookie) return undefined
    // The native layer omits Secure; add it on HTTPS so the session cookie is
    // never sent over a plaintext downgrade.
    if (proto === 'https' && !/;\s*Secure/i.test(cookie)) cookie += '; Secure'
    return cookie
  }

  currentSession(req) {
    if (!this.authRequired) return { username: this.config.username }
    return this.sessions.get(parseCookies(req.headers.cookie).get(COOKIE_NAME))
  }

  credentialsMatch(username, password) {
    const userMatches = verifyPlainPassword(username, this.config.username)
    const passwordMatches = this.config.passwordHash
      ? verifyPassword(password, this.config.passwordHash)
      : verifyPlainPassword(password, this.config.password)
    return userMatches && passwordMatches
  }

  validOrigin(req) {
    const origin = req.headers.origin
    if (!origin) return true
    try {
      const scheme = this.isSecureRequest(req) ? 'https:' : 'http:'
      const requestOrigin = new URL(`${scheme}//${req.headers.host}`).origin
      return new URL(origin).origin === requestOrigin
    } catch {
      return false
    }
  }

  async handleAuthRoute(pathname, url, req, res) {
    if (pathname === '/auth/bootstrap.js' && (req.method === 'GET' || req.method === 'HEAD')) {
      addSecurityHeaders(res)
      res.statusCode = 200
      res.setHeader('Content-Type', 'text/javascript; charset=utf-8')
      res.setHeader('Content-Length', Buffer.byteLength(AUTH_BOOTSTRAP_SCRIPT))
      return res.end(req.method === 'HEAD' ? undefined : AUTH_BOOTSTRAP_SCRIPT)
    }

    if (pathname === '/auth/favicon.svg' && (req.method === 'GET' || req.method === 'HEAD')) {
      addSecurityHeaders(res)
      res.statusCode = 200
      res.setHeader('Content-Type', 'image/svg+xml; charset=utf-8')
      res.setHeader('Content-Length', Buffer.byteLength(BRAND_FAVICON))
      return res.end(req.method === 'HEAD' ? undefined : BRAND_FAVICON)
    }

    if (pathname === '/auth/manifest.webmanifest' && (req.method === 'GET' || req.method === 'HEAD')) {
      addSecurityHeaders(res)
      res.statusCode = 200
      res.setHeader('Content-Type', 'application/manifest+json; charset=utf-8')
      res.setHeader('Content-Length', Buffer.byteLength(BRAND_MANIFEST))
      return res.end(req.method === 'HEAD' ? undefined : BRAND_MANIFEST)
    }

    if (pathname === '/auth/login' && (req.method === 'GET' || req.method === 'HEAD')) {
      const html = loginPage(url.searchParams.get('next') ?? '/')
      if (req.method === 'HEAD') {
        addSecurityHeaders(res)
        res.statusCode = 200
        res.setHeader('Content-Type', 'text/html; charset=utf-8')
        return res.end()
      }
      return sendHtml(res, 200, html)
    }

    if (pathname === '/auth/login' && req.method === 'POST') {
      if (!this.validOrigin(req)) return sendJson(res, 403, { error: 'invalid_origin' })
      const ip = this.requestIp(req)
      const decision = this.limiter.check(ip)
      if (!decision.allowed) {
        res.setHeader('Retry-After', decision.retryAfterSeconds)
        return sendHtml(res, 429, loginPage('/', '尝试次数过多，请稍后重试。'))
      }
      let body
      try {
        body = await readLoginBody(req)
      } catch {
        return sendJson(res, 400, { error: 'invalid_login_request' })
      }
      if (!this.credentialsMatch(body.username, body.password)) {
        this.limiter.fail(ip)
        return sendHtml(res, 401, loginPage(body.next, '用户名或密码错误。'))
      }
      this.limiter.clear(ip)
      const token = this.sessions.create(this.config.username)
      res.statusCode = 303
      res.setHeader('Cache-Control', 'no-store')
      res.setHeader('Set-Cookie', this.cookieHeader(token, req, this.config.sessionTtlMinutes * 60))
      // Redirect through the dsh launch-token URL so the native browser-auth
      // layer (dsh-client-connection) can mint its own signed cookie; a plain
      // `next` path would otherwise hit the native 401 gate ("reopen the URL
      // printed by dsh web"). Falls back to `next` when connection is absent.
      // Never place the native launch token in a browser-visible Location: it
      // is a bearer credential for this process. Redirect to a fixed, tokenless
      // handoff page that performs a same-origin exchange instead.
      res.setHeader('Location', `/auth/handoff?next=${encodeURIComponent(sanitizeReturnPath(body.next))}`)
      return res.end()
    }

    if (pathname === '/auth/enter' && req.method === 'GET') {
      // Single-use entry ticket issued by the controlling platform. It carries
      // no password and cannot be replayed: the nonce is burned on first use
      // and the ticket expires in seconds. A valid ticket establishes the same
      // web-auth session a password login would, then continues to the normal
      // tokenless native handoff.
      if (!this.config.entrySecret) return sendJson(res, 404, { error: 'not_found' })
      const ticket = url.searchParams.get('ticket') ?? ''
      const verdict = this.verifyEntryTicket(ticket)
      if (!verdict.ok) {
        // Never explain which check failed; an attacker learns nothing.
        return sendHtml(res, 401, loginPage('/', '進入連結已失效，請回到平台重新進入。'))
      }
      const token = this.sessions.create(this.config.username)
      // Optional same-origin continuation (a project site's member confirm
      // page). sanitizeReturnPath refuses anything that is not a local path.
      const next = sanitizeReturnPath(url.searchParams.get('next') ?? '/')
      res.statusCode = 303
      res.setHeader('Cache-Control', 'no-store')
      res.setHeader('Referrer-Policy', 'no-referrer')
      res.setHeader('Set-Cookie', this.cookieHeader(token, req, this.config.sessionTtlMinutes * 60))
      res.setHeader('Location', `/auth/handoff?next=${encodeURIComponent(next)}`)
      return res.end()
    }

    if (pathname === '/auth/handoff.js' && req.method === 'GET') {
      // Same-origin script for the handoff page; requires a session like the
      // page itself, and is never cached.
      if (!this.currentSession(req)) return sendJson(res, 401, { error: 'authentication_required' })
      res.statusCode = 200
      res.setHeader('Content-Type', 'text/javascript; charset=utf-8')
      res.setHeader('Cache-Control', 'no-store')
      res.setHeader('X-Content-Type-Options', 'nosniff')
      return res.end(HANDOFF_SCRIPT)
    }

    if (pathname === '/auth/handoff' && req.method === 'GET') {
      if (!this.currentSession(req)) {
        res.statusCode = 303
        res.setHeader('Cache-Control', 'no-store')
        res.setHeader('Location', '/auth/login')
        return res.end()
      }
      return sendHtml(res, 200, handoffPage(sanitizeReturnPath(url.searchParams.get('next'))),
                      { connectSelf: true })
    }

    if (pathname === '/auth/native-session' && req.method === 'POST') {
      // Tokenless exchange: the caller proves an authenticated web-auth session
      // plus same-origin intent; the launch token is used only inside this
      // process and never appears in a URL, body or log.
      if (!this.validOrigin(req)) return sendJson(res, 403, { error: 'invalid_origin' })
      if (req.headers.origin === undefined) return sendJson(res, 403, { error: 'origin_required' })
      const site = req.headers['sec-fetch-site']
      if (site !== undefined && site !== 'same-origin') {
        return sendJson(res, 403, { error: 'invalid_origin' })
      }
      if (!this.currentSession(req)) return sendJson(res, 401, { error: 'authentication_required' })
      const host = req.headers.host
      if (!host) return sendJson(res, 400, { error: 'invalid_request' })
      let connection
      try {
        connection = await this.ctx.get('connection', false)
      } catch {
        connection = undefined
      }
      // Fail closed when the pinned native layer cannot issue a session: never
      // fabricate a cookie or fall back to exposing the token.
      if (!connection?.authenticatedUrl || !connection?.authorizeIndex) {
        return sendJson(res, 503, { error: 'native_session_unsupported' })
      }
      const proto = this.isSecureRequest(req) ? 'https' : 'http'
      let issued
      try {
        issued = await this.mintNativeSession(connection, proto, host)
      } catch {
        return sendJson(res, 503, { error: 'native_session_unsupported' })
      }
      if (!issued) return sendJson(res, 503, { error: 'native_session_unsupported' })
      res.statusCode = 204
      res.setHeader('Cache-Control', 'no-store')
      res.setHeader('Set-Cookie', issued)
      return res.end()
    }

    if (pathname === '/auth/logout' && req.method === 'POST') {
      if (!this.validOrigin(req)) return sendJson(res, 403, { error: 'invalid_origin' })
      this.sessions.delete(parseCookies(req.headers.cookie).get(COOKIE_NAME))
      res.statusCode = 303
      res.setHeader('Cache-Control', 'no-store')
      res.setHeader('Set-Cookie', this.cookieHeader('', req, 0))
      res.setHeader('Location', '/auth/login')
      return res.end()
    }

    if (pathname === '/auth/status' && req.method === 'GET') {
      const session = this.currentSession(req)
      return sendJson(res, session ? 200 : 401, {
        authenticated: Boolean(session),
        required: this.authRequired,
        username: session?.username,
      })
    }

    res.statusCode = 405
    res.setHeader('Allow', pathname === '/auth/status' ? 'GET' : 'GET, HEAD, POST')
    return res.end()
  }

  async handleRequest(req, res) {
    const url = new URL(req.url ?? '/', 'http://dsh.local')
    const pathname = decodeURIComponent(url.pathname)

    if (pathname === AUTH_PREFIX || pathname.startsWith(`${AUTH_PREFIX}/`)) {
      return this.handleAuthRoute(pathname, url, req, res)
    }

    // Public only when the raw path is already canonical and needs no decoding,
    // so the gate and every downstream router see the very same path.
    const rawPath = String(req.url ?? '/').split(/[?#]/, 1)[0]
    const publicPrefix = rawPath === url.pathname && rawPath === pathname && !rawPath.startsWith('//') && canonicalPath(pathname)
      ? this.publicPrefixes.find((p) => pathname === p || pathname.startsWith(`${p}/`))
      : undefined
    if (publicPrefix !== undefined) {
      // Only a registered prefix route inside the public prefix may answer;
      // never the GUI fallback, never an exact route elsewhere.
      const route = [...this.routes.values()]
        .filter((r) => r.kind === 'prefix' && (r.path === publicPrefix || r.path.startsWith(`${publicPrefix}/`)) && (pathname === r.path || pathname.startsWith(`${r.path}/`)))
        .sort((a, b) => b.path.length - a.path.length)[0]
      if (route) {
        stripLoginCookies(req, res)
        return route.handler(req, res)
      }
      res.statusCode = 404
      return res.end('Not Found')
    }

    if (!this.currentSession(req)) {
      const acceptsHtml = req.method === 'GET' && (req.headers.accept ?? '').includes('text/html')
      if (acceptsHtml) {
        res.statusCode = 302
        res.setHeader('Cache-Control', 'no-store')
        res.setHeader('Location', `/auth/login?next=${encodeURIComponent(sanitizeReturnPath(req.url ?? '/'))}`)
        return res.end()
      }
      return sendJson(res, 401, { error: 'authentication_required' })
    }

    const exact = this.routes.get(`exact:${pathname}`)
    if (exact) return exact.handler(req, res)

    const prefix = [...this.routes.values()]
      .filter((route) => route.kind === 'prefix' && (pathname === route.path || pathname.startsWith(`${route.path}/`)))
      .sort((a, b) => b.path.length - a.path.length)[0]
    if (prefix) return prefix.handler(req, res)
    if (this.fallback) return this.fallback(req, res)
    res.statusCode = 404
    res.end('Not Found')
  }

  async handleUpgrade(req, socket, head) {
    const url = new URL(req.url ?? '/', 'http://dsh.local')
    const pathname = decodeURIComponent(url.pathname)
    if (!this.currentSession(req)) {
      socket.write('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\nContent-Type: application/json\r\n\r\n{"error":"authentication_required"}')
      socket.destroy()
      return
    }
    const handler = this.upgrades.get(pathname)
    if (!handler) {
      socket.write('HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n')
      socket.destroy()
      return
    }
    await handler(req, socket, head)
  }

  handleRequestError(error, res) {
    this.logger.warn('HTTP request failed: %s', error instanceof Error ? error.message : String(error))
    if (res.headersSent) return res.destroy()
    sendJson(res, 400, { error: 'bad_request' })
  }
}
