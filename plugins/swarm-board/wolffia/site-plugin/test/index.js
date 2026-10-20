// `node --test test/` on Node 22 doesn't scan the directory: it loads `test/`
// as a module, which resolves here. Pull in every *.test.js so that form runs
// the same suite as `npm test` (node --test test/*.test.js).
import { readdirSync } from 'node:fs'

const dir = new URL('./', import.meta.url)
for (const f of readdirSync(dir).filter((n) => n.endsWith('.test.js')).sort()) {
  await import(new URL(f, dir))
}
