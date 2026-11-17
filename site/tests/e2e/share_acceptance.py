"""End-to-end acceptance for dsh-share-room on a real site (run on the platform host).

    python3 share_acceptance.py <site host> [--entry-script PATH] [--out DIR]

Goes through the site's real entry (a one-time ticket from the platform's site-entry-url script), with the
owner and a guest in two separate browser contexts (two users). Checks:
  0  DSH 0.2's 「預覽版說明」 dialog can be dismissed (absent on 0.1.7: passes)
  1  share-room answers           2  a conversation is created, the 🔗 share button appears
  3  share + invite link          4  guest joins with the link   5  guest cannot use the owner's API
  6  read-only share: guest sees the AI's question   7  ... and has no answer button
  8  site allows full access      9  share switched to full access
  10 guest gets an answer button  11 guest answers B, the AI continues with it ("收到 B")
  12 the room records who answered
  13 owner ends the share: guest sees it ended AND the server refuses the guest's message
  14 no JS errors on the owner page
Needs a working model on the site. Restores the site's full-access switch to what it was.
Never prints tickets, invite secrets or tokens. Exit 0 only when every check passes.
"""
import argparse, json, os, re, subprocess, sys

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("host")
ap.add_argument("--entry-script", default="/srv/sites/.swarm-platform/site-entry-url.sh")
ap.add_argument("--out", default="/tmp/share-acceptance")
ap.add_argument("--chrome", default=os.path.expanduser("~/.cache/ms-playwright/chromium-1134/chrome-linux/chrome"))
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + ((" — " + detail) if detail else ""), flush=True)
    return ok


def entry():
    return subprocess.run([a.entry_script, a.host, "/"], capture_output=True, text=True, check=True).stdout.strip()


def api(pg, path, body=None, method="POST"):
    return pg.evaluate("""async ([path, body, method]) => {
        const r = await fetch(path, method === 'GET' ? {credentials: 'same-origin'} :
            {method: 'POST', credentials: 'same-origin', headers: {'content-type': 'application/json'}, body: JSON.stringify(body || {})});
        let j = null; try { j = await r.json() } catch {}
        return {status: r.status, json: j};
    }""", [path, body, method])


def send(page, text):
    """Type into the composer and send with the send button; True when the composer emptied (the message left)."""
    box = page.locator("[contenteditable=true]:visible, textarea:visible").first
    box.click()
    box.fill(text)
    page.wait_for_timeout(300)
    btn = page.locator("button[aria-label=送出訊息], button[aria-label*=Send]")
    if btn.count():
        btn.last.click()
    else:
        page.keyboard.press("Enter")
    for _ in range(20):
        page.wait_for_timeout(250)
        if not (box.inner_text() if box.count() else "").strip():
            return True
    return False


def wait_for(page, selector, tries=60, every=2000):
    for _ in range(tries):
        if page.locator(selector).count():
            return True
        page.wait_for_timeout(every)
    return False


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=a.chrome)
    pg = b.new_context(viewport={"width": 1400, "height": 900}, locale="zh-TW").new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)[:160]))

    pg.goto(entry(), wait_until="networkidle", timeout=120000)
    pg.wait_for_timeout(3000)
    if pg.get_by_role("button", name="繼續").count():
        pg.get_by_role("button", name="繼續").first.click()
        pg.wait_for_timeout(1500)
    check("0 預覽版說明可以關閉（0.1.7 沒有這個對話框）", not pg.get_by_role("dialog").filter(has_text="預覽版說明").count())

    site = api(pg, "/api/share-room.site", method="GET")
    check("1 share-room 回應（經站台入口）", site["status"] == 200)
    was_full = bool((site["json"] or {}).get("fullAccess"))
    if (site["json"] or {}).get("enabled") is False:
        api(pg, "/api/share-room.site", {"enabled": True})

    send(pg, "請只回覆「好」一個字。")
    check("2 建立對話並出現「🔗 分享」按鈕", wait_for(pg, "[data-testid=share-room-share-button]"))
    pg.wait_for_timeout(15000)  # fork needs a completed turn
    pg.screenshot(path=a.out + "/01-owner.png")

    invite = None
    for _ in range(4):
        pg.locator("[data-testid=share-room-share-button]").first.click()
        pg.wait_for_timeout(800)
        if pg.locator("[data-share-room=owner-name]").count():
            pg.locator("[data-share-room=owner-name]").fill("測試站主")
        pg.locator("[data-share-room=guest-name]").fill("訪客甲")
        pg.locator("[data-share-room=create]").click()
        if wait_for(pg, "[data-share-room=link]", tries=30, every=500):
            invite = pg.locator("[data-share-room=link]").input_value()
            break
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(10000)
    check("3 建立分享與邀請連結", bool(invite) and "/share/" in invite)
    if not invite:
        print("RESULT %d/%d" % (sum(ok for _, ok in results), len(results)))
        sys.exit(1)
    share_id = re.search(r"/share/([^/#]+)/", invite).group(1)
    pg.keyboard.press("Escape")

    gp = b.new_context(viewport={"width": 1100, "height": 900}, locale="zh-TW").new_page()
    gp.goto(invite, wait_until="domcontentloaded", timeout=120000)
    if wait_for(gp, "[data-testid=join]", tries=10, every=500):
        gp.locator("[data-testid=join]").click()
    check("4 訪客用邀請連結加入（另一個瀏覽器身分）", wait_for(gp, "[data-testid=input]", tries=20, every=500))
    st = gp.evaluate("async () => (await fetch('/api/share-room.site', {credentials: 'same-origin'})).status")
    check("5 訪客不能用站主的 API", st != 200, "status=%s" % st)

    # In a shared session the owner's composer starts in 💬 討論 (guests see it; the AI does not answer).
    # Switch it to 🤖 問 AI first, or the question below never reaches the AI.
    mode = pg.locator("[data-share-room=mode]")
    if wait_for(pg, "[data-share-room=mode]", tries=10, every=500) and mode.first.get_attribute("data-mode") == "discuss":
        mode.first.click()
        pg.wait_for_timeout(500)
    check("6a 站主切到「🤖 問 AI」", not pg.locator("[data-share-room=mode][data-mode=discuss]").count())
    asked = send(pg, "請用 ask_user_question 工具問我一題單選：選項 A 或 B。收到答案後只回覆「收到」加上我選的字母。")
    check("6 唯讀分享：訪客看得到 AI 的提問", asked and wait_for(gp, "[data-testid=ask]"))
    check("7 唯讀分享：訪客沒有回答按鈕", gp.locator("[data-testid=answer]").count() == 0)
    gp.screenshot(path=a.out + "/02-guest-readonly-question.png")

    check("8 開啟「分享可使用完全權限」", api(pg, "/api/share-room.site", {"fullAccess": True})["status"] == 200)
    r = api(pg, "/api/share-room.permission", {"shareId": share_id, "preset": "danger-full-access", "confirm": True})
    check("9 分享切到完全權限", r["status"] == 200 and (r["json"] or {}).get("share", {}).get("aiPreset") == "danger-full-access")

    check("10 完全權限：訪客出現回答按鈕", wait_for(gp, "[data-testid=answer]", tries=30, every=1000))
    gp.screenshot(path=a.out + "/03-guest-can-answer.png")
    opts = gp.locator("[data-testid=opt]")
    for i in range(opts.count()):
        if opts.nth(i).inner_text().strip() == "B":
            opts.nth(i).click()
            break
    answered = gp.locator("[data-testid=answer]").count() > 0
    if answered:
        gp.locator("[data-testid=answer]").first.click()
    got = False
    for _ in range(60):
        gp.wait_for_timeout(2000)
        if re.search(r"收到\s*[「\"']?B", gp.locator("[data-testid=log]").inner_text()):
            got = True
            break
    check("11 訪客回答後 AI 照答案繼續（「收到 B」）", answered and got)
    log = gp.locator("[data-testid=log]").inner_text()
    check("12 房間記錄是誰回答的", "回答了 AI 的問題" in log and "訪客甲" in log)
    gp.screenshot(path=a.out + "/04-guest-answered.png")

    r = api(pg, "/api/share-room.end", {"shareId": share_id})
    gp.wait_for_timeout(3000)
    gp.reload(wait_until="domcontentloaded")  # the guest page keeps a live event stream: networkidle never settles
    gp.wait_for_timeout(2500)
    body = gp.locator("body").inner_text()
    shown = ("分享已結束" in body or "這個分享已經結束" in body) and gp.locator("[data-testid=send]:visible").count() == 0
    say = gp.evaluate("""async (sid) => {
        const r = await fetch('/share/' + sid + '/say', {method: 'POST', credentials: 'same-origin',
          headers: {'content-type': 'application/json'}, body: JSON.stringify({text: 'after end'})});
        return {status: r.status, body: (await r.text()).slice(0, 120)};
    }""", share_id)
    check("13 結束分享：訪客看到「已結束」，伺服器拒絕發言",
          r["status"] == 200 and shown and say["status"] in (401, 403, 409, 410) and "ended" in say["body"],
          "server=%s" % say["status"])
    gp.screenshot(path=a.out + "/05-guest-ended.png")

    api(pg, "/api/share-room.site", {"fullAccess": was_full})  # leave the site switch as it was
    check("14 站主頁面沒有 JS 錯誤", not errors, "; ".join(errors)[:200])
    b.close()

passed = sum(ok for _, ok in results)
print("RESULT %d/%d" % (passed, len(results)))
sys.exit(0 if passed == len(results) else 1)
