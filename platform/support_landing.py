"""Public landing page for the Swarm collaboration platform.

Constraints this page must respect (enforced by the platform CSP and tests):
same-origin assets only, no inline <script>, no style attributes, no event
handler attributes. The architecture diagram is inline SVG built here so its
geometry is computed, not hand-typed, and it describes the real system: the
platform (mother site), per-customer hives, and the humans around them.

The credential form keeps the exact element ids WELCOME_JS drives, so the
activation/login behaviour (invite-only account creation) is unchanged.
"""
from math import cos, pi, sin


def _hex(cx, cy, r):
    """Pointy-top hexagon points."""
    return ' '.join(f'{cx + r * cos(pi / 180 * (60 * i - 90)):.1f},'
                    f'{cy + r * sin(pi / 180 * (60 * i - 90)):.1f}' for i in range(6))


def _hex_node(cx, cy, r, title, sub, cls):
    return (f'<g class="node {cls}"><polygon points="{_hex(cx, cy, r)}"/>'
            f'<text x="{cx}" y="{cy - 4}" class="t1">{title}</text>'
            f'<text x="{cx}" y="{cy + 16}" class="t2">{sub}</text></g>')


def _diagram():
    parts = ['<svg class="sa" viewBox="0 -8 980 528" role="img" '
             'aria-labelledby="sa-title sa-desc" xmlns="http://www.w3.org/2000/svg">',
             '<title id="sa-title">Swarm 系統架構</title>',
             '<desc id="sa-desc">客戶經邀請進入平台母站；母站負責帳號、邀請、Copilot 與客服房間，'
             '並以一次性入場券把客戶送進各自獨立的 Hive 站台。管理員從母站加入客服房間。'
             '平台模型金鑰只存在母站，不進入任何 Hive。</desc>',
             '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
             'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
             'class="arrowhead"/></marker></defs>']

    # Platform (mother site) boundary.
    parts.append('<g class="zone"><rect x="300" y="40" width="380" height="370" rx="18"/>'
                 '<text x="490" y="72" class="zone-title">SWARM 母站</text>'
                 '<text x="490" y="92" class="zone-sub">平台站</text></g>')
    parts.append(_hex_node(400, 175, 58, '邀請與帳號', 'invite-only', 'core'))
    parts.append(_hex_node(580, 175, 58, 'Copilot', '平台模型', 'core'))
    parts.append(_hex_node(490, 315, 58, '客服房間', 'AI + 真人', 'core'))
    parts.append('<g class="lock"><rect x="330" y="372" width="320" height="26" rx="13"/>'
                 '<text x="490" y="390">平台金鑰只留在母站，不進入 Hive</text></g>')

    # Customer and administrator.
    parts.append(_hex_node(115, 175, 60, '客戶', '瀏覽器', 'person'))
    parts.append(_hex_node(115, 355, 60, '管理員', '/admin', 'person'))

    # Hives: each customer gets an isolated site.
    parts.append('<g class="zone hives"><rect x="760" y="40" width="200" height="440" rx="18"/>'
                 '<text x="860" y="72" class="zone-title">HIVES</text>'
                 '<text x="860" y="92" class="zone-sub">每位客戶一座蜂巢</text></g>')
    parts.append(_hex_node(860, 170, 56, 'Hive · A', '獨立站台', 'hive'))
    parts.append(_hex_node(860, 300, 56, '你的 Hive', '自己的模型', 'hive'))
    parts.append(_hex_node(860, 420, 44, '…', '', 'hive dim'))

    # Flows: (path, label, label x, label y)
    flows = [
        ('M175,175 L338,175', '邀請啟用 · 登入', 256, 162),
        ('M175,355 L428,330', '加入對話', 290, 372),
        # The platform hands the customer into their own hive with a one-time
        # ticket; drawn zone-to-zone so it never crosses the platform nodes.
        ('M638,175 L804,170', '一次性入場券', 721, 158),
        # The customer's browser works in its own hive directly (not through
        # the platform), so this flow arcs over the platform boundary.
        ('M140,122 C260,-6 740,-6 836,120', '直接在自己的 Hive 工作', 490, 20),
        ('M580,233 L532,262', '', 0, 0),
        ('M400,233 L448,262', '', 0, 0),
    ]
    for d, label, lx, ly in flows:
        parts.append(f'<path d="{d}" class="flow" marker-end="url(#arrow)"/>')
        if label:
            parts.append(f'<text x="{lx}" y="{ly}" class="flow-label">{label}</text>')
    parts.append('</svg>')
    return ''.join(parts)


def _honeycomb():
    """Decorative background; aria-hidden and pointer-transparent."""
    return ('<svg class="comb" aria-hidden="true" focusable="false" xmlns="http://www.w3.org/2000/svg">'
            '<defs><pattern id="comb" width="56" height="97" patternUnits="userSpaceOnUse" '
            'patternTransform="scale(1.1)"><path d="M28 0 L56 16 L56 48 L28 64 L0 48 L0 16 Z '
            'M28 64 L28 97 M0 48 L-28 64 M56 48 L84 64" class="comb-line"/></pattern></defs>'
            '<rect width="100%" height="100%" fill="url(#comb)"/></svg>')


def _mark(size):
    return (f'<svg class="mark" width="{size}" height="{size}" viewBox="0 0 40 40" aria-hidden="true" '
            f'focusable="false" xmlns="http://www.w3.org/2000/svg"><polygon points="{_hex(20, 20, 18)}" '
            f'class="mark-outer"/><polygon points="{_hex(20, 20, 8)}" class="mark-inner"/></svg>')


def landing_page():
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Swarm — 群體協作平台</title>
<link rel="stylesheet" href="/customer/ui/landing.css">
<script defer src="/customer/ui/welcome.js"></script></head>
<body data-role="customer" data-invite="">{_honeycomb()}
<header class="nav"><a class="brand" href="/">{_mark(28)}<span>SWARM</span></a>
<a class="nav-login" href="#access">客戶登入</a></header>
<main>
<section class="hero">
<p class="eyebrow">SWARM · COLLABORATION PLATFORM</p>
<h1>一個蜂群，<br><span class="glow">為你協作。</span></h1>
<p class="lede">Swarm 是 AI 與人的協作平台。AI 工蜂、Copilot 與真人支援在同一個空間為你工作；
每位客戶擁有一座獨立的 <strong>Hive</strong>：自己的站台、自己的模型、自己的資料。</p>
<p class="cta"><a class="btn primary" href="#access">登入工作空間</a>
<a class="btn ghost" href="#architecture">看系統架構</a></p>
</section>

<section id="architecture" class="panel">
<p class="eyebrow">SYSTEM ARCHITECTURE</p>
<h2>群在母站，巢各自獨立</h2>
<div class="diagram">{_diagram()}</div>
<p class="swipe hint">← 左右滑動查看完整架構 →</p>
<ul class="pillars">
<li><h3>母站 · The Swarm</h3><p>負責邀請、帳號、Copilot 與客服房間。平台模型的金鑰只存在這裡。</p></li>
<li><h3>蜂巢 · Hive</h3><p>每位客戶一座隔離的站台，工作資料與客戶自己的模型設定都留在自己的 Hive。</p></li>
<li><h3>協作 · Colony</h3><p>同一個房間裡，你、Copilot 與真人支援三方對話；需要時真人隨時接手。</p></li>
</ul>
</section>

<section class="panel invite">
<p class="badge">INVITE ONLY</p>
<h2>Swarm 採邀請制</h2>
<p>我們不開放自由註冊。每座 Hive 都由平台管理員為客戶建立，並透過<strong>一次性邀請連結</strong>交付。</p>
<ol class="steps">
<li><span>01</span><h3>收到邀請</h3><p>管理員寄給你一條專屬連結，限時且只能使用一次。</p></li>
<li><span>02</span><h3>設定帳號</h3><p>開啟連結、設定自己的帳號與密碼，邀請隨即失效。</p></li>
<li><span>03</span><h3>進入 Hive</h3><p>Copilot 引導你完成設定，站台建好後一鍵進入。</p></li>
</ol>
<p class="hint">沒有邀請？請聯絡為你服務的平台管理員。</p>
</section>

<section id="access" class="panel access">
<div class="access-card">
<h2 id="page-title">客戶登入</h2>
<p id="intro">已啟用的客戶，請以先前設定的帳號與密碼登入。</p>
<p id="invite-required" hidden>尚未啟用？請使用管理員提供的邀請連結。</p>
<form id="credentials" autocomplete="off">
<label for="username">帳號</label>
<input id="username" name="username" required maxlength="64" pattern="[A-Za-z0-9_.-]{{1,64}}" autocomplete="username">
<label for="password">密碼</label>
<input id="password" name="password" type="password" required minlength="8" autocomplete="current-password">
<p id="confirm-row" hidden><label for="password-confirm">再次輸入密碼</label>
<input id="password-confirm" name="password-confirm" type="password" minlength="8" autocomplete="new-password"></p>
<button id="action" type="submit" class="btn primary">登入</button></form>
<p id="status" role="status" aria-live="polite"></p>
<p class="hint">請勿在任何聊天訊息中貼上密碼或 API 金鑰。</p>
</div></section>
</main>
<footer class="foot">{_mark(18)}<span>Swarm · 群體協作平台</span></footer>
</body></html>'''


LANDING_CSS = """
:root { --bg:#07090d; --panel:rgba(16,20,28,.78); --line:#1f2733; --text:#e7ecf3;
  --muted:#8b97a8; --honey:#f5b301; --honey-2:#ffcf4a; --cyan:#3dd6f5; }
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; background: radial-gradient(1200px 600px at 70% -10%, #1a1606 0%, transparent 60%),
  radial-gradient(900px 500px at 0% 30%, #06161b 0%, transparent 60%), var(--bg);
  color: var(--text); font: 16px/1.65 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif; }
.comb { position: fixed; inset: 0; width: 100%; height: 100%; z-index: 0; pointer-events: none;
  opacity: .5; mask-image: radial-gradient(ellipse at 60% 20%, black 0%, transparent 70%);
  -webkit-mask-image: radial-gradient(ellipse at 60% 20%, black 0%, transparent 70%); }
.comb-line { fill: none; stroke: #f5b30114; stroke-width: 1; }
.nav, main, .foot { position: relative; z-index: 1; }
.nav { display: flex; justify-content: space-between; align-items: center;
  max-width: 72rem; margin: 0 auto; padding: 1.2rem 1.5rem; }
.brand { display: flex; align-items: center; gap: .6rem; color: var(--text);
  text-decoration: none; font-weight: 800; letter-spacing: .28em; }
.mark-outer { fill: none; stroke: var(--honey); stroke-width: 2.5; }
.mark-inner { fill: var(--honey); }
.nav-login { color: var(--text); text-decoration: none; border: 1px solid var(--line);
  padding: .45rem 1rem; border-radius: 999px; font-size: .92rem; }
.nav-login:hover, .nav-login:focus-visible { border-color: var(--honey); color: var(--honey-2); }
main { max-width: 72rem; margin: 0 auto; padding: 0 1.5rem 3rem; }
.hero { padding: 5rem 0 4rem; max-width: 46rem; }
.eyebrow { font: 600 .78rem/1 ui-monospace, "SF Mono", Menlo, monospace; letter-spacing: .22em;
  color: var(--cyan); margin: 0 0 1rem; }
h1 { font-size: clamp(2.6rem, 7vw, 4.6rem); line-height: 1.08; margin: 0 0 1.4rem; letter-spacing: -.01em; }
.glow { color: var(--honey); text-shadow: 0 0 32px #f5b30155; }
.lede { font-size: 1.12rem; color: #c3ccd8; max-width: 40rem; }
.lede strong { color: var(--honey-2); }
.cta { display: flex; flex-wrap: wrap; gap: .8rem; margin-top: 2rem; }
.btn { display: inline-block; font-family: inherit; font-size: 1rem; font-weight: 600; line-height: 1; padding: .9rem 1.5rem; border-radius: .6rem;
  text-decoration: none; cursor: pointer; border: 1px solid transparent; }
.btn.primary { background: linear-gradient(135deg, var(--honey), #ff9f1a); color: #1a1200;
  box-shadow: 0 8px 30px #f5b30133; }
.btn.primary:hover, .btn.primary:focus-visible { box-shadow: 0 10px 40px #f5b30166; }
.btn.ghost { color: var(--text); border-color: var(--line); background: #ffffff06; }
.btn.ghost:hover, .btn.ghost:focus-visible { border-color: var(--cyan); }
.btn:disabled { opacity: .6; cursor: wait; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 1.2rem;
  padding: 2.4rem; margin-bottom: 2rem; backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); }
h2 { font-size: clamp(1.5rem, 3.5vw, 2.1rem); margin: 0 0 1.4rem; }
h3 { margin: 0 0 .4rem; font-size: 1.05rem; }
.diagram { overflow-x: auto; margin: 0 -.5rem; }
.sa { width: 100%; min-width: 640px; height: auto; display: block; }
.sa text { text-anchor: middle; font-family: system-ui, "Noto Sans TC", sans-serif; }
.zone rect { fill: #f5b30106; stroke: #f5b30140; stroke-dasharray: 6 6; }
.zone.hives rect { fill: #3dd6f506; stroke: #3dd6f540; }
.zone-title { fill: var(--honey); font: 700 15px ui-monospace, Menlo, monospace; letter-spacing: .25em; }
.zone.hives .zone-title { fill: var(--cyan); }
.zone-sub { fill: var(--muted); font-size: 12px; }
.node polygon { stroke-width: 1.6; }
.node.core polygon { fill: #1c1705; stroke: var(--honey); filter: drop-shadow(0 0 10px #f5b30140); }
.node.hive polygon { fill: #05181d; stroke: var(--cyan); filter: drop-shadow(0 0 10px #3dd6f540); }
.node.person polygon { fill: #121821; stroke: #6c7a8e; }
.node.dim { opacity: .45; }
.t1 { fill: var(--text); font-size: 14px; font-weight: 700; }
.t2 { fill: var(--muted); font-size: 11.5px; }
.flow { fill: none; stroke: #f5b301aa; stroke-width: 1.8; stroke-dasharray: 5 7; }
.arrowhead { fill: #f5b301cc; }
.flow-label { fill: #d8c690; font-size: 12px; paint-order: stroke; stroke: var(--bg); stroke-width: 5px; }
.lock rect { fill: #0c1117; stroke: #2c3644; }
.lock text { fill: #aeb9c7; font-size: 12px; }
.pillars, .steps { list-style: none; padding: 0; margin: 2rem 0 0; display: grid; gap: 1rem;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); }
.pillars li, .steps li { border: 1px solid var(--line); border-radius: .9rem; padding: 1.2rem; background: #0b0f15; }
.pillars p, .steps p { margin: 0; color: var(--muted); font-size: .94rem; }
.pillars h3 { color: var(--honey-2); }
.badge { display: inline-block; font: 700 .75rem/1 ui-monospace, Menlo, monospace; letter-spacing: .2em;
  color: #1a1200; background: var(--honey); padding: .45rem .7rem; border-radius: .35rem; margin: 0 0 1rem; }
.invite > p:not(.badge) { color: #c3ccd8; max-width: 44rem; }
.swipe { display: none; text-align: center; margin: .4rem 0 0; }
@media (max-width: 44rem) { .swipe { display: block; } }
.steps span { font: 700 1.6rem/1 ui-monospace, Menlo, monospace; color: var(--honey); display: block; margin-bottom: .6rem; }
.hint { color: var(--muted); font-size: .88rem; }
.access { display: flex; justify-content: center; }
.access-card { width: min(26rem, 100%); }
.access-card h2 { margin-bottom: .6rem; }
#intro { color: #c3ccd8; }
#invite-required { color: var(--muted); font-size: .92rem; }
label { display: block; margin: 1rem 0 .35rem; font-size: .92rem; color: #c3ccd8; }
input { width: 100%; font: inherit; padding: .75rem .9rem; border-radius: .55rem; color: var(--text);
  background: #06090d; border: 1px solid #2a3442; }
input:focus-visible { outline: 2px solid var(--honey); outline-offset: 1px; border-color: transparent; }
#confirm-row { margin: 0; }
#action { width: 100%; margin-top: 1.5rem; }
#status { min-height: 1.5rem; color: #ffcf4a; }
.foot { display: flex; align-items: center; justify-content: center; gap: .5rem;
  color: var(--muted); font-size: .85rem; padding: 2rem 0 3rem; }
[hidden] { display: none !important; }
@media (prefers-reduced-motion: no-preference) {
  .flow { animation: flow 1.6s linear infinite; }
  @keyframes flow { to { stroke-dashoffset: -24; } }
  .node.core polygon, .node.hive polygon { animation: pulse 4s ease-in-out infinite; }
  @keyframes pulse { 50% { stroke-opacity: .55; } }
}
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
@media (max-width: 40rem) { .panel { padding: 1.5rem; } .hero { padding: 3rem 0 2.5rem; } }
"""
