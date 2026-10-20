import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const SRC = readFileSync(new URL('../lib/launcher.js', import.meta.url), 'utf8')

function load(href) {
  let mod = null
  const replaced = []
  const timers = []
  const win = {
    location: { href },
    history: { state: null, replaceState: (_s, _t, url) => replaced.push(url) },
    __ModuleLoader__: { load: ({ factory }) => { mod = factory() } },
  }
  const doc = { getElementById: () => null, createElement: () => ({ style: {}, addEventListener() {}, remove() {} }),
    body: { appendChild() {} }, addEventListener() {}, removeEventListener() {} }
  new Function('window', 'document', 'setTimeout', SRC)(win, doc, (f, ms) => timers.push([f, ms]))
  return { mod, replaced, timers }
}

test('client injects uiWorkspace', () => {
  assert.deepEqual(load('https://x/').mod.inject, ['uiWorkspace'])
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
