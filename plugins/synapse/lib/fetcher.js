// dsh-synapse fetcher: keeps dataDir/{hub,projects,hives}.json fresh from inside the site.
//
//   hub.json       HackMD note (config.hackmdNote) every minute, read with the site's HackMD token
//                  (config.hackmdTokenFile; same file the office already uses). Unchanged → file untouched.
//   projects.json  open GitHub Projects + EPICs (title [EPIC] + sub-issues) of config.orgs, every 10 min,
//                  with the site's own `gh` (the same login the 任務星系 uses). Read-only.
//   hives.json     the site's swarm hives (config.hivesDir/*/HIVE.md), every minute.
//
// Nothing here writes to HackMD, GitHub or swarm. A failed round keeps the previous file.
import { execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const sha = (s) => createHash('sha256').update(s).digest('hex').slice(0, 16)

export function writeAtomic(path, text) {
  const tmp = `${path}.tmp-${process.pid}`
  writeFileSync(tmp, text, { mode: 0o600 })
  renameSync(tmp, path)
}

/** Strip the preview banner line ("> 🧪 …") the hub may carry. */
export const cleanHub = (md) => md.replace(/^>\s*🧪[^\n]*\n\n?/, '')

export async function fetchHub({ note, tokenFile, out, fetchImpl = fetch }) {
  if (!note || !tokenFile || !existsSync(tokenFile)) return { skipped: true }
  const token = readFileSync(tokenFile, 'utf8').trim()
  const r = await fetchImpl(`https://api.hackmd.io/v1/notes/${encodeURIComponent(note)}`, { headers: { authorization: `Bearer ${token}` } })
  if (!r.ok) throw new Error(`HackMD HTTP ${r.status}`)
  const j = await r.json()
  const markdown = cleanHub(j.content || '')
  const digest = sha(markdown)
  try { if (JSON.parse(readFileSync(out, 'utf8')).digest === digest) return { changed: false } } catch {}
  writeAtomic(out, JSON.stringify({ markdown, digest, title: j.title, syncedAt: Math.floor(Date.now() / 1000) }))
  return { changed: true }
}

/** One hive per directory with a HIVE.md: id, title, kind, state machine (## 狀態機 bullets). */
export function readHives(dir, devices = []) {
  if (!dir || !existsSync(dir)) return []
  return readdirSync(dir, { withFileTypes: true }).filter((d) => d.isDirectory() && existsSync(join(dir, d.name, 'HIVE.md'))).map((d) => {
    const t = readFileSync(join(dir, d.name, 'HIVE.md'), 'utf8')
    const title = ((t.match(/^#\s+(.+)$/m) || [])[1] || d.name).replace(/\s*—\s*專案規則\s*$/, '')
    const host = /類型\*\*:\s*GitLab/i.test(t) ? 'gitlab' : 'github'
    const org = ((t.match(/\*\*(?:組織|Group)\*\*:\s*([^\n（(]+)/) || [])[1] || '').trim()
    const kind = devices.includes(d.name) ? 'device' : /組織規則/.test(t) ? 'org' : 'project'
    const sm = (t.match(/^##\s*狀態機\s*\n([\s\S]*?)(?=^##\s|(?![\s\S]))/m) || [])[1]
    const states = sm ? [...sm.matchAll(/^\s*[-*]\s*(.+)$/gm)].map((m) => m[1].trim()).filter(Boolean) : []
    return { id: d.name, title, host, org, kind, states }
  }).sort((a, b) => a.id.localeCompare(b.id))
}

const LIST = 'query($o:String!){organization(login:$o){projectsV2(first:50){nodes{number title url closed items{totalCount}}}}}'
const SEARCH = `query($q:String!,$after:String){search(query:$q,type:ISSUE,first:100,after:$after){pageInfo{hasNextPage endCursor}
 nodes{... on Issue{number title url state closedAt updatedAt repository{nameWithOwner}
  subIssuesSummary{total completed}
  projectItems(first:10){nodes{project{number owner{... on Organization{login}}}}}}}}}`
const EPIC = /^\s*\[EPIC\]/i

export function ghGraphql(query, vars, { gh = 'gh', timeout = 90_000 } = {}) {
  const args = ['api', 'graphql', '-f', `query=${query}`]
  for (const [k, v] of Object.entries(vars)) if (v !== undefined && v !== null) args.push(typeof v === 'number' ? '-F' : '-f', `${k}=${v}`)
  return new Promise((resolve, reject) => execFile(gh, args, { timeout, maxBuffer: 32 << 20 }, (err, stdout, stderr) => {
    if (err) return reject(new Error(String(stderr || err.message).slice(0, 200)))
    try { resolve(JSON.parse(stdout).data) } catch (e) { reject(e) }
  }))
}

export async function fetchProjects({ orgs, out, graphql = ghGraphql }) {
  const projects = [], epics = new Map(), errors = []
  for (const org of orgs) {
    try {
      for (const p of (await graphql(LIST, { o: org })).organization.projectsV2.nodes)
        if (!p.closed) projects.push({ id: `${org}#${p.number}`, org, number: p.number, title: p.title, url: p.url, cards: p.items.totalCount })
    } catch (e) { errors.push(`${org} project：${e.message}`) }
    try {
      let after
      for (;;) {
        const d = (await graphql(SEARCH, { q: `org:${org} is:issue in:title EPIC`, after })).search
        for (const n of d.nodes) {
          if (!n || !EPIC.test(n.title || '') || !n.subIssuesSummary?.total) continue
          const repo = n.repository.nameWithOwner
          epics.set(`${repo}#${n.number}`, {
            id: `${repo}#${n.number}`, repo, number: n.number, title: n.title.replace(EPIC, '').trim(), url: n.url, state: n.state,
            closedAt: n.closedAt, updatedAt: n.updatedAt, done: n.subIssuesSummary.completed, total: n.subIssuesSummary.total,
            projects: (n.projectItems?.nodes ?? []).filter((x) => x?.project?.owner).map((x) => `${x.project.owner.login}#${x.project.number}`),
          })
        }
        if (!d.pageInfo.hasNextPage) break
        after = d.pageInfo.endCursor
      }
    } catch (e) { errors.push(`${org} EPIC：${e.message}`) }
  }
  if (!projects.length && !epics.size && errors.length) throw new Error(errors.join('；'))
  const ep = [...epics.values()].sort((a, b) => (a.state !== 'OPEN') - (b.state !== 'OPEN') || a.repo.localeCompare(b.repo) || b.number - a.number)
  writeAtomic(out, JSON.stringify({ projects, epics: ep, errors, syncedAt: Math.floor(Date.now() / 1000) }))
  return { projects: projects.length, epics: ep.length, errors }
}

/** Start the rounds; returns stop(). Each kind runs on its own timer and never overlaps itself. */
export function startFetcher({ dataDir, hub, projects, hivesDir, devices = [], logger = console, hubEveryMs = 60_000, projectsEveryMs = 600_000, deps = {} }) {
  mkdirSync(dataDir, { recursive: true })
  const timers = []
  const every = (label, ms, fn) => {
    let busy = false
    const run = async () => {
      if (busy) return
      busy = true
      try { await fn() } catch (e) { logger.warn?.('synapse %s fetch failed: %s', label, e?.message ?? e) } finally { busy = false }
    }
    run()
    timers.push(setInterval(run, ms))
  }
  if (hub?.note) every('hub', hubEveryMs, () => fetchHub({ note: hub.note, tokenFile: hub.tokenFile, out: join(dataDir, 'hub.json'), fetchImpl: deps.fetch }))
  if (projects?.orgs?.length) every('projects', projectsEveryMs, () => fetchProjects({ orgs: projects.orgs, out: join(dataDir, 'projects.json'), graphql: deps.graphql }))
  if (hivesDir) every('hives', hubEveryMs, async () => writeAtomic(join(dataDir, 'hives.json'), JSON.stringify({ hives: readHives(hivesDir, devices), url: hub?.url || null, syncedAt: Math.floor(Date.now() / 1000) })))
  return () => timers.forEach(clearInterval)
}
