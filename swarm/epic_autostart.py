#!/usr/bin/env python3
"""cron：已放行（swarm:go）且有可接 sub-issue 的 EPIC，自動在背景啟動蜂群 orchestrator（#474）。

  */10 * * * *  python3 swarm/epic_autostart.py --repo acme/swarm --engine claude

先用一次 GraphQL 查詢（`gh.go_epics()`）取得 open、帶 epic 與 swarm:go、沒有 epic:review 的 EPIC，
連同每張 sub-issue 的狀態與 labels 摘要（#484）。對每個 EPIC：
  1. 摘要裡沒有「open、帶 by:swarm、沒有 swarm:stuck」的 sub-issue 就直接略過，不逐張查；
     有才逐張查這些候選、用 `epiclib.takeable.pick` 看有沒有可接的；沒有就略過。
     摘要被查詢上限截斷（`truncated`：sub-issue > 50、labels > 10 等，見 `Q_GO_EPICS` 註解，#488）時，
     這個 EPIC 退回逐張檢查：重查 EPIC 本身與它全部的 sub-issue，不靠摘要篩選，不漏判。
  2. 用 `epiclib/round` 同一把檔案鎖（`<STATE>/locks/epic-<N>.lock`）判斷是否在跑；拿不到鎖＝在跑，略過。
  3. 否則在背景啟動 `epic_run.py --repo R --epic N --swarm --engine E`（start_new_session），
     輸出寫到 `~/.local/share/swarm-epic/autostart/epic<N>.log`，立刻返回不等它。
     鎖的 fd 交給子行程繼承：子行程活著鎖就一直被拿著，結束時自動釋放，下次 cron 不會重複啟動。

每次執行在 stdout 印一行摘要（含本次 GraphQL 呼叫次數）。--dry-run：只印不啟動。

開始前先問 `gh_budget.check()`（#485）：GitHub API 剩餘額度 < 20% 就整輪不檢查，
印「額度偏低，跳過到 <resetAt>」（並發一次 Discord 通知）；查不到額度就照常執行。
"""
from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# gh_budget lives with the board plugin in this repository.
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "swarm-board", "scheduler"))

STATE = os.path.expanduser("~/.local/share/swarm-epic")
LOCK_DIR = os.path.join(STATE, "locks")          # 與 epic_run.py 傳給 round.run_round 的相同
LOG_DIR = os.path.join(STATE, "autostart")
EPIC_RUN = os.path.join(HERE, "epic_run.py")


def try_lock(lock_dir: str, n: int):
    """拿 EPIC #n 的鎖（與 epiclib/round.run_round 同一個檔案、同一種 flock）。拿到回傳 fd，在跑回傳 None。"""
    os.makedirs(lock_dir, exist_ok=True)
    fd = os.open(os.path.join(lock_dir, f"epic-{n}.lock"), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def launch(cmd: list[str], log_path: str, lock_fd: int) -> subprocess.Popen:
    """在背景啟動，不等。子行程繼承鎖的 fd，活多久鎖就拿多久。"""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "ab") as log:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True, pass_fds=(lock_fd,))


def command(repo: str, n: int, engine: str, max_bees: int | None, script: str = EPIC_RUN) -> list[str]:
    cmd = [sys.executable, script, "--repo", repo, "--epic", str(n), "--swarm", "--engine", engine]
    if max_bees:
        cmd += ["--max-bees", str(max_bees)]
    return cmd


def candidates(summary: dict) -> list[int]:
    """摘要裡值得逐張檢查的 sub-issue：open、帶 by:swarm、沒有 swarm:stuck。"""
    return [s["number"] for s in summary.get("subs", [])
            if s.get("state") == "open" and "by:swarm" in s.get("labels", [])
            and "swarm:stuck" not in s.get("labels", [])]


def run(gh, repo: str, engine: str = "claude", max_bees: int | None = None, dry_run: bool = False,
        lock_dir: str = LOCK_DIR, log_dir: str = LOG_DIR, launcher=launch, script: str = EPIC_RUN) -> dict:
    """回傳 {"checked": int, "started": [n], "skipped": {n: 原因}, "calls": 本次 GraphQL 呼叫次數}。"""
    from epiclib import takeable

    calls0 = getattr(gh, "gql_calls", 0)
    started, skipped, checked = [], {}, 0
    for e in gh.go_epics():
        n = e["number"]
        if e.get("state") != "open":
            continue
        checked += 1
        if e.get("truncated"):
            # 摘要被查詢上限截斷（#488：sub-issue > 50、labels > 10…）：這個 EPIC 退回逐張檢查，不漏判
            epic = gh.issue(n)
            take, _ = takeable.pick(epic, [gh.issue(k) for k in epic["subs"]])
            if not take:
                skipped[n] = "沒有可接的 sub-issue（逐張檢查）"
                continue
        elif "swarm:go" not in e.get("labels", []) or not e.get("go_at"):
            skipped[n] = "沒有 swarm:go"
            continue
        else:
            cand = candidates(e)
            if not cand:
                skipped[n] = "沒有 open、by:swarm 且未 stuck 的 sub-issue"
                continue
            epic = {"number": n, "state": e["state"], "labels": e["labels"], "go_at": e["go_at"]}
            take, _ = takeable.pick(epic, [gh.issue(k) for k in cand])
            if not take:
                skipped[n] = "沒有可接的 sub-issue"
                continue
        fd = try_lock(lock_dir, n)
        if fd is None:
            skipped[n] = "已經在跑"
            continue
        try:
            if not dry_run:
                launcher(command(repo, n, engine, max_bees, script), os.path.join(log_dir, f"epic{n}.log"), fd)
            started.append(n)
        finally:
            os.close(fd)     # 父行程放掉自己的那份；子行程繼承的那份繼續拿著鎖
    calls = getattr(gh, "gql_calls", 0) - calls0
    return {"checked": checked, "started": started, "skipped": skipped, "calls": calls}


def summary(rep: dict, dry_run: bool = False) -> str:
    verb = "會啟動" if dry_run else "啟動"
    s = f"{'[dry-run] ' if dry_run else ''}檢查 {rep['checked']} 個 EPIC；"
    s += f"{verb} " + ("、".join(f"#{n}" for n in rep["started"]) or "無")
    s += "；略過 " + ("、".join(f"#{n}（{why}）" for n, why in rep["skipped"].items()) or "無")
    s += f"；GraphQL 呼叫 {rep.get('calls', 0)} 次"
    return s


def main(argv=None, gh=None, launcher=launch, budget=None):
    """budget：回傳 gh_budget.check() 形狀的函式。沒給時，用真的 GitHub 才查真的額度（注入假 gh 的測試不查）。"""
    ap = argparse.ArgumentParser(description="已放行且有可接 sub-issue 的 EPIC，自動在背景啟動蜂群 orchestrator。")
    ap.add_argument("--repo", required=True, help="owner/name，例如 acme/swarm")
    ap.add_argument("--engine", choices=["claude", "codex", "opencode"], default="claude", help="用哪種蜂（預設 claude）")
    ap.add_argument("--max-bees", type=int, help="同時最多幾隻（傳給 epic_run.py）")
    ap.add_argument("--dry-run", action="store_true", help="只印不啟動")
    ap.add_argument("--lock-dir", default=LOCK_DIR, help=argparse.SUPPRESS)
    ap.add_argument("--log-dir", default=LOG_DIR, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if budget is None and gh is None:
        import gh_budget
        budget = lambda: gh_budget.check(who="epic_autostart")
    if budget is not None:
        b = budget()
        if b.get("low"):
            print(f"額度偏低，跳過到 {b.get('reset_at')}", flush=True)
            return 0
    if gh is None:
        from epiclib.github import GitHub
        gh = GitHub(a.repo)
    rep = run(gh, a.repo, a.engine, a.max_bees, a.dry_run, a.lock_dir, a.log_dir, launcher)
    print(summary(rep, a.dry_run), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
