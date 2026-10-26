// Board write actions inside a site, with the site's own gh (same rules as Wolffia's host API):
//   POST /board/api/epic/go         {url, auto_merge?}  放行：加 swarm:go（與選填 swarm:auto-merge）
//   POST /board/api/epic/rego       {url}               重新放行：移除再加回 swarm:go（沒放行過 400）
//   POST /board/api/epic/automerge  {url, on}           加／移除 swarm:auto-merge
//   POST /board/api/status          {project, url, status}  改 project 的 Status 欄；移到「完成」時一併關閉 issue
//                                   project: 2 / "2"（default org 2）or "org/number"（e.g. "acme/1"）
// Only open issues with the `epic` label for the epic actions; only config.projects for status.
// Success: {ok:true, by, already?, auto_merge?, on?, state?} with by = the site's host name.
// Errors: ActionError (400 etc.) or anything else (the caller answers 502).
import { execFile } from 'node:child_process'

export const GO = 'swarm:go'
export const AUTO = 'swarm:auto-merge'
export const DONE = '完成'
export const ISSUE_URL = /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/issues\/\d+$/
// The default org: bare numbers in config.projects belong to it (env SWARM_BOARD_ORG; historical default kept).
export let TOWN = (process.env.SWARM_BOARD_ORG || '').toLowerCase()
export function setDefaultOrg(org) { if (typeof org === 'string' && /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(org)) TOWN = org.toLowerCase() }
const KEY = /^([A-Za-z0-9][A-Za-z0-9-]{0,38})\/(\d{1,9})$/

// A config.projects entry (or api/status `project`) -> its key: the number for the default org
// (2, "2", "<default org>/2"), "org/number" with the org lowercased for any other org; null otherwise.
export function projectKey(x) {
  if (Number.isInteger(x)) return x > 0 && x < 1e9 ? x : null
  if (typeof x !== 'string') return null
  const s = x.trim()
  if (/^\d{1,9}$/.test(s)) return projectKey(Number(s))
  const m = KEY.exec(s)
  if (!m || !(Number(m[2]) > 0)) return null
  const org = m[1].toLowerCase()
  return org === TOWN ? Number(m[2]) : `${org}/${Number(m[2])}`
}

// key -> { owner, num }; owner is null for a bare number (the caller decides: board data, else the default org).
export function splitKey(key) {
  if (typeof key === 'string') { const [owner, num] = key.split('/'); return { owner, num: Number(num) } }
  return { owner: null, num: key }
}

// key -> data file name: p2.js, p-acme-1.js (same as fetch-project.py).
export const dataFile = (key) => typeof key === 'string' ? `p-${key.replace('/', '-')}.js` : `p${key}.js`

export class ActionError extends Error {
  constructor(message, status = 400) {
    super(message)
    this.status = status
  }
}

/** Default gh runner: resolves stdout, rejects with the last line of stderr. */
// env: extra environment for this gh only (e.g. { GH_TOKEN } from the platform relay).
export function runGh(args, timeoutMs = 60_000, env = undefined) {
  return new Promise((resolve, reject) => {
    execFile('gh', args, { timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024, env: env ? { ...process.env, ...env } : process.env }, (err, stdout, stderr) => {
      if (err) return reject(new Error(String(stderr || stdout || err.message).trim().slice(0, 300)))
      resolve(stdout)
    })
  })
}

// gh: async (args: string[]) => stdout. by: the site's host name (who did it, in comments and responses).
// projects: allowed project keys (projectKey). ownerOf: async (num) => GitHub login owning default-org-style
// project num (a bare number), or null. "org/number" keys always use their own org.
export function createActions({ gh = runGh, by, projects = [], ownerOf = async () => null }) {
  async function gql(query, vars = {}) {
    const args = ['api', 'graphql', '-f', `query=${query}`]
    for (const [k, v] of Object.entries(vars)) args.push(Number.isInteger(v) ? '-F' : '-f', `${k}=${v}`)
    const out = JSON.parse(await gh(args))
    if (out?.errors?.length) throw new Error(String(out.errors[0]?.message ?? 'GraphQL error').slice(0, 300))
    return out.data
  }

  async function epicLabels(url) {
    const q = JSON.parse(await gh(['issue', 'view', url, '--json', 'labels,state']))
    const labels = new Set((q?.labels ?? []).map((l) => l?.name))
    if (!labels.has('epic')) throw new ActionError('這張 issue 沒有 epic label，不是 EPIC')
    if (q?.state !== 'OPEN') throw new ActionError('這個 EPIC 已關閉')
    return labels
  }

  const comment = (url, body) => gh(['issue', 'comment', url, '--body', body])

  async function go(url, autoMerge) {
    const labels = await epicLabels(url)
    const add = [GO, autoMerge ? AUTO : null].filter((l) => l && !labels.has(l))
    if (add.length) {
      await gh(['issue', 'edit', url, ...add.flatMap((l) => ['--add-label', l])])
      await comment(url, `🚦 由 站台協作星系（${by}）放行（加 ${add.map((l) => `\`${l}\``).join('、')}）。`)
    }
    return { ok: true, by, already: !add.includes(GO), auto_merge: autoMerge }
  }

  async function rego(url) {
    if (!(await epicLabels(url)).has(GO)) throw new ActionError('這個 EPIC 還沒放行過，請用「放行」')
    await gh(['issue', 'edit', url, '--remove-label', GO])
    await gh(['issue', 'edit', url, '--add-label', GO])
    await comment(url, `↻ 由 站台協作星系（${by}）重新放行（移除再加回 \`${GO}\`）。`)
    return { ok: true, by, already: false }
  }

  async function automerge(url, on) {
    if ((await epicLabels(url)).has(AUTO) === on) return { ok: true, by, already: true, on }
    await gh(['issue', 'edit', url, on ? '--add-label' : '--remove-label', AUTO])
    await comment(url, `⚡ 由 站台協作星系（${by}）${on ? '開啟' : '關閉'}自動合併（\`${AUTO}\`）。`)
    return { ok: true, by, already: false, on }
  }

  async function move(key, url, status) {
    const { owner: own, num } = splitKey(key)
    const owner = own || (await ownerOf(num)) || url.split('/')[3]
    const field = 'projectV2(number:$n){id field(name:"Status"){... on ProjectV2SingleSelectField{id options{id name}}}}'
    let p
    try {
      const d = await gql(`query($o:String!,$n:Int!){organization(login:$o){${field}}}`, { o: owner, n: num })
      p = d?.organization?.projectV2
    } catch {}
    if (!p) p = (await gql(`query($o:String!,$n:Int!){user(login:$o){${field}}}`, { o: owner, n: num }))?.user?.projectV2
    if (!p) throw new ActionError(`找不到 project ${key}`)
    const opt = (p.field?.options ?? []).find((o) => o.name === status)
    if (!opt) throw new ActionError(`這個 project 沒有「${status}」欄`)
    // Find the project item for this issue (an issue can be in several projects).
    const it = (await gql('query($u:URI!){resource(url:$u){... on Issue{id state projectItems(first:20){nodes{id project{id}}}}}}', { u: url }))?.resource
    if (!it?.id) throw new ActionError('找不到 issue')
    const item = (it.projectItems?.nodes ?? []).find((n) => n?.project?.id === p.id)
    if (!item) throw new ActionError('這張 issue 不在這個 project')
    await gql('mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,' +
      'value:{singleSelectOptionId:$o}}){projectV2Item{id}}}', { p: p.id, i: item.id, f: p.field.id, o: opt.id })
    // 移到「完成」＝關閉 issue（不只改欄位，否則 GitHub 上仍是 open）
    if (status === DONE && it.state === 'OPEN') {
      await gql('mutation($i:ID!){closeIssue(input:{issueId:$i}){issue{state}}}', { i: it.id })
      return 'CLOSED'
    }
    return it.state
  }

  function issueUrl(body) {
    const url = typeof body?.url === 'string' ? body.url.trim() : ''
    if (!ISSUE_URL.test(url)) throw new ActionError('不是 GitHub issue 網址')
    return url
  }

  function flag(value, name) {
    if (typeof value !== 'boolean') throw new ActionError(`${name} 要是 true 或 false`)
    return value
  }

  /** kind: 'go' | 'rego' | 'automerge' | 'status'. Returns the 200 body. */
  return async function act(kind, body) {
    if (!body || typeof body !== 'object' || Array.isArray(body)) throw new ActionError('內容不是 JSON 物件')
    if (kind === 'go') return go(issueUrl(body), flag(body.auto_merge ?? false, 'auto_merge'))
    if (kind === 'rego') return rego(issueUrl(body))
    if (kind === 'automerge') return automerge(issueUrl(body), flag(body.on, 'on'))
    if (kind === 'status') {
      const key = projectKey(body.project)
      if (key === null || !projects.includes(key)) throw new ActionError('不允許的 project')
      const url = issueUrl(body)
      if (typeof body.status !== 'string' || !body.status) throw new ActionError('缺少 status')
      return { ok: true, by, state: await move(key, url, body.status) }
    }
    throw new ActionError('不認得的動作', 404)
  }
}
