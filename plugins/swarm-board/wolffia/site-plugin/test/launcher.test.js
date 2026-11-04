import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const SRC = readFileSync(new URL('../lib/launcher.js', import.meta.url), 'utf8')

// Enough of React's jsx runtime to render the sidebar link to a plain tree.
const fakeRequire = (name) => {
  assert.equal(name, 'react/jsx-runtime')
  const el = (type, props) => ({ type, props })
  return { jsx: el, jsxs: el }
}

function load(href) {
  let mod = null
  const replaced = []
  const timers = []
  const win = {
    location: { href },
    history: { state: null, replaceState: (_s, _t, url) => replaced.push(url) },
    __ModuleLoader__: { load: ({ factory }) => { mod = factory(fakeRequire) } },
  }
  const doc = { getElementById: () => null, createElement: () => ({ style: {}, addEventListener() {}, remove() {} }),
    body: { appendChild() {} }, addEventListener() {}, removeEventListener() {} }
  new Function('window', 'document', 'setTimeout', SRC)(win, doc, (f, ms) => timers.push([f, ms]))
  return { mod, replaced, timers }
}

test('client injects uiWorkspace and slots', () => {
  assert.deepEqual(load('https://x/').mod.inject, ['uiWorkspace', 'slots'])
})

// The launcher sits in the left sidebar above 「設定」 (DSH's sidebar.footer.action slot), not floating over
// the page: floating at the bottom right it covered the composer's send button and model picker.
function slots() {
  const registered = []
  return {
    registered,
    inject: (name, fn) => { registered.push({ slot: name, ...fn() }); return () => {} },
    register: (options, component) => ({ options, component }),
  }
}

test('in DSH: a sidebar row above 設定, nothing floats over the page', () => {
  const { mod } = load('https://x/')
  const s = slots()
  let floated = false
  mod.apply({ slots: s, effect: () => { floated = true }, uiWorkspace: {} })
  assert.equal(floated, false, 'no floating button when the sidebar slot exists')
  assert.equal(s.registered.length, 1)
  const [r] = s.registered
  assert.equal(r.slot, 'sidebar.footer.action')
  assert.equal(r.options.name, 'sidebar.footer.action')
  assert.equal(r.options.id, 'dsh-swarm-board-launcher')
  // Wide sidebar: icon + label; rail: icon only. Both open /board/ in a new tab.
  const wide = r.component({ wide: true })
  assert.equal(wide.type, 'a')
  assert.equal(wide.props.href, '/board/')
  assert.equal(wide.props.target, '_blank')
  assert.match(wide.props.rel, /noopener/)
  assert.equal(wide.props['aria-label'], '協作星系')
  assert.deepEqual(wide.props.children.filter(Boolean).map((c) => c.props.children), ['🌌', '協作星系'])
  assert.equal(wide.props.style.position, undefined, 'part of the sidebar layout, not fixed')
  const rail = r.component({ wide: false })
  assert.deepEqual(rail.props.children.filter(Boolean).map((c) => c.props.children), ['🌌'])
})

test('outside DSH (no slots): a small round floating icon, not a wide pill', () => {
  let made = null
  const doc = { getElementById: () => null, body: { appendChild() {} }, addEventListener() {}, removeEventListener() {},
    createElement: () => (made = { style: {}, addEventListener() {}, remove() {} }) }
  new Function('window', 'document', 'setTimeout', SRC)({}, doc, () => 0)
  assert.equal(made.style.position, 'fixed')
  assert.equal(made.textContent, '🌌')
  assert.equal(made.style.width, '36px')
  assert.equal(made.ariaLabel, '協作星系')
})

test('?swarm-session=<id> opens that Session and drops the parameter', () => {
  const { mod, replaced, timers } = load('https://site.example/?swarm-session=session-0aba7f4e-e0c6&x=1')
  const opened = []
  mod.apply({ uiWorkspace: { openSession: (id) => opened.push(id) } })
  assert.deepEqual(opened, ['session-0aba7f4e-e0c6'])
  assert.deepEqual(replaced, ['/?x=1'])
  timers.forEach(([f]) => f())
  assert.equal(opened.length, 3, 'opened again after startup restoration')
})

test('no or bad parameter: nothing opens', () => {
  for (const href of ['https://s/', 'https://s/?swarm-session=../../etc', 'https://s/?swarm-session=']) {
    const { mod, replaced } = load(href)
    const opened = []
    mod.apply({ uiWorkspace: { openSession: (id) => opened.push(id) } })
    assert.deepEqual(opened, []); assert.deepEqual(replaced, [])
  }
})
