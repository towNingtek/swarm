// The 🧠 策略中樞 launcher sits in DSH's left sidebar above 「設定」 (sidebar.footer.action), not floating
// over the page: floating at the bottom right it covered the composer's send button and model picker.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const SRC = readFileSync(new URL('../lib/launcher.js', import.meta.url), 'utf8')

const fakeRequire = (name) => {
  assert.equal(name, 'react/jsx-runtime')
  const el = (type, props) => ({ type, props })
  return { jsx: el, jsxs: el }
}

function load() {
  let mod = null
  const doc = { getElementById: () => null, createElement: () => ({ style: {}, addEventListener() {}, remove() {} }),
    body: { appendChild() {} }, addEventListener() {}, removeEventListener() {} }
  new Function('window', 'document', SRC)({ __ModuleLoader__: { load: ({ id, factory }) => { assert.equal(id, 'dsh-synapse'); mod = factory(fakeRequire) } } }, doc)
  return mod
}

test('in DSH: a sidebar row above 設定, nothing floats over the page', () => {
  const mod = load()
  assert.deepEqual(mod.inject, ['slots'])
  const registered = []
  const slots = { inject: (name, fn) => { registered.push({ slot: name, ...fn() }) }, register: (options, component) => ({ options, component }) }
  let floated = false
  mod.apply({ slots, effect: () => { floated = true } })
  assert.equal(floated, false)
  assert.equal(registered.length, 1)
  const [r] = registered
  assert.equal(r.slot, 'sidebar.footer.action')
  assert.equal(r.options.id, 'dsh-synapse-launcher')
  const wide = r.component({ wide: true })
  assert.equal(wide.props.href, '/synapse/')
  assert.equal(wide.props.target, '_blank')
  assert.match(wide.props.rel, /noopener/)
  assert.deepEqual(wide.props.children.filter(Boolean).map((c) => c.props.children), ['🧠', '策略中樞'])
  assert.equal(wide.props.style.position, undefined, 'part of the sidebar layout, not fixed')
  assert.deepEqual(r.component({ wide: false }).props.children.filter(Boolean).map((c) => c.props.children), ['🧠'])
})

test('a DSH client without slots falls back to the floating icon; it is removed on dispose', () => {
  const mod = load()
  const children = []
  const doc = { getElementById: () => null, addEventListener() {}, removeEventListener() {},
    body: { appendChild: (a) => children.push(a) },
    createElement: () => ({ style: {}, addEventListener() {}, remove() { children.splice(children.indexOf(this), 1) } }) }
  let mod2
  new Function('window', 'document', SRC)({ __ModuleLoader__: { load: ({ factory }) => { mod2 = factory(fakeRequire) } } }, doc)
  const disposers = []
  mod2.apply({ effect: (fn) => disposers.push(fn()) })
  assert.equal(children.length, 1)
  assert.equal(children[0].textContent, '🧠')
  assert.equal(children[0].style.width, '36px')
  disposers.forEach((d) => d())
  assert.equal(children.length, 0)
  assert.ok(mod)
})

test('outside DSH (no module loader): a small round floating icon', () => {
  let made = null
  const doc = { getElementById: () => null, body: { appendChild() {} }, addEventListener() {}, removeEventListener() {},
    createElement: () => (made = { style: {}, addEventListener() {}, remove() {} }) }
  new Function('window', 'document', SRC)({}, doc)
  assert.equal(made.style.position, 'fixed')
  assert.equal(made.textContent, '🧠')
  assert.equal(made.ariaLabel, '策略中樞')
  assert.equal(made.href, '/synapse/')
})
