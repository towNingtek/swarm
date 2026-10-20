// Render docs/diagrams/*.mmd to PNG (light and dark, 2x) with Mermaid in headless Chromium.
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'
const dir = process.argv[2]
const b = await chromium.launch()
for (const theme of ['default', 'dark']) {
  const bg = theme === 'dark' ? '#0d1117' : '#ffffff'
  const p = await b.newPage({ viewport: { width: 2400, height: 1600 }, deviceScaleFactor: 2 })
  await p.setContent(`<html><body style="margin:0;background:${bg}"><div id="o" style="display:inline-block;padding:24px;background:${bg}"></div><script type="module">import m from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs"; window.M=m; window.ready=true;</script></body></html>`)
  await p.waitForFunction(() => window.ready, null, { timeout: 30000 })
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.mmd'))) {
    const src = fs.readFileSync(path.join(dir, f), 'utf8')
    await p.evaluate(async ({ src, theme }) => {
      window.M.initialize({ startOnLoad: false, theme, securityLevel: 'strict',
        fontFamily: '"Noto Sans TC", "Noto Sans SC", "Noto Sans CJK TC", "PingFang TC", sans-serif' })
      const { svg } = await window.M.render('g' + Math.random().toString(36).slice(2), src)
      document.getElementById('o').innerHTML = svg
      const s = document.querySelector('#o svg'); const vb = s.viewBox.baseVal; s.removeAttribute('style'); s.setAttribute('width', Math.ceil(vb.width)); s.setAttribute('height', Math.ceil(vb.height))
    }, { src, theme })
    await p.waitForTimeout(300)
    const out = path.join(dir, f.replace(/\.mmd$/, theme === 'dark' ? '.dark.png' : '.light.png'))
    await p.locator('#o').screenshot({ path: out })
    console.log('wrote', path.basename(out))
  }
  await p.close()
}
await b.close()
