"""蜂群 orchestrator（#416，EPIC #412）。

在 EPIC runner 的契約上加上：
- **廣度**：同時派出的蜂不超過 `max_bees`，也不超過該引擎的 `per_engine` 上限；
  依賴（blocked by）沒關的不派。
- **深度**：蜂的回覆若是「停下等人」（結尾問句、要不要繼續、已完成第 N 階段…），
  在同一個 session 推一把，最多 `max_nudges` 次；每次嘗試有 `max_minutes` 上限。
- **驗收**：完成只看判準（沿用 judge.judge，程式執行、只看 exit code），不看蜂怎麼說。
  沒過就重試，`max_tries` 次都沒過標 `swarm:stuck`。
- **收成果**：每隻蜂在自己的分支 `epic/<N>-bee-<issue>` 工作；判準過了才合進 `epic/<N>`，
  合併衝突就標 `swarm:stuck`（不硬合）。全部通過開一個總 PR，EPIC 加 `epic:review`。
- **自動合併**（#465）：EPIC 帶 `swarm:auto-merge`、而且低風險四條件都成立，總 PR 開好立刻合併
  （merge commit）；任一條不成立就照舊等人。不論合不合，EPIC 都加 `epic:review` 由人驗收結案。
- **benchmark**：每次執行記錄時間、agent 總數與峰值、每張用了幾隻、推了幾次、判準結果。
- **執行紀錄**（#455）：EPIC 總結留言結尾附 `<!-- swarm-run … -->`（YAML），給 epic_summary.py 彙整。

蜂（Bee）是一個很薄的介面：start(workdir, prompt) / send(text) / stop()，回傳 Turn。
各家實作（claude CLI、codex、opencode）在 bees.py。測試用假的蜂。
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone

import yaml

from . import criteria, judge, lint, takeable

STUCK = "swarm:stuck"
RUNNING = "swarm:running"          # 蜂正在做這張（Wolffia 運行動畫，#457）
REVIEW = "epic:review"
AUTO_MERGE = "swarm:auto-merge"   # 人放行時另外加在 EPIC 上：低風險就自動合併總 PR（#465）
# 總 PR 不准碰的路徑：改到就不自動合併（不准自己放寬規則）。
# Identity of the merge commits the orchestrator makes (EPIC_GIT_EMAIL; historical default kept).
GIT_EMAIL = os.environ.get("EPIC_GIT_EMAIL", "swarm@example.com")
PROTECTED = (".github/", "scheduler/model-policy.yaml")
# GitHub Project 的 Status（#475）：看板跟著蜂走。卡住不改回；關閉交給 Project 的「Item closed」規則。
STATUS_RUNNING = "進行中 (7 天一輪)"
STATUS_REVIEW = "審核後請關閉"

DEFAULT_STOP_PATTERNS = [
    r"要不要繼續", r"是否(要)?繼續", r"需要我繼續", r"要繼續嗎", r"可以繼續嗎",
    r"(時間|今天).{0,8}(晚|不早)", r"先(到這裡|停在這|休息)",
    r"已完成.{0,10}第\s*\S{1,3}\s*(階段|步)", r"下一步(要|是否)",
    r"(?i)\b(shall|should|do you want me to|would you like me to)\b.{0,40}\b(continue|proceed|go on)\b",
    r"(?i)\blet me know (if|whether)\b",
]
NUDGE = ("繼續。這是無人值守的執行，沒有人會回覆你，也不需要確認。照計畫做到驗收判準全部通過為止；"
         "真的卡住時，寫明卡在哪裡、需要什麼，然後結束，不要用問句結束。")


class PolicyError(ValueError):
    pass


RUN_MARK = "swarm-run"
RUN_FIELDS = ("mode", "engine", "seconds", "agents", "peak", "input_tokens", "output_tokens", "cost_usd",
              "nudges", "retries", "conflicts", "passed", "stuck", "pr")


def run_record(m: dict, pr: str = "") -> str:
    """機器可讀的執行紀錄：`<!-- swarm-run\n<YAML>\n-->`。沒回報的用量寫 null，不寫 0。"""
    u = m.get("usage") or {}
    seen = set(m.get("usage_seen") or [])
    d = {"mode": m.get("mode"), "engine": m.get("engine"), "model": m.get("model") or None,
         "seconds": m.get("seconds"), "agents": m.get("agents_total"), "peak": m.get("peak_bees"),
         "input_tokens": u.get("input_tokens") if "input_tokens" in seen else None,
         "output_tokens": u.get("output_tokens") if "output_tokens" in seen else None,
         "cost_usd": u.get("cost_usd") if "cost_usd" in seen else None,
         "nudges": m.get("nudges"), "retries": m.get("retries"), "conflicts": m.get("conflicts"),
         "passed": m.get("passed"), "stuck": m.get("stuck"), "pr": pr or None,
         "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    body = yaml.safe_dump(d, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"<!-- {RUN_MARK}\n{body}-->"


@dataclass
class Turn:
    text: str
    timed_out: bool = False
    usage: dict = field(default_factory=dict)     # {"input_tokens","output_tokens","cost_usd"} when the CLI reports it


@dataclass
class Config:
    max_bees: int = 2
    per_engine: dict = field(default_factory=lambda: {"claude": 2, "codex": 2, "opencode": 2})
    max_minutes: int = 30
    max_nudges: int = 3
    max_tries: int = 3
    stop_patterns: list = field(default_factory=lambda: list(DEFAULT_STOP_PATTERNS))
    check_timeout: int = 900

    @classmethod
    def load(cls, path: str) -> "Config":
        d = yaml.safe_load(open(path, encoding="utf-8")) or {}
        s, dp = d.get("swarm") or {}, d.get("depth") or {}
        return cls(max_bees=int(s.get("max_bees", 2)), per_engine=dict(s.get("per_engine") or {}),
                   max_minutes=int(dp.get("max_minutes", 30)), max_nudges=int(dp.get("max_nudges", 3)),
                   max_tries=int(dp.get("max_tries", 3)),
                   stop_patterns=list(dp.get("stop_patterns") or DEFAULT_STOP_PATTERNS),
                   check_timeout=int(dp.get("check_timeout", 900)))


@dataclass
class Policy:
    """哪些引擎、模型可以用在無人值守執行（model-policy.yaml）。"""
    allowed: set = field(default_factory=set)
    deny_prefixes: list = field(default_factory=list)

    @classmethod
    def load(cls, path: str) -> "Policy":
        d = yaml.safe_load(open(path, encoding="utf-8")) or {}
        auto = d.get("automation") or {}
        return cls(allowed=set(auto.get("engines") or []), deny_prefixes=list(auto.get("deny_models") or []))

    def check(self, engine: str, model: str) -> None:
        if engine not in self.allowed:
            raise PolicyError(f"引擎 {engine} 不在 model-policy 的允許清單（{sorted(self.allowed)}）")
        for p in self.deny_prefixes:
            if (model or "").startswith(p):
                raise PolicyError(f"模型 {model} 被 model-policy 禁止（{p}*）")


def is_stop(text: str, patterns) -> bool:
    """蜂是不是停下來等人：最後一段是問句，或出現停下的句型。"""
    t = (text or "").strip()
    if not t:
        return False
    tail = t[-300:]
    if any(re.search(p, tail) for p in patterns):
        return True
    last = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    return bool(last) and last[-1].rstrip("*_` ").endswith(("？", "?"))


@dataclass
class Report:
    done: list = field(default_factory=list)
    stuck: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    review: bool = False
    pr: str = ""
    metrics: dict = field(default_factory=dict)
    metrics_path: str = ""


def _git(d, *a, check=True):
    r = subprocess.run(["git", "-C", d, *a], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(a[:2])}: {r.stderr.strip()[:300]}")
    return r


class Orchestrator:
    def __init__(self, gh, *, epic: int, repo_dir: str, base: str, cfg: Config, policy: Policy,
                 engine: str, model: str, bee_factory, state_dir: str, push: bool = True,
                 open_pr=None, env: dict | None = None, repo: str = "",
                 pr_mergeable=None, merge_pr=None, comment_pr=None):
        policy.check(engine, model)           # before any GitHub write
        self.gh, self.epic_n, self.repo_dir, self.base = gh, epic, repo_dir, base
        self.cfg, self.engine, self.model = cfg, engine, model
        self.bee_factory, self.state_dir, self.push = bee_factory, state_dir, push
        self.open_pr, self.env, self.repo = open_pr, dict(env or {}), repo
        # 自動合併用（#465）：pr_mergeable(url)->True/False/None、merge_pr(url)->(ok, 訊息)、comment_pr(url, body)
        self.pr_mergeable, self.merge_pr, self.comment_pr = pr_mergeable, merge_pr, comment_pr
        self.branch = f"epic/{epic}"
        self.lock = threading.Lock()          # git refs + metrics
        self.live = 0
        self.m = {"engine": engine, "model": model, "agents_total": 0, "peak_bees": 0, "nudges": 0,
                  "retries": 0, "per_issue": {}, "conflicts": 0, "mode": "swarm",
                  "auto_merged": False, "auto_merge_reasons": [],
                  "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}, "usage_seen": []}

    # -- workspace -------------------------------------------------------------

    def _prepare(self):
        os.makedirs(self.state_dir, exist_ok=True)
        if _git(self.repo_dir, "rev-parse", "--verify", "-q", self.branch, check=False).returncode != 0:
            _git(self.repo_dir, "branch", self.branch, self.base)

    def _bee_tree(self, n: int) -> tuple[str, str]:
        wt = os.path.join(self.state_dir, "wt", f"epic{self.epic_n}-bee-{n}")
        br = f"{self.branch}-bee-{n}"
        with self.lock:
            if os.path.isdir(wt):
                _git(self.repo_dir, "worktree", "remove", "--force", wt, check=False)
            _git(self.repo_dir, "branch", "-f", br, self.branch)
            _git(self.repo_dir, "worktree", "add", "-q", wt, br)
        return wt, br

    def _commit(self, wt: str, n: int):
        if _git(wt, "status", "--porcelain").stdout.strip():
            _git(wt, "add", "-A")
            _git(wt, "-c", "user.name=swarm", "-c", f"user.email={GIT_EMAIL}",
                 "commit", "-q", "-m", f"[epic#{self.epic_n}] #{n} 蜂的成果")

    def _merge(self, br: str, n: int) -> tuple[bool, str]:
        """把蜂的分支合進 EPIC 分支；衝突就放棄，回傳 (ok, 訊息)。"""
        with self.lock:
            tmp = os.path.join(self.state_dir, "wt", f"epic{self.epic_n}-merge")
            if os.path.isdir(tmp):
                _git(self.repo_dir, "worktree", "remove", "--force", tmp, check=False)
            _git(self.repo_dir, "worktree", "add", "-q", tmp, self.branch)
            try:
                r = _git(tmp, "-c", "user.name=swarm", "-c", f"user.email={GIT_EMAIL}",
                         "merge", "--no-ff", "-q", "-m", f"[epic#{self.epic_n}] 合併 #{n}", br, check=False)
                if r.returncode != 0:
                    files = _git(tmp, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
                    _git(tmp, "merge", "--abort", check=False)
                    return False, "、".join(files) or r.stderr.strip()[:200]
                return True, ""
            finally:
                _git(self.repo_dir, "worktree", "remove", "--force", tmp, check=False)

    # -- swarm:running ---------------------------------------------------------

    def _mark_running(self, n: int):
        """開工：加 swarm:running，留言引擎與開始時間。"""
        self.gh.add_label(n, RUNNING)
        self._set_status(n, STATUS_RUNNING)
        self.gh.comment(n, f"🐝 開工：引擎 {self.engine}，{datetime.now(timezone.utc).isoformat(timespec='seconds')}")

    def _mark_epic_running(self, epic):
        """第一隻蜂開工時 EPIC 也改成「進行中」；一輪只呼叫一次，已經 epic:review 的不改。"""
        if REVIEW in epic.get("labels", []):
            return
        self._set_status(self.epic_n, STATUS_RUNNING)

    def _set_status(self, n: int, name: str):
        """改 Project 的 Status；失敗（權限不足、沒有 project…）只記一行警告，不影響開工、判準與 review。"""
        try:
            self.gh.set_status(n, name)
        except Exception as exc:
            print(f"⚠️ #{n} 設定 Project Status「{name}」失敗：{type(exc).__name__}: {str(exc)[:200]}", flush=True)

    def _unmark_running(self, n: int):
        """結束（通過、卡住、出錯）一律移除 swarm:running；移除失敗不能蓋掉這張的結果。"""
        try:
            self.gh.remove_label(n, RUNNING)
        except Exception:
            pass

    # -- one bee ---------------------------------------------------------------

    def _prompt(self, epic, it, prev=None) -> str:
        cmds = criteria.parse(it.get("body", "")).commands
        lines = [f"你負責 EPIC #{self.epic_n}「{epic.get('title', '')}」底下的 #{it['number']}「{it.get('title', '')}」。",
                 "這是無人值守的執行：沒有人會回覆你。不要問要不要繼續，做到下面的驗收判準全部通過為止。",
                 "只能修改這張 issue「可以修改的範圍」裡的檔案。不要 push，不要開 PR，不要改判準。", "",
                 "## Issue 內容", it.get("body", ""), "", "## 驗收判準（runner 會照原文執行，只看 exit code）"]
        lines += [f"- `{c}`" for c in cmds]
        if prev:
            lines += ["", "## 上一次嘗試沒通過", prev[-4000:]]
        return "\n".join(lines)

    def _try_once(self, epic, it, wt, prev) -> Turn:
        n = it["number"]
        bee = self.bee_factory(n)
        with self.lock:
            self.live += 1
            self.m["agents_total"] += 1
            self.m["peak_bees"] = max(self.m["peak_bees"], self.live)
            pi = self.m["per_issue"].setdefault(n, {"agents": 0, "tries": 0, "nudges": 0, "seconds": 0})
            pi["agents"] += 1
        deadline = time.monotonic() + self.cfg.max_minutes * 60
        try:
            turn = bee.start(wt, self._prompt(epic, it, prev))
            self._add_usage(turn)
            nudges = 0
            while is_stop(turn.text, self.cfg.stop_patterns) and nudges < self.cfg.max_nudges \
                    and time.monotonic() < deadline and not turn.timed_out:
                nudges += 1
                with self.lock:
                    self.m["nudges"] += 1
                    self.m["per_issue"][n]["nudges"] += 1
                turn = bee.send(NUDGE)
                self._add_usage(turn)
            return turn
        finally:
            bee.stop()
            with self.lock:
                self.live -= 1

    def _add_usage(self, turn):
        with self.lock:
            for k in ("input_tokens", "output_tokens", "cost_usd"):
                v = (turn.usage or {}).get(k)
                if isinstance(v, (int, float)):
                    self.m["usage"][k] = round(self.m["usage"][k] + v, 6)
                    if k not in self.m["usage_seen"]:
                        self.m["usage_seen"].append(k)

    def _work(self, epic, it) -> str:
        n, prev = it["number"], None
        snap = criteria.snapshot(it)
        start = time.monotonic()
        wt = None
        try:
            self._mark_running(n)
            wt, br = self._bee_tree(n)
            for i in range(1, self.cfg.max_tries + 1):
                with self.lock:
                    self.m["per_issue"].setdefault(n, {"agents": 0, "tries": 0, "nudges": 0, "seconds": 0})
                    self.m["per_issue"][n]["tries"] = i
                    if i > 1:
                        self.m["retries"] += 1
                turn = self._try_once(epic, it, wt, prev)
                self._commit(wt, n)
                cur = self.gh.issue(n)
                if not criteria.same(cur, snap):
                    self.gh.comment(n, "判準在執行中被改過：停止，等人重新放行。")
                    self.gh.add_label(n, STUCK)
                    return "stuck"
                res = self._judge_in(wt, epic, cur, snap["commands"], close=False)
                if res.ok:
                    ok, why = self._merge(br, n)
                    if not ok:
                        with self.lock:
                            self.m["conflicts"] += 1
                        self.gh.comment(n, f"判準通過，但合併進 `{self.branch}` 時衝突（{why}）。標 `{STUCK}`，"
                                           f"不硬合；成果保留在分支 `{br}`，由人處理。")
                        self.gh.add_label(n, STUCK)
                        return "stuck"
                    self.gh.close(n)
                    return "done"
                note = "（蜂逾時）" if turn.timed_out else ""
                prev = f"第 {i} 次嘗試的判準輸出{note}：\n{res.summary}"
            self.gh.comment(n, f"試了 {self.cfg.max_tries} 次判準都沒通過，標 `{STUCK}`，由人決定下一步。")
            self.gh.add_label(n, STUCK)
            return "stuck"
        finally:
            with self.lock:
                self.m["per_issue"].setdefault(n, {"agents": 0, "tries": 0, "nudges": 0, "seconds": 0})
                self.m["per_issue"][n]["seconds"] = round(time.monotonic() - start, 1)
            if wt:
                with self.lock:
                    _git(self.repo_dir, "worktree", "remove", "--force", wt, check=False)
            self._unmark_running(n)

    def _judge_in(self, wt, epic, issue, commands, close):
        """judge.judge 會關 issue；合併成功前不能關，所以這裡先不關。"""
        gh = self.gh

        class NoClose:
            def __getattr__(self, k):
                return getattr(gh, k)

            def close(self, n):
                pass
        e = dict(self.env)
        return judge.judge(NoClose() if not close else gh, epic, issue, wt, go=self.base, repo=self.repo,
                           commands=commands, env=e, timeout=self.cfg.check_timeout)

    # -- single mode (A/B group A): one session does the whole EPIC ---------------

    def run_single(self) -> Report:
        """A 組：一個 session 一次做完整個 EPIC（不推一把、不重試），之後由 runner 逐張跑判準。

        可接的 sub-issue 依依賴順序全部交給同一隻蜂，在 `epic/<N>` 上直接工作；
        結束後對每張跑判準，過的關閉，沒過的標 swarm:stuck。全部通過開總 PR。
        """
        rep = Report()
        t0 = time.monotonic()
        self.m["mode"] = "single"
        epic = self.gh.issue(self.epic_n)
        if not epic.get("go_at"):
            raise ValueError(f"EPIC #{self.epic_n} 沒有 swarm:go：還沒放行")
        self._prepare()
        subs = self.gh.subs(self.epic_n)
        todo = [s for s in subs if s["state"] == "open" and "by:swarm" in s.get("labels", [])
                and "swarm:stuck" not in s.get("labels", [])]
        snaps = {s["number"]: criteria.snapshot(s) for s in todo}
        self.gh.comment(self.epic_n, f"## 單一 session 開始（orchestrator --single）\n\n引擎 `{self.engine}`，模型 `{self.model}`；"
                                     f"{len(todo)} 張交給同一個 session。")
        wt = os.path.join(self.state_dir, "wt", f"epic{self.epic_n}-single")
        try:
            if todo:
                self._mark_epic_running(epic)
            for s in todo:
                self._mark_running(s["number"])
            if os.path.isdir(wt):
                _git(self.repo_dir, "worktree", "remove", "--force", wt, check=False)
            _git(self.repo_dir, "worktree", "add", "-q", wt, self.branch)
            parts = [f"你負責 EPIC #{self.epic_n}「{epic.get('title', '')}」底下的全部工作，請一次做完。",
                     "這是無人值守的執行：沒有人會回覆你。依照依賴順序做完每一張，做到每張的驗收判準都通過為止。",
                     "每張只能修改它「可以修改的範圍」裡的檔案。不要 push，不要開 PR，不要改判準。", ""]
            for s in todo:
                parts += [f"# #{s['number']}「{s.get('title', '')}」", s.get("body", ""), ""]
            bee = self.bee_factory(0)
            self.m["agents_total"] = 1
            self.m["peak_bees"] = 1
            try:
                turn = bee.start(wt, "\n".join(parts))
                self._add_usage(turn)
            finally:
                bee.stop()
            self._commit(wt, 0)
            for s in todo:
                n = s["number"]
                cur = self.gh.issue(n)
                if not criteria.same(cur, snaps[n]):
                    self.gh.comment(n, "判準在執行中被改過：停止，等人重新放行。")
                    self.gh.add_label(n, STUCK)
                    rep.stuck.append(n)
                    continue
                res = self._judge_in(wt, epic, cur, snaps[n]["commands"], close=True)
                self.m["per_issue"][n] = {"agents": 1, "tries": 1, "nudges": 0, "seconds": 0}
                if res.ok:
                    rep.done.append(n)
                else:
                    self.gh.add_label(n, STUCK)
                    rep.stuck.append(n)
        finally:
            _git(self.repo_dir, "worktree", "remove", "--force", wt, check=False)
            for s in todo:
                self._unmark_running(s["number"])
        return self._finish(rep, t0, epic)

    # -- the round -------------------------------------------------------------

    def run(self) -> Report:
        rep = Report()
        t0 = time.monotonic()
        epic = self.gh.issue(self.epic_n)
        if not epic.get("go_at"):
            raise ValueError(f"EPIC #{self.epic_n} 沒有 swarm:go：還沒放行")
        self._prepare()
        self.gh.comment(self.epic_n, f"## 蜂群開始（orchestrator）\n\n引擎 `{self.engine}`，模型 `{self.model}`；"
                                     f"同時最多 {self._limit()} 隻。")
        started: set[int] = set()
        running = {}
        limit = self._limit()
        with ThreadPoolExecutor(max_workers=limit) as pool:
            while True:
                epic = self.gh.issue(self.epic_n)
                take, skipped = takeable.pick(epic, self.gh.subs(self.epic_n))
                take = [t for t in take if t["number"] not in started]
                while take and len(running) < limit:
                    it = take.pop(0)
                    if not started:
                        self._mark_epic_running(epic)
                    started.add(it["number"])
                    running[pool.submit(self._work, epic, it)] = it["number"]
                if not running:
                    rep.skipped = {k: v for k, v in skipped.items() if k not in started}
                    break
                finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for f in finished:
                    n = running.pop(f)
                    try:
                        out = f.result()
                    except Exception as exc:          # one bee's crash never stops the swarm
                        self.gh.comment(n, f"orchestrator 執行這張時出錯：{type(exc).__name__}: {exc}。標 `{STUCK}`。")
                        self.gh.add_label(n, STUCK)
                        out = "stuck"
                    (rep.done if out == "done" else rep.stuck).append(n)
        return self._finish(rep, t0, epic)

    def _finish(self, rep, t0, epic) -> Report:
        self.m["seconds"] = round(time.monotonic() - t0, 1)
        self.m["passed"] = len(rep.done)
        self.m["stuck"] = len(rep.stuck)
        subs = self.gh.subs(self.epic_n)
        allclosed = bool(subs) and all(s["state"] == "closed" for s in subs)
        rep.metrics = self.m
        if allclosed:
            if self.open_pr is not None:
                rep.pr = self.open_pr(self.branch, f"[epic#{self.epic_n}] {epic.get('title', '')}"[:250],
                                      self._pr_body(subs, rep))
            self._auto_merge(epic, subs, rep)
            self.gh.add_label(self.epic_n, REVIEW)          # 自動合併了也一樣：入口與出口由人
            self._set_status(self.epic_n, STATUS_REVIEW)
            rep.review = True
        else:
            self.m["auto_merge_reasons"] = ["sub-issue 沒有全部關閉，沒有開總 PR"]
        rep.metrics_path = self._save_metrics()
        # 總結留言＋機器可讀的執行紀錄（#455）；自動合併的結果已在 metrics 裡
        self.gh.comment(self.epic_n, self._summary(subs, rep) + "\n\n" + run_record(self.m, rep.pr))
        return rep

    # -- auto-merge (#465) -----------------------------------------------------

    def _changed_files(self) -> list[str]:
        """總 PR 改到的檔案：EPIC 分支相對於 base 的分岔點（改名的新舊路徑都算）。"""
        out = _git(self.repo_dir, "diff", "--name-only", "--no-renames", f"{self.base}...{self.branch}").stdout
        return sorted({f for f in out.splitlines() if f.strip()})

    @staticmethod
    def _in_scope(path: str, globs) -> bool:
        for g in globs:
            g = g.strip()
            g = g[2:] if g.startswith("./") else g
            if not g:
                continue
            if fnmatch.fnmatchcase(path, g):
                return True
            d = g[:-3] if g.endswith("/**") else g[:-1] if g.endswith("/") else None
            if d and path.startswith(d + "/"):
                return True
        return False

    def auto_merge_blockers(self, subs, pr: str) -> list[str]:
        """四條件裡不成立的（空 = 可以自動合併）。只看條件，不寫 GitHub。"""
        why = []
        # 1. 每張都由 swarm 做完並通過判準
        for s in subs:
            labels = s.get("labels", [])
            if "by:swarm" not in labels:
                why.append(f"#{s['number']} 不是由 swarm 做的（沒有 `by:swarm`）")
            elif STUCK in labels:
                why.append(f"#{s['number']} 有 `{STUCK}`")
            elif s.get("state") != "closed":
                why.append(f"#{s['number']} 還沒通過判準（未關閉）")
        # 2. 改到的檔案都在某張 sub-issue 的「可以修改的範圍」裡；3. 沒碰 .github／model-policy.yaml
        try:
            files = self._changed_files()
        except Exception as exc:
            files = None
            why.append(f"無法取得總 PR 改到的檔案：{exc}")
        globs = [g for s in subs for g in lint.scope_paths(s.get("body", ""))]
        for f in files or []:
            if any(f == p or (p.endswith("/") and f.startswith(p)) for p in PROTECTED):
                why.append(f"改到受保護的 `{f}`（不准自己放寬規則）")
            elif not self._in_scope(f, globs):
                why.append(f"`{f}` 不在任何 sub-issue 的「可以修改的範圍」裡")
        # 4. GitHub 回報可合併
        if not (pr or "").startswith("http"):
            why.append(f"沒有總 PR（{pr or '未開'}）")
        elif self.pr_mergeable is None or self.merge_pr is None:
            why.append("沒有設定 GitHub 合併介面，無法確認可合併")
        else:
            try:
                ok = self.pr_mergeable(pr)
            except Exception as exc:
                ok, why_m = None, f"：{type(exc).__name__}: {str(exc)[:200]}"
            else:
                why_m = ""
            if ok is not True:
                why.append("GitHub 回報總 PR 不可合併（有衝突）" if ok is False else f"GitHub 無法確認總 PR 可合併{why_m}")
        return why

    def _auto_merge(self, epic, subs, rep) -> None:
        """EPIC 帶 swarm:auto-merge 且四條件都成立 → 立刻合併總 PR；否則留言原因、照舊等人。"""
        if AUTO_MERGE not in (epic.get("labels") or []):
            self.m["auto_merge_reasons"] = [f"EPIC 沒有 `{AUTO_MERGE}`，等人合併"]
            return
        why = self.auto_merge_blockers(subs, rep.pr)
        if not why:
            try:
                ok, msg = self.merge_pr(rep.pr)
            except Exception as exc:
                ok, msg = False, f"{type(exc).__name__}: {str(exc)[:200]}"
            if not ok:
                why = [f"合併失敗：{msg}"]
        if why:
            self.m["auto_merge_reasons"] = why
            self.gh.comment(self.epic_n, f"## 不自動合併（`{AUTO_MERGE}`）\n\n總 PR {rep.pr or '（未開）'} 不符合自動合併條件，"
                                         "照舊等人合併：\n\n" + "\n".join(f"- {w}" for w in why))
            return
        reasons = ["每張 sub-issue 都由 swarm 做完並通過判準（沒有 `swarm:stuck`）",
                   "總 PR 改到的每個檔案都在某張 sub-issue 的「可以修改的範圍」裡",
                   "沒有改到 `.github/` 與 `scheduler/model-policy.yaml`",
                   "GitHub 回報總 PR 可合併（沒有衝突）"]
        self.m["auto_merged"] = True
        self.m["auto_merge_reasons"] = reasons
        body = (f"## 自動合併（`{AUTO_MERGE}`）\n\nEPIC #{self.epic_n} 帶 `{AUTO_MERGE}`，低風險條件全部成立，"
                f"orchestrator 已自動合併總 PR（merge commit）：\n\n" + "\n".join(f"- {r}" for r in reasons) +
                f"\n\nEPIC 仍加 `{REVIEW}`，由人驗收結案。")
        if self.comment_pr is not None:
            try:
                self.comment_pr(rep.pr, body)
            except Exception as exc:
                print(f"⚠️ 總 PR 留言失敗：{type(exc).__name__}: {str(exc)[:200]}", flush=True)
        self.gh.comment(self.epic_n, body.replace("已自動合併總 PR", f"已自動合併總 PR {rep.pr}"))

    def _limit(self) -> int:
        return max(1, min(self.cfg.max_bees, int(self.cfg.per_engine.get(self.engine, self.cfg.max_bees))))

    def _save_metrics(self) -> str:
        d = os.path.join(self.state_dir, "metrics")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"epic{self.epic_n}-{time.strftime('%Y%m%d-%H%M%S')}-{self.m.get('mode')}-{self.engine}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.m, fh, ensure_ascii=False, indent=2, default=str)
        return path

    def _metrics_table(self) -> str:
        m = self.m
        u = m.get("usage") or {}
        rows = [("模式", m.get("mode")), ("引擎／模型", f"{m['engine']} / {m['model']}"), ("時間（秒）", m.get("seconds")),
                ("用量（輸入／輸出 tokens）", f"{u.get('input_tokens', 0)} / {u.get('output_tokens', 0)}"),
                ("費用估計（USD，CLI 回報）", u.get("cost_usd", 0)),
                ("agent 個數（總數）", m["agents_total"]), ("同時最多（峰值）", m["peak_bees"]),
                ("推一把次數", m["nudges"]), ("重試次數", m["retries"]), ("合併衝突", m["conflicts"]),
                ("判準通過／卡住", f"{m.get('passed', 0)} / {m.get('stuck', 0)}")]
        if m.get("auto_merge_reasons"):         # 總 PR 開之前還沒決定，PR 內文不放這列
            rows.append(("自動合併（auto_merged）",
                         f"{str(bool(m.get('auto_merged'))).lower()}：{'；'.join(m['auto_merge_reasons'])}"))
        out = ["| 指標 | 值 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows]
        out += ["", "| sub-issue | agent 個數 | 嘗試 | 推一把 | 秒 |", "|---|---|---|---|---|"]
        out += [f"| #{n} | {p['agents']} | {p['tries']} | {p['nudges']} | {p['seconds']} |"
                for n, p in sorted(m["per_issue"].items())]
        return "\n".join(out)

    def _summary(self, subs, rep) -> str:
        lines = ["## 蜂群這一輪結束（orchestrator）", "", self._metrics_table(), "",
                 "| sub-issue | 狀態 | 這一輪 |", "|---|---|---|"]
        for s in subs:
            n = s["number"]
            now = "通過並合併" if n in rep.done else STUCK if n in rep.stuck else rep.skipped.get(n, "—")
            lines.append(f"| #{n} {s.get('title', '')[:60]} | {s['state']} | {now} |")
        return "\n".join(lines)

    def _pr_body(self, subs, rep) -> str:
        return "\n".join([f"EPIC #{self.epic_n} 的總 PR（orchestrator 建立）。合併與結案由人做。", "",
                          "## benchmark（agent 個數、時間、推一把…）", "", self._metrics_table(), "",
                          "## sub-issue", ""] + [f"- #{s['number']} {s.get('title', '')}" for s in subs] +
                         ["", f"Refs #{self.epic_n}"])
