"""What a scheduled run hands back, and which models it may use.

Scheduled runs are unattended. Two things differ from a chat in the site:

* **Model.** A site's chat default may be a personal subscription (for example
  a Claude subscription), which must not be used unattended and has no adapter
  in headless DSH anyway. ``SCHEDULE_MODELS`` (platform setting) lists, in
  order, the ``provider/model`` ids scheduled runs may use; the first that
  answers wins. Each id must be a provider the site's web profile already
  declares (for example ``nvidia/deepseek-ai/deepseek-v4.1-flash``). Empty
  means "the site's own default", as before.
* **Answer.** Models write working notes around the result ("I'll read ...",
  "已更新 WORKING.md", a closing report after ``---``). The run log keeps only
  the deliverable; a run whose cleaned answer is too short counts as failed.

Pure functions; the scheduler does the I/O.
"""
from __future__ import annotations

import re

_MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,40}/[A-Za-z0-9][A-Za-z0-9._:/@-]{0,120}$')
MIN_DELIVERABLE_CHARS = 40
THINK_END = '</think>'


def schedule_models(value):
    """``SCHEDULE_MODELS`` -> [(provider, model)], malformed entries dropped."""
    out = []
    for item in (value or '').replace('\n', ',').split(','):
        item = item.strip()
        if _MODEL.match(item):
            provider, model = item.split('/', 1)
            if (provider, model) not in out:
                out.append((provider, model))
    return out


def model_overlay(provider, model):
    """A DSH patch overlay that sets the agent default model for one run only."""
    for v in (provider, model):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@-]{0,160}', v):
            raise ValueError('invalid model id')
    return ("# scheduled run only (support_schedule_answer)\n"
            "- id: agent-default-model\n"
            "  name: '@deepseek-ai/dsh-agent-default-model'\n"
            "  config:\n"
            f"    provider: '{provider}'\n"
            f"    model: '{model}'\n")


_NARRATION_PREFIXES = (
    "我是 ", "我將 ", "我會 ", "我先 ", "我來 ",
    "現在執行", "現在開始", "接下來", "首先", "先依", "先讀", "先確認", "先檢查",
    "依照醒來流程", "依醒來流程", "按照醒來流程",
    "已讀取", "已確認", "已檢查", "已完成讀取", "已同步",
    "讀取完成", "核心身份已", "改為直接",
    "讓我", "好的，", "收到，",
    "Let me ", "Let's ", "I'll ", "I will ", "I'm going to ", "I need to ",
    "Now let me ", "Now I ", "Next, ", "First, ", "Then I ", "Looking at ",
    "Good!", "Great!", "Perfect!", "Ah-ha!", "Ah, ", "Hmm, ", "Interesting!",
    "I see ", "Got it", "Okay, ", "OK, ",
)
_NARRATION_SUFFIXES = ("：", ":")
_PROCESS_VERBS = ("執行", "讀取", "檢查", "確認", "掃描", "抓取", "呼叫",
                  "改用", "改為", "重試", "修正", "略過", "跳過", "醒來流程", "skill", "changelog", "token")
_PROCESS_SECTIONS = (
    "過程說明", "執行過程", "執行說明", "處理過程", "執行摘要", "執行結果",
    "已完成", "完成項目", "資料處理", "檔案產出", "進度追蹤", "搜尋結果",
    "紀錄更新", "記錄更新", "工作記憶", "更新紀錄", "本地紀錄",
    "技術備忘", "除錯", "排除障礙", "內部備註", "備註", "自我檢查", "後續步驟", "統計",
    "Summary", "Execution", "Completed", "Results", "Statistics", "Next steps", "Notes", "Appendix",
)
_UPDATE_DONE = (
    re.compile(r"^(?:已更新|更新了|已完成更新|已更新完成|更新完畢|已完成).{0,60}(?:更新)?(?:完成|完畢|好了)?[。\.]?$"),
    re.compile(r"^更新[^\n。]{0,80}(?:完成|完畢|好了|已儲存)[。\.]?$"),
)
_EN_NOTE = re.compile(
    r"^(?:let me|let's|i'll|i will|i'm going to|i need to|i am going to|now (?:let me|i|i'll|"
    r"updating|writing|creating|running|checking|reading|generating|producing|output)|"
    r"next,|first,|then i|verification done|done\.|ok(?:ay)?[,.]|alright|"
    r"now the |the mandatory |running the |starting )\b", re.IGNORECASE)
# A closing report after a horizontal rule ("---" then "已完成 … 兩項完成條件均達成").
_REPORT_AFTER_RULE = re.compile(
    r"\n[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*\n+[ \t]*(?:已完成|完成|本次|執行結果|本地詳細紀錄|補充說明|"
    r"Done|Completed|Summary)[\s\S]*\Z")


def _english_note(line):
    return len(line) <= 220 and bool(_EN_NOTE.match(line)) and not re.search(r"[\u4e00-\u9fff]", line)


def strip_narration(text):
    kept, in_process = [], False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip().strip("*").strip()
            in_process = any(s in heading for s in _PROCESS_SECTIONS)
            if in_process:
                continue
        elif in_process:
            continue
        if not line:
            kept.append(raw)
            continue
        by_prefix = (line.startswith(_NARRATION_PREFIXES) and len(line) <= 120
                     and (line.endswith(_NARRATION_SUFFIXES) or len(line) <= 60))
        by_colon_verb = (line.endswith(_NARRATION_SUFFIXES) and len(line) <= 120
                         and any(v in line for v in _PROCESS_VERBS))
        by_update = len(line) <= 120 and any(rx.match(line) for rx in _UPDATE_DONE)
        if by_prefix or by_colon_verb or by_update or _english_note(line):
            continue
        kept.append(raw)
    return "\n".join(kept).strip()


def deliverable(text):
    """(answer, reject_reason). An empty reason means the answer is usable."""
    if text and THINK_END in text:
        text = text.rsplit(THINK_END, 1)[1]       # keep what follows the last </think>
    if not text or not text.strip():
        return "", "輸出為空"
    text = _REPORT_AFTER_RULE.sub("", text)
    cleaned = strip_narration(text)
    if not cleaned:
        return "", "全部都是過程敘述，沒有實質內容"
    if len(cleaned) < MIN_DELIVERABLE_CHARS:
        return "", f"實質內容太短（{len(cleaned)} 字）"
    return cleaned, ""
