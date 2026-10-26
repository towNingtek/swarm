// 「＋ 加入 project」: the projects the site's gh can add to the board, and adding one.
//   GET  /board/api/projects/available  {github:true, projects:[{owner, number, title, items, added}]}
//                                       open projects of the gh login and its orgs, most cards first;
//                                       added = already on the board. {github:false} without a logged-in gh.
//   POST /board/api/projects/add        {owner, number} -> {ok, key, already, index}
//                                       only a project that is (still) in that list, asked again on GitHub;
//                                       appended to projects.json (other entries kept), fetched once, index returned.
// The site's gh has no `gh project` command: GraphQL only.
import { readFile } from 'node:fs/promises'
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { ActionError, projectKey, runGh } from './actions.js'

export const AVAILABLE_TTL_MS = 10 * 60_000
const PAGE = 'projectsV2(first:100){nodes{number title closed items{totalCount}}}'
const QUERY = `query{viewer{login ${PAGE} organizations(first:100){nodes{login ${PAGE}}}}}`

// gh -> every open project of the viewer and its orgs, most cards first: [{owner, number, title, items}].
// A GitHub App installation token (the platform relay) has a bot viewer with no orgs; the
// projects come from the owners of the repositories the installation can reach instead.
const ORG_QUERY = `query($o:String!){organization(login:$o){login ${PAGE}}}`
const isBot = (login) => /\[bot\]$/.test(login ?? '')

async function installationOrgs(gh) {
  let out
  try { out = JSON.parse(await gh(['api', 'installation/repositories?per_page=100'])) } catch { return [] }
  const owners = [...new Set((out?.repositories ?? []).map((r) => r?.owner?.login).filter((o) => typeof o === 'string'))]
  const orgs = []
  for (const o of owners) {
    try {
      const r = JSON.parse(await gh(['api', 'graphql', '-f', `query=${ORG_QUERY}`, '-f', `o=${o}`]))
      if (r?.data?.organization) orgs.push(r.data.organization)
    } catch {}
  }
  return orgs
}

const VIEWER_LOGIN = 'query{viewer{login}}'

const viewerOf = (out) => {
  const v = out?.data?.viewer
  if (!v) throw new Error(String(out?.errors?.[0]?.message ?? 'GraphQL error').slice(0, 300))
  return v
}

export async function listProjects(gh = runGh) {
  // Who is asking decides the query. An App installation token (the platform relay) may not
  // read viewer.organizations at all — GitHub answers "Resource not accessible by integration"
  // and fails the whole query — so a bot never asks for it.
  const who = viewerOf(JSON.parse(await gh(['api', 'graphql', '-f', `query=${VIEWER_LOGIN}`])))
  let owners
  if (isBot(who.login)) {
    owners = await installationOrgs(gh)
  } else {
    const v = viewerOf(JSON.parse(await gh(['api', 'graphql', '-f', `query=${QUERY}`])))
    owners = [{ login: v.login, projectsV2: v.projectsV2 }, ...(v.organizations?.nodes ?? [])]
  }
  const list = []
  for (const o of owners) {
    if (!o?.login) continue
    for (const p of o.projectsV2?.nodes ?? []) {
      if (!p || p.closed || !Number.isInteger(p.number)) continue
      list.push({ owner: o.login, number: p.number, title: String(p.title ?? ''), items: p.items?.totalCount ?? 0 })
    }
  }
  return list.sort((a, b) => b.items - a.items || a.owner.localeCompare(b.owner) || a.number - b.number)
}

// data/index.js (fetch-project.py) as an object; null when missing or broken.
export async function readIndex(dataDir) {
  let text
  try { text = await readFile(join(dataDir, 'index.js'), 'utf8') } catch { return null }
  const m = /^\s*window\.PROJECT_INDEX\s*=\s*([\s\S]*?);?\s*$/.exec(text)
  if (!m) return null
  try { return JSON.parse(m[1]) } catch { return null }
}

// projects.json entries, as written (null when missing or broken).
export function readProjectsJson(file) {
  try {
    const conf = JSON.parse(readFileSync(file, 'utf8'))
    return conf && typeof conf === 'object' && !Array.isArray(conf) ? conf : null
  } catch {
    return null
  }
}

// Append key to projects.json, keeping every other entry (and anything else in the file) as it was.
export function addToProjectsJson(file, key) {
  const conf = readProjectsJson(file) ?? {}
  const list = Array.isArray(conf.projects) ? conf.projects : []
  if (list.some((x) => projectKey(x) === key)) return false
  const next = { ...conf, default: conf.default ?? key, projects: [...list, key] }
  mkdirSync(dirname(file), { recursive: true })
  const tmp = `${file}.${process.pid}.tmp`
  writeFileSync(tmp, JSON.stringify(next, null, 2) + '\n')
  renameSync(tmp, file)
  return true
}

// projects: the plugin's live list of project keys (mutated on add, so the fetcher and api/status see it).
// file: projects.json. fetchOne: async (key) => fetch that project once into dataDir (rejects on failure).
export function createProjectPicker({ gh = runGh, projects, file, dataDir, fetchOne, onAdd = () => {}, now = Date.now, ttlMs = AVAILABLE_TTL_MS }) {
  let cache = null // { at, list }

  async function fresh() {
    const list = await listProjects(gh)
    cache = { at: now(), list }
    return list
  }

  const mark = (list) => list.map((p) => ({ ...p, added: projects.includes(projectKey(`${p.owner}/${p.number}`)) }))

  async function available() {
    const list = cache && now() - cache.at < ttlMs ? cache.list : await fresh()
    return { github: true, projects: mark(list) }
  }

  async function add(body) {
    if (!body || typeof body !== 'object' || Array.isArray(body)) throw new ActionError('內容不是 JSON 物件')
    const owner = typeof body.owner === 'string' ? body.owner.trim() : ''
    const number = typeof body.number === 'string' && /^\d{1,9}$/.test(body.number.trim()) ? Number(body.number) : body.number
    const key = Number.isInteger(number) && owner ? projectKey(`${owner}/${number}`) : null
    if (key === null) throw new ActionError('缺少 owner 或 number')
    // Never trust the page: ask GitHub again whether this is one of ours and still open.
    const found = (await fresh()).find((p) => projectKey(`${p.owner}/${p.number}`) === key)
    if (!found) throw new ActionError('不在可加入的 project 清單裡')
    const already = projects.includes(key)
    addToProjectsJson(file, key)
    if (!already) {
      projects.push(key)
      onAdd(key)
    }
    let fetched = true
    if (!already) { try { await fetchOne(key) } catch { fetched = false } }
    return { ok: true, key, already, fetched, title: found.title, index: await readIndex(dataDir) }
  }

  return { available, add }
}
