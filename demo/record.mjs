// Records the demo clips (v1..v5) and screenshots for docs/media/.
// Runs against a THROWAWAY install only: it creates a customer and a site.
//
//   OUT=demo-out ADMIN_PASSWORD=... HOST_IP=10.250.7.2 \
//   PLAYWRIGHT_FROM=/path/to/node_modules/ node demo/record.mjs
//
// HOST_IP is where *.example.com resolves for the browser (the throwaway
// host). The invite link and the customer password live only in memory,
// and the invite field only ever paints a masked copy of the link.
import { createRequire } from 'node:module'
import { mkdirSync, writeFileSync } from 'node:fs'
import { randomBytes } from 'node:crypto'
import { join } from 'node:path'

const require = createRequire(process.env.PLAYWRIGHT_FROM ?? import.meta.url)
const { chromium } = require('playwright-core')
const OUT = process.env.OUT ?? 'demo-out'
const DOMAIN = process.env.SWARM_DOMAIN ?? 'example.com'
const P = `https://platform.${DOMAIN}`
const SITE = process.env.DEMO_SITE ?? 'cafe'
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD
if (!ADMIN_PASSWORD || !process.env.HOST_IP) throw new Error('set ADMIN_PASSWORD and HOST_IP')
const CUSTOMER_PASSWORD = randomBytes(18).toString('base64url')
mkdirSync(OUT, { recursive: true })
// Seconds into each clip at which something happens; compose.py cuts and
// captions by these, since load times differ between runs.
const marks = {}
let clipStart = 0
const mark = (clipName, label) => { (marks[clipName] ??= {})[label] = (Date.now() - clipStart) / 1000 }

const browser = await chromium.launch({
  args: [`--host-resolver-rules=MAP *.${DOMAIN} ${process.env.HOST_IP}, MAP ${DOMAIN} ${process.env.HOST_IP}`, '--lang=zh-TW'],
})
const VIEW = { width: 1280, height: 800 }
async function clip (name, state) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: VIEW, locale: 'zh-TW', storageState: state,
    recordVideo: { dir: join(OUT, name), size: VIEW } })
  const page = await ctx.newPage()
  clipStart = Date.now()
  page.on('dialog', (d) => d.accept())
  await page.addInitScript(() => {
    const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
    Object.defineProperty(HTMLInputElement.prototype, 'value', {
      configurable: true,
      get () { return d.get.call(this) },
      set (v) {
        if (this.id === 'invite-link' && /token=/.test(v)) { window.__invite = v; v = v.replace(/token=.*/, 'token=••••••••••••••••••••') }
        d.set.call(this, v)
      },
    })
  })
  return { ctx, page }
}
const shot = (target, file) => target.screenshot({ path: join(OUT, file) })
const type = async (page, sel, text, delay = 90) => { await page.click(sel); await page.keyboard.type(text, { delay }) }

// Admin session, not recorded.
let admin
{
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: VIEW })
  const page = await ctx.newPage()
  await page.goto(P + '/admin/login'); await page.fill('input[type=password]', ADMIN_PASSWORD); await page.click('button')
  await page.waitForURL(/\/admin\/customers/)
  admin = await ctx.storageState(); await ctx.close()
}

// v1: the operator creates the customer and sends an invite.
let invite
{
  const { ctx, page: p } = await clip('v1', admin)
  await p.goto(P + '/admin/customers'); await p.waitForTimeout(1200)
  mark('v1', 'create'); await type(p, '#bootstrap-host', SITE); await p.waitForTimeout(800)
  await shot(p.locator('#bootstrap-form'), '01-admin-create.png')
  await p.click('#bootstrap-submit'); await p.waitForTimeout(1500)
  const center = (sel) => p.locator(sel).evaluate((e) => e.scrollIntoView({ block: 'center' }))
  mark('v1', 'template'); await center('#template-form'); await p.waitForTimeout(500)
  await p.selectOption('#template', { index: 1 }); await p.waitForTimeout(500)
  await p.click('#template-submit'); await p.waitForTimeout(600)
  await center('#quota-form'); await p.waitForTimeout(500)
  await p.selectOption('#policy', 'capped'); await type(p, '#limit', '2000000', 60); await p.waitForTimeout(400)
  await p.click('#quota-submit'); await p.waitForTimeout(800)
  await center('#site-job-section'); await p.waitForTimeout(500)
  mark('v1', 'build'); await p.click('#site-job-submit'); await p.waitForTimeout(1300)
  await p.waitForTimeout(600)
  await shot(p.locator('#site-job-section'), '02-admin-site-job.png')
  await p.locator('#invite-form').scrollIntoViewIfNeeded(); await p.waitForTimeout(600)
  mark('v1', 'invite'); await p.click('#invite-submit'); await p.waitForTimeout(1200)
  invite = await p.evaluate(() => window.__invite)
  await p.waitForTimeout(1000)
  await shot(p.locator('#invite-link').locator('xpath=ancestor::section'), '03-admin-invite.png')
  mark('v1', 'end'); await ctx.close()
}

// v2: the customer activates and sees the site being built.
let customer
{
  const { ctx, page: p } = await clip('v2')
  await p.goto(invite); await p.waitForTimeout(800)
  mark('v2', 'activate'); await p.locator('#username').scrollIntoViewIfNeeded()
  await type(p, '#username', 'mei', 110)
  await type(p, '#password', CUSTOMER_PASSWORD, 15)
  await type(p, '#password-confirm', CUSTOMER_PASSWORD, 15)
  await p.waitForTimeout(500); await shot(p, '04-activate.png')
  await p.click('button:has-text("建立帳號並啟用")')
  await p.waitForURL(/onboarding/, { timeout: 30000 }); await p.waitForTimeout(1500)
  mark('v2', 'onboarding'); await shot(p, '05-onboarding.png')
  if (await p.isVisible('#acknowledge')) {
    await p.locator('#acknowledge').scrollIntoViewIfNeeded(); await p.waitForTimeout(800)
    await p.click('#acknowledge'); await p.waitForTimeout(1200)
  }
  mark('v2', 'end'); customer = await ctx.storageState(); await ctx.close()
}

// Wait for the build without recording it.
{
  // Poll from a page: the browser has the host mapping, Node's resolver does not.
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, storageState: customer })
  const page = await ctx.newPage(); await page.goto(P + '/customer/onboarding')
  const deadline = Date.now() + 15 * 60_000
  for (;;) {
    const st = await page.evaluate(async () => (await fetch('/customer/onboarding/status', { credentials: 'same-origin' })).json())
    const job = st.site_job ?? {}
    if (job.provisioned) break
    if (/fail|reconcil/.test(job.status ?? '') || Date.now() > deadline) throw new Error('site build: ' + job.status)
    await new Promise((r) => setTimeout(r, 10_000))
  }
  await ctx.close()
}

// v3: the site is ready; the setup copilot; entering the site.
{
  const { ctx, page: p } = await clip('v3', customer)
  await p.goto(P + '/customer/onboarding'); await p.waitForTimeout(1800)
  mark('v3', 'ready'); await shot(p, '06-site-ready.png')
  await p.waitForTimeout(2000)
  mark('v3', 'copilot'); await p.click('#copilot-launcher'); await p.waitForTimeout(2500)
  await shot(p, '07-copilot.png')
  await p.click('#copilot-close').catch(() => {}); await p.waitForTimeout(500)
  // The entry button navigates this tab (a form POST hands over a single-use ticket).
  mark('v3', 'enter'); await p.click('#site-enter')
  const site = p
  await site.waitForURL((u) => u.hostname === `${SITE}.${DOMAIN}`, { timeout: 60000 })
  // DSH shows "Loading plugins" first; the sidebar means it is ready.
  await site.getByText('新對話').first().waitFor({ timeout: 60000 }); mark('v3', 'site')
  await site.waitForTimeout(2500)
  await shot(site, '08-site.png'); mark('v3', 'end')
  customer = await ctx.storageState(); await ctx.close()
}

// v4: working in the site; the model call goes through the platform relay.
{
  const { ctx, page: p } = await clip('v4', customer)
  await p.goto(`https://${SITE}.${DOMAIN}/`)
  await p.getByText('新對話').first().waitFor({ timeout: 60000 }); mark('v4', 'site')
  await p.waitForTimeout(1200)
  await p.locator('textarea, [contenteditable=true]').first().click()
  await p.keyboard.type('帶我完成新手任務：開一間咖啡店公司', { delay: 120 })
  await p.waitForTimeout(600); mark('v4', 'send'); await p.keyboard.press('Enter')
  await p.getByText(/用量 \d+ tok/).first().waitFor({ timeout: 60000 }); await p.waitForTimeout(2500)
  mark('v4', 'answered'); await shot(p, '09-site-chat.png')
  await p.waitForTimeout(1500)
  await ctx.close()
}

// v5: the operator's overview.
{
  const { ctx, page: p } = await clip('v5', admin)
  await p.goto(P + '/admin/customers'); await p.waitForTimeout(1500)
  mark('v5', 'overview'); await p.locator('#tenant-list').evaluate((e) => e.scrollIntoView({ block: 'start' })); await p.waitForTimeout(1500)
  await shot(p, '10-admin-overview.png')
  await p.waitForTimeout(2500); mark('v5', 'end')
  await ctx.close()
}
writeFileSync(join(OUT, 'marks.json'), JSON.stringify(marks, null, 2))
await browser.close()
console.log('recorded into', OUT)
