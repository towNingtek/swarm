// Board data fetcher that runs inside the site: every intervalMs it runs
// `python script --one <key> outDir` (wolffia/fetch-project.py) for each project, one at a time;
// a key is a default-org number (3) or "org/number" ("acme/1").
// The site already has python3 and a gh with project scope, so nothing else is needed.
import { execFile } from 'node:child_process'

const TIMEOUT_MS = 180_000
const NOW_DEDUP_MS = 60_000

function defaultRun(file, args, opts = {}) {
  const env = opts.env ? { ...process.env, ...opts.env } : process.env
  return new Promise((resolve, reject) => {
    execFile(file, args, { timeout: opts.timeout ?? TIMEOUT_MS, env }, (err, stdout, stderr) => {
      if (err) { err.stderr = stderr; reject(err) } else resolve({ stdout, stderr })
    })
  })
}

export function startFetcher({
  run = defaultRun,
  python = 'python3',
  script,
  projects = [],
  outDir,
  intervalMs = 10 * 60_000,
  env = undefined, // extra env for the script, e.g. WOLFFIA_PROJECTS
  log = () => {},
  // test hooks: fake clock / timers
  now = Date.now,
  setInterval: setTimer = setInterval,
  clearInterval: clearTimer = clearInterval,
} = {}) {
  const queue = [] // project keys waiting to run, in order
  const lastNow = new Map() // project -> time of last accepted fetchNow
  let busy = false
  let stopped = false

  async function fetchOne(project) {
    try {
      await run(python, [script, '--one', String(project), outDir], { timeout: TIMEOUT_MS, env })
    } catch (err) {
      const why = String(err?.stderr || err?.message || err).trim().split('\n').pop()
      log(`board fetch ${typeof project === 'string' ? project : `p${project}`} failed: ${why}`)
    }
  }

  async function pump() {
    if (busy) return
    busy = true
    try {
      while (queue.length && !stopped) await fetchOne(queue.shift())
    } finally {
      busy = false
    }
  }

  function round() {
    if (stopped) return
    if (busy || queue.length) { log('board fetch: previous round still running, skipped'); return }
    queue.push(...projects)
    pump()
  }

  round()
  const timer = setTimer(round, intervalMs)
  timer?.unref?.()

  return {
    stop() {
      stopped = true
      queue.length = 0
      clearTimer(timer)
    },
    fetchNow(project) {
      if (stopped) return false
      const t = now()
      const last = lastNow.get(project)
      if (last !== undefined && t - last < NOW_DEDUP_MS) return false
      lastNow.set(project, t)
      if (!queue.includes(project)) queue.push(project)
      pump()
      return true
    },
  }
}
