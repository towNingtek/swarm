// Fetcher against a fake run and a fake clock. No python, no gh, no real timers.
import test from 'node:test'
import assert from 'node:assert/strict'
import { startFetcher } from '../lib/fetcher.js'

const flush = () => new Promise((r) => setImmediate(r))

function setup(opts = {}) {
  const calls = [] // { file, args, resolve, reject }
  const logs = []
  const clock = { t: 1_000_000, tick: null, ms: null, cleared: false }
  const run = (file, args) => new Promise((resolve, reject) => calls.push({ file, args, resolve, reject }))
  const f = startFetcher({
    run,
    python: 'py',
    script: '/x/fetch-project.py',
    projects: [3, 5],
    outDir: '/out',
    log: (m) => logs.push(m),
    now: () => clock.t,
    setInterval: (fn, ms) => { clock.tick = fn; clock.ms = ms; return 42 },
    clearInterval: (id) => { if (id === 42) clock.cleared = true },
    ...opts,
  })
  // finish the oldest pending call
  const finish = async (ok = true) => {
    const c = calls.find((c) => !c.done)
    c.done = true
    if (ok) c.resolve({ stdout: '' }); else c.reject(Object.assign(new Error('exit 1'), { stderr: 'boom\n' }))
    await flush()
  }
  return { f, calls, logs, clock, finish }
}

test('runs once at start, each project in order with --one N outDir', async () => {
  const { calls, clock, finish } = setup()
  assert.equal(clock.ms, 10 * 60_000)
  assert.equal(calls.length, 1, 'only one at a time')
  assert.deepEqual(calls[0], { ...calls[0], file: 'py', args: ['/x/fetch-project.py', '--one', '3', '/out'] })
  await finish()
  assert.equal(calls.length, 2)
  assert.deepEqual(calls[1].args, ['/x/fetch-project.py', '--one', '5', '/out'])
  await finish()
  assert.equal(calls.length, 2)
})

test('interval runs another round; skipped while the previous round is unfinished', async () => {
  const { calls, logs, clock, finish } = setup({ intervalMs: 5000 })
  assert.equal(clock.ms, 5000)
  clock.tick() // round 1 still on project 3
  await flush()
  assert.equal(calls.length, 1)
  assert.match(logs.join('\n'), /skipped/)
  await finish(); await finish()
  assert.equal(calls.length, 2, 'skipped round did not queue extra work')
  clock.tick()
  await flush()
  assert.equal(calls.length, 3)
  assert.deepEqual(calls[2].args.slice(1, 3), ['--one', '3'])
})

test('fetchNow queues one immediately, not again for the same project within 60s', async () => {
  const { f, calls, clock, finish } = setup({ projects: [] })
  assert.equal(calls.length, 0)
  assert.equal(f.fetchNow(7), true)
  assert.equal(calls.length, 1)
  assert.deepEqual(calls[0].args, ['/x/fetch-project.py', '--one', '7', '/out'])
  await finish()
  clock.t += 59_000
  assert.equal(f.fetchNow(7), false)
  assert.equal(calls.length, 1)
  assert.equal(f.fetchNow(8), true, 'other projects are not blocked')
  await finish()
  clock.t += 1_000
  assert.equal(f.fetchNow(7), true)
  assert.equal(calls.length, 3)
})

test('fetchNow during a round waits its turn (one at a time)', async () => {
  const { f, calls, finish } = setup()
  f.fetchNow(9)
  assert.equal(calls.length, 1)
  await finish(); await finish()
  assert.deepEqual(calls.map((c) => c.args[2]), ['3', '5', '9'])
})

test('a failing child only logs one line and does not stop the round', async () => {
  const { calls, logs, finish } = setup()
  await finish(false)
  assert.equal(logs.length, 1)
  assert.match(logs[0], /p3.*boom/)
  assert.ok(!logs[0].includes('\n'))
  assert.equal(calls.length, 2, 'moved on to the next project')
  await finish()
  assert.equal(logs.length, 1)
})

test('stop(): no new runs afterwards', async () => {
  const { f, calls, clock, finish } = setup()
  f.stop()
  assert.equal(clock.cleared, true)
  await finish() // the in-flight one may finish, but nothing follows it
  assert.equal(calls.length, 1)
  clock.tick()
  assert.equal(f.fetchNow(11), false)
  await flush()
  assert.equal(calls.length, 1)
})
