"""Compose demo.mp4 and demo.gif from the clips that demo/record.mjs made.

Usage: python3 demo/compose.py <clips dir> <output dir>

The clips dir holds v1..v5/<one>.webm and marks.json (seconds into each clip
at which something happened). Cuts and captions follow the marks, so a slow
site start does not leave a loading screen under a caption.
Needs ffmpeg and a CJK font (WenQuanYi Zen Hei or Noto Sans CJK).
"""
import glob
import json
import os
import subprocess
import sys

SRC, OUT = sys.argv[1], sys.argv[2]
FONT = next((f for f in ['/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
                         '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'] if os.path.exists(f)), None)
if FONT is None:
    sys.exit('no CJK font found')
with open(os.path.join(SRC, 'marks.json'), encoding='utf-8') as f:
    M = json.load(f)


def m(clip, label, shift=0.0):
    return round(M[clip][label] + shift, 2)


# (clip, start, end, [(clip time, caption[, 'top'])], captions at top?)
# Captions sit at the bottom unless the bottom holds what the caption is about.
SEGMENTS = [
    ('v1', m('v1', 'create', -0.3), m('v1', 'end', 0.5), [
        (m('v1', 'create'), '營運者在 /admin 建立新客戶'),
        (m('v1', 'template'), '選辦公室樣板、設定每月模型額度'),
        (m('v1', 'build'), '排入建站：平台會建立一個隔離的 DSH 容器', 'top'),
        (m('v1', 'invite'), '產生一次性邀請連結，交給客戶')], False),
    ('v2', m('v2', 'activate', -0.3), m('v2', 'end', 0.3), [
        (m('v2', 'activate'), '客戶打開邀請連結，自己設定帳號密碼'),
        (m('v2', 'onboarding'), '平台首頁：設定進度、建站狀態、設定助手')], False),
    # The site-entry hand-over and DSH's plugin loader are cut out.
    ('v3', m('v3', 'ready', -0.5), m('v3', 'enter', -0.1), [
        (m('v3', 'ready', -0.5), '站台建好了，一鍵進入，不需要另一組密碼'),
        (m('v3', 'copilot'), '設定助手依平台記錄的實際狀態說明下一步')], False),
    ('v3', m('v3', 'site', -0.2), m('v3', 'site', 1.5), [
        (m('v3', 'site', -0.2), '進入自己的 DSH 站台，辦公室範本已經就位')], False),
    ('v4', m('v4', 'site', 1.0), m('v4', 'answered', 2.0), [
        (m('v4', 'site', 1.0), '在自己的站台裡交代工作'),
        (m('v4', 'send'), '模型呼叫經過平台的 relay：平台金鑰不進站台，用量計入每月額度')], True),
    ('v5', m('v5', 'overview', -0.3), m('v5', 'end'), [
        (m('v5', 'overview', -0.3), '營運者一覽每位客戶：站台、額度政策、樣板')], True),
]

work = os.path.join(OUT, '.compose')
os.makedirs(work, exist_ok=True)
parts = []
for i, (clip, start, end, captions, top) in enumerate(SEGMENTS):
    src = glob.glob(os.path.join(SRC, clip, '*.webm'))[0]
    filters = ['fps=25', 'scale=1280:800']
    for j, (at, text, *where) in enumerate(captions):
        a = max(0.0, at - start)
        b = (captions[j + 1][0] - start) if j + 1 < len(captions) else end - start
        text_file = os.path.join(work, f'cap_{i}_{j}.txt')
        with open(text_file, 'w', encoding='utf-8') as f:
            f.write(text)
        y = '40' if top or where == ['top'] else 'h-90'
        filters.append(f"drawtext=fontfile={FONT}:textfile={text_file}:fontsize=30:fontcolor=white:"
                       f"box=1:boxcolor=black@0.72:boxborderw=14:x=(w-text_w)/2:y={y}:"
                       f"enable='gte(t,{a:.2f})*lt(t,{b:.2f})'")
    part = os.path.join(work, f'part{i}.mp4')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(start), '-to', str(end), '-i', src,
                    '-vf', ','.join(filters), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '22', '-an', part],
                   check=True)
    parts.append(part)

listing = os.path.join(work, 'list.txt')
with open(listing, 'w') as f:
    f.writelines(f"file '{os.path.abspath(p)}'\n" for p in parts)
mp4, gif = os.path.join(OUT, 'demo.mp4'), os.path.join(OUT, 'demo.gif')
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', listing, '-c:v', 'libx264',
                '-crf', '26', '-preset', 'slow', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4], check=True)
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', mp4, '-vf',
                'fps=7,scale=760:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];'
                '[b][p]paletteuse=dither=bayer:bayer_scale=4', gif], check=True)
print(mp4, gif)
