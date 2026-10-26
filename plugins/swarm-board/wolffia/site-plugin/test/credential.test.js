// The board's GitHub credential: the site's own gh login, else the platform relay key.
// Fake fetch, fake clock. No network, no gh.
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createGhEnv, createRelayCredential, readRelayKey, RELAY_KEY_FILE } from '../lib/credential.js'

const KEY = 'scs_' + 'a'.repeat(43)
const TOKEN = 'ghs_' + 'b'.repeat(36)
const iso = (ms) => new Date(ms).toISOString()

function relayFetch({ status = 200, permissions = { issues: 'write', organization_projects: 'write', metadata: 'read' }, expiresIn = 3600_000, token = TOKEN, clock } = {}) {
  const calls = []
  const fn = async (url, init) => {
    calls.push({ url, init })
    if (status === 'down') { const e = new Error('connect'); e.code = 'ECONNREFUSED'; throw e }
    return { ok: status === 200, status, json: async () => (status === 200 ? { token, expires_at: iso(clock() + expiresIn), permissions } : { error: { code: 'not_grantable' } }) }
  }
  fn.calls = calls
  return fn
}

test('readRelayKey: only a well-formed scs_ key from .swarm/keys/shared', () => {
  const ws = mkdtempSync(join(tmpdir(), 'cred-'))
  assert.equal(readRelayKey(ws), null)
  mkdirSync(join(ws, '.swarm', 'keys', 'shared'), { recursive: true })
  writeFileSync(join(ws, RELAY_KEY_FILE), 'ghp_notarelaykey\n')
  assert.equal(readRelayKey(ws), null)
  writeFileSync(join(ws, RELAY_KEY_FILE), KEY + '\n')
  assert.equal(readRelayKey(ws), KEY)
  assert.equal(readRelayKey(null), null)
})

test('relay: exchanges the key, sends it only as a bearer header, caches the token', async () => {
  let t = 1_000_000
  const fetch = relayFetch({ clock: () => t })
  const relay = createRelayCredential({ relayKey: () => KEY, fetch, now: () => t })
  assert.equal(await relay.get(), TOKEN)
  assert.equal(fetch.calls[0].init.headers.Authorization, `Bearer ${KEY}`)
  assert.equal(fetch.calls[0].init.method, 'POST')
  assert.equal(await relay.get(), TOKEN)
  assert.equal(fetch.calls.length, 1, 'cached')
  t += 3600_000 - 4 * 60_000 // within five minutes of expiry
  await relay.get()
  assert.equal(fetch.calls.length, 2, 'refreshed before it expires')
})

test('relay: concurrent requests share one exchange', async () => {
  const t = 0
  const fetch = relayFetch({ clock: () => t })
  const relay = createRelayCredential({ relayKey: () => KEY, fetch, now: () => t })
  const got = await Promise.all([relay.get(), relay.get(), relay.get()])
  assert.deepEqual(got, [TOKEN, TOKEN, TOKEN])
  assert.equal(fetch.calls.length, 1)
})

test('relay: a token without organization_projects is not used (the board cannot run on it)', async () => {
  const logs = []
  let t = 0
  const relay = createRelayCredential({ relayKey: () => KEY, now: () => t, log: (m) => logs.push(m),
    fetch: relayFetch({ clock: () => t, permissions: { issues: 'write', metadata: 'read' } }) })
  assert.equal(await relay.get(), null)
  assert.equal(logs.length, 1)
  assert.match(logs[0], /organization_projects/)
  assert.doesNotMatch(logs[0], /ghs_|scs_/)
})

test('relay: refused or unreachable -> null, logged once, retried only after a backoff', async () => {
  for (const status of [403, 'down']) {
    const logs = []
    let t = 0
    const fetch = relayFetch({ status, clock: () => t })
    const relay = createRelayCredential({ relayKey: () => KEY, fetch, now: () => t, log: (m) => logs.push(m) })
    assert.equal(await relay.get(), null)
    assert.equal(await relay.get(), null)
    assert.equal(fetch.calls.length, 1, 'no hammering')
    t += 60_000
    await relay.get()
    assert.equal(fetch.calls.length, 2)
    assert.equal(logs.length, 1, 'same error is logged once')
    assert.ok(logs.every((l) => !l.includes(KEY)))
  }
})

test('relay: current GitHub installation tokens (long, with _ . -) are accepted', async () => {
  // Shape of a real 2026 installation token: ghs_ + ~380 chars of [A-Za-z0-9_.-].
  const real = 'ghs_' + 'eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOjF9_'.repeat(11) + 'a-b.c_d'
  const t = 0
  const relay = createRelayCredential({ relayKey: () => KEY, now: () => t, fetch: relayFetch({ clock: () => t, token: real }) })
  assert.equal(await relay.get(), real)
})

test('relay: unexpected answers are rejected', async () => {
  for (const token of ['ghp_' + 'x'.repeat(36), '', 'ghs_short', 'ghs_' + 'a'.repeat(30) + ' x', 'ghs_' + 'a'.repeat(30) + '\nX: y', 'ghs_' + 'a'.repeat(2100)]) {
    const t = 0
    const relay = createRelayCredential({ relayKey: () => KEY, now: () => t, fetch: relayFetch({ clock: () => t, token }) })
    assert.equal(await relay.get(), null, token)
  }
})

test('relay: no key -> null without any request; a key put in later counts', async () => {
  let key = null
  const t = 0
  const fetch = relayFetch({ clock: () => t })
  const relay = createRelayCredential({ relayKey: () => key, fetch, now: () => t })
  assert.equal(await relay.get(), null)
  assert.equal(fetch.calls.length, 0)
  key = KEY
  assert.equal(await relay.get(), TOKEN)
})

test('ghEnv: the site gh login wins ({}), else GH_TOKEN from the relay, else null', async () => {
  const relay = { get: async () => TOKEN }
  assert.deepEqual(await createGhEnv({ ghLogin: async () => true, relay })(), {})
  assert.deepEqual(await createGhEnv({ ghLogin: async () => false, relay })(), { GH_TOKEN: TOKEN })
  assert.equal(await createGhEnv({ ghLogin: async () => false, relay: { get: async () => null } })(), null)
  assert.equal(await createGhEnv({ ghLogin: async () => false, relay: null })(), null)
})
