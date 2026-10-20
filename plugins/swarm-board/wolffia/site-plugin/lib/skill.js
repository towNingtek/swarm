// 站台層級的 skill: this package ships skills/epic/ (SKILL.md, templates, offline lint);
// on start it is copied to $DSH_HOME/skills/epic/ so the site's DSH sessions (分析對話) can use
// the `epic` skill without a swarm checkout.
//
// Written only when the target is missing or differs from the bundled copy. The new copy is
// built in a temp directory beside the target and renamed into place, so a reader never sees
// a half-written skill.
import { randomUUID } from 'node:crypto'
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const BUNDLED_SKILLS = fileURLToPath(new URL('../skills/', import.meta.url))
export const defaultSkillsDir = () => join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'skills')

// Bytecode caches and the like are not part of the skill.
const skip = (name) => name === '__pycache__' || name.endsWith('.pyc')

/** Relative path -> contents of every file under dir; null when dir is missing. */
function snapshot(dir) {
  if (!existsSync(dir)) return null
  const files = new Map()
  const walk = (rel) => {
    for (const name of readdirSync(join(dir, rel)).sort()) {
      if (skip(name)) continue
      const r = rel ? `${rel}/${name}` : name
      if (statSync(join(dir, r)).isDirectory()) walk(r)
      else files.set(r, readFileSync(join(dir, r)))
    }
  }
  walk('')
  return files
}

function same(a, b) {
  if (!a || !b || a.size !== b.size) return false
  for (const [k, v] of a) if (!b.get(k)?.equals(v)) return false
  return true
}

/**
 * Install skill `name` from `from` (the bundled skills dir) into `to` (the DSH skills dir).
 * Returns 'installed' (was missing), 'updated' (differed), or 'unchanged'.
 */
export function installSkill({ name = 'epic', from = BUNDLED_SKILLS, to = defaultSkillsDir() } = {}) {
  const src = join(from, name)
  const dst = join(to, name)
  const want = snapshot(src)
  if (!want) throw new Error(`bundled skill not found: ${src}`)
  const have = snapshot(dst)
  if (same(want, have)) return 'unchanged'

  const tag = `${process.pid}.${randomUUID()}`
  const tmp = join(to, `.${name}.${tag}.tmp`)
  const old = join(to, `.${name}.${tag}.old`)
  try {
    mkdirSync(to, { recursive: true })
    cpSync(src, tmp, { recursive: true, filter: (p) => !skip(p.split(/[\\/]/).pop()) })
    if (have) renameSync(dst, old)
    try {
      renameSync(tmp, dst)
    } catch (error) {
      if (have) renameSync(old, dst)
      throw error
    }
  } finally {
    rmSync(tmp, { recursive: true, force: true })
    rmSync(old, { recursive: true, force: true })
  }
  return have ? 'updated' : 'installed'
}
