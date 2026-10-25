// Build plugins/synapse/page/index.html from build/template.html + build/parse-hub.mjs.
// No network and no secrets: the page ships empty and the site fills it at runtime
// from /synapse/data/{hub,projects,hives}.json (kept fresh by lib/fetcher.js).
//   node plugins/synapse/build/build.mjs [--out page/index.html]
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d }
const OUT = arg('--out', join(here, '..', 'page', 'index.html'))
const HUB_URL = arg('--hub-url', '')  // 站台在執行時由 hub.json 的 url 提供

const parser = readFileSync(join(here, 'parse-hub.mjs'), 'utf8')
  .replace(/^import[^\n]*\n/m, '').replace(/^export\s+/gm, '').replace(/\nif \(process\.argv[\s\S]*$/, '\n')
const safe = (s) => s.replace(/<\/script/gi, '<\\/script')
const html = readFileSync(join(here, 'template.html'), 'utf8')
  .replace('__HUB_MD__', () => '')
  .replace('__PARSER__', () => safe(parser))
  .replace('__HIVES__', () => '[]')
  .replace('__SRC__', () => JSON.stringify('站台（讀取中…）'))
  .replace('__HUB_URL__', () => safe(JSON.stringify(HUB_URL)))
for (const k of ['__HUB_MD__', '__PARSER__', '__HIVES__', '__SRC__', '__HUB_URL__']) if (html.includes(k)) throw new Error('placeholder left: ' + k)
mkdirSync(dirname(OUT), { recursive: true })
writeFileSync(OUT, html)
console.log(`wrote ${OUT} (${html.length} bytes)`)
