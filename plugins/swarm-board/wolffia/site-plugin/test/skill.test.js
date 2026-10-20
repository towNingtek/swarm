// 站台層級的 epic skill: installSkill against temp dirs; apply installs it into $DSH_HOME/skills/epic on start.
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import * as plugin from '../lib/index.js'
import { BUNDLED_SKILLS, defaultSkillsDir, installSkill } from '../lib/skill.js'

const tmp = () => mkdtempSync(join(tmpdir(), 'swarm-skill-'))
const FILES = ['SKILL.md', 'templates/epic.md', 'templates/sub-issue.md', 'lint/epic_lint.py',
  'lint/epiclib/__init__.py', 'lint/epiclib/lint.py', 'lint/epiclib/criteria.py']

function withDshHome(home, fn) {
  const before = process.env.DSH_HOME
  process.env.DSH_HOME = home
  try { return fn() } finally {
    if (before === undefined) delete process.env.DSH_HOME
    else process.env.DSH_HOME = before
  }
}

function fakeCtx() {
  const logs = []
  return {
    logs,
    ctx: {
      logger: () => ({ warn: (...a) => logs.push(['warn', ...a]), info: (...a) => logs.push(['info', ...a]), error() {} }),
      effect: (fn) => { fn() },
      connection: {},
      typertGateway: {},
      webServer: { register: () => () => {} },
    },
  }
}

test('the package bundles skills/epic with SKILL.md, templates and the offline lint', () => {
  for (const f of FILES) assert.ok(existsSync(join(BUNDLED_SKILLS, 'epic', f)), f)
  const skill = readFileSync(join(BUNDLED_SKILLS, 'epic', 'SKILL.md'), 'utf8')
  assert.match(skill, /^---\nname: epic\n/)
  assert.doesNotMatch(skill, /docs\/skills\/epic\/SKILL\.md|scheduler\/epic_lint\.py|docs\/templates\//)
  assert.match(skill, /lint\/epic_lint\.py --file/)
  const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
  assert.ok(pkg.files.includes('skills/'), 'skills/ ships with the package')
})

test('installSkill writes when missing, skips when the same, updates when different', () => {
  const to = tmp()
  assert.equal(installSkill({ to }), 'installed')
  for (const f of FILES) {
    assert.deepEqual(readFileSync(join(to, 'epic', f)), readFileSync(join(BUNDLED_SKILLS, 'epic', f)), f)
  }
  const before = readFileSync(join(to, 'epic', 'SKILL.md'))
  assert.equal(installSkill({ to }), 'unchanged')
  assert.deepEqual(readFileSync(join(to, 'epic', 'SKILL.md')), before)

  writeFileSync(join(to, 'epic', 'SKILL.md'), 'old version')
  writeFileSync(join(to, 'epic', 'stale.txt'), 'left over')
  assert.equal(installSkill({ to }), 'updated')
  assert.deepEqual(readFileSync(join(to, 'epic', 'SKILL.md')), before)
  assert.equal(existsSync(join(to, 'epic', 'stale.txt')), false)
  // no temp or backup directories left beside it
  assert.deepEqual(readdirSync(to), ['epic'])
})

test('a missing file in the installed copy counts as different', () => {
  const to = tmp()
  installSkill({ to })
  writeFileSync(join(to, 'epic', 'lint', 'epiclib', 'lint.py'), '')
  assert.equal(installSkill({ to }), 'updated')
  assert.ok(readFileSync(join(to, 'epic', 'lint', 'epiclib', 'lint.py'), 'utf8').includes('def lint('))
})

test('python bytecode caches in the installed copy are ignored', () => {
  const to = tmp()
  installSkill({ to })
  mkdirSync(join(to, 'epic', 'lint', 'epiclib', '__pycache__'))
  writeFileSync(join(to, 'epic', 'lint', 'epiclib', '__pycache__', 'lint.cpython-312.pyc'), 'x')
  assert.equal(installSkill({ to }), 'unchanged')
})

test('default skills dir is $DSH_HOME/skills', () => {
  assert.equal(withDshHome('/x/dsh-home', defaultSkillsDir), '/x/dsh-home/skills')
})

test('apply installs skills/epic into $DSH_HOME/skills/epic on start (missing → write, same → no write, different → update)', () => {
  const home = tmp()
  withDshHome(home, () => {
    const dst = join(home, 'skills', 'epic', 'SKILL.md')
    const want = readFileSync(join(BUNDLED_SKILLS, 'epic', 'SKILL.md'))
    const results = []
    const hooks = () => ({
      skillsDir: defaultSkillsDir(),
      installSkill: (opts) => { const r = installSkill(opts); results.push(r); return r },
    })
    const dataDir = join(home, 'data')
    let f = fakeCtx()
    plugin.apply(f.ctx, { dataDir, digestEveryMs: 0 }, hooks())
    assert.deepEqual(readFileSync(dst), want)
    assert.ok(f.logs.some((l) => l[0] === 'info' && l.includes('installed')))

    f = fakeCtx()
    plugin.apply(f.ctx, { dataDir, digestEveryMs: 0 }, hooks())
    assert.equal(f.logs.filter((l) => String(l[1]).includes('epic skill')).length, 0)

    writeFileSync(dst, 'old')
    plugin.apply(fakeCtx().ctx, { dataDir, digestEveryMs: 0 }, hooks())
    assert.deepEqual(readFileSync(dst), want)
    assert.deepEqual(results, ['installed', 'unchanged', 'updated'])
  })
})

test('config.skillsDir picks the target; an install failure is logged, not thrown', () => {
  const to = tmp()
  plugin.apply(fakeCtx().ctx, { dataDir: join(to, 'data'), digestEveryMs: 0, skillsDir: join(to, 'sk') })
  assert.ok(existsSync(join(to, 'sk', 'epic', 'SKILL.md')))
  const f = fakeCtx()
  plugin.apply(f.ctx, { dataDir: join(to, 'data'), digestEveryMs: 0 }, {
    skillsDir: join(to, 'x'), installSkill: () => { throw new Error('disk full') },
  })
  assert.ok(f.logs.some((l) => l[0] === 'warn' && l.includes('disk full')))
})

test('under node --test, apply without a skillsDir does not touch the real DSH home', () => {
  const home = tmp()
  withDshHome(home, () => plugin.apply(fakeCtx().ctx, { dataDir: join(home, 'data'), digestEveryMs: 0 }))
  assert.equal(existsSync(join(home, 'skills')), false)
})
