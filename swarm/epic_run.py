#!/usr/bin/env python3
"""對一個 EPIC 跑一輪（docs/epic-contract.md）。一律由人手動啟動，並指定模型。

  python3 swarm/epic_run.py --repo acme/swarm --epic 390 --model claude/claude-opus-5-5

流程：
  1. 檢查 EPIC 已放行（swarm:go），算出 $GO。
  2. 準備 EPIC branch `epic/<N>` 的 worktree；agent 只在這裡工作。
  3. 反覆挑可接的 sub-issue：agent 做（最多 3 次）→ runner 照原文跑判準 → 全過才關。
  4. EPIC 留總結；sub-issue 全關才加 epic:review。推送 branch、開／更新 PR。合併與結案由人做。

--no-agent：不叫 agent，只對可接的 sub-issue 跑判準（人已經在 branch 上做完時用）。

蜂群模式（#416）：
  python3 swarm/epic_run.py --repo acme/swarm --epic 412 --swarm --engine claude --max-bees 2
  同時派多隻蜂（官方 CLI），停下就推一把，判準過了才合進 epic/<N>；全部通過開一個總 PR。
  設定見 swarm/model-policy.yaml；說明見 docs/swarm/orchestrator.md。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEFAULT_VENV = os.path.expanduser("~/.venvs/swarm-sched")
STATE = os.path.expanduser("~/.local/share/swarm-epic")


def git(d, *a, check=True):
    return subprocess.run(["git", "-C", d, *a], check=check, capture_output=True, text=True).stdout.strip()


def prepare_worktree(repo_dir: str, branch: str, base: str, wt: str) -> str:
    git(repo_dir, "fetch", "-q", "origin")
    remote = git(repo_dir, "ls-remote", "--heads", "origin", branch)
    if os.path.isdir(wt):
        git(wt, "checkout", "-q", branch)
        if remote:
            git(wt, "merge", "-q", "--ff-only", f"origin/{branch}", check=False)
        return wt
    if remote:
        if git(repo_dir, "rev-parse", "--verify", "-q", branch, check=False):
            git(repo_dir, "worktree", "add", "-q", wt, branch)
        else:
            git(repo_dir, "worktree", "add", "-q", "-b", branch, wt, f"origin/{branch}")
    else:
        git(repo_dir, "worktree", "add", "-q", "-b", branch, wt, f"origin/{base}")
    return wt


def push_and_pr(wt: str, repo: str, branch: str, base: str, epic: dict) -> str:
    # agent 的沙盒可能寫不到 worktree 的 git 目錄；它沒 commit 的改動由 runner 收進來
    if git(wt, "status", "--porcelain"):
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", f"[epic#{epic['number']}] runner：收進 agent 未 commit 的改動")
    git(wt, "push", "-q", "-u", "origin", branch)
    r = subprocess.run(["gh", "pr", "list", "-R", repo, "--head", branch, "--state", "open", "--json", "url", "--jq", ".[0].url"],
                       capture_output=True, text=True)
    url = r.stdout.strip()
    if url:
        return url
    body = (f"EPIC #{epic['number']} 的 branch（runner 建立）。\n\n合併由人做；EPIC 也由人結案。\n"
            f"判準結果見各 sub-issue 的留言。\n\nRefs #{epic['number']}")
    r = subprocess.run(["gh", "pr", "create", "-R", repo, "--head", branch, "--base", base,
                        "--title", f"[epic#{epic['number']}] {epic.get('title', '')}"[:250], "--body", body],
                       capture_output=True, text=True)
    return r.stdout.strip() or r.stderr.strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description="對一個 EPIC 跑一輪（一次一個 EPIC、一輪；由人手動啟動）。")
    ap.add_argument("--repo", required=True, help="owner/name，例如 acme/swarm")
    ap.add_argument("--epic", required=True, type=int, help="EPIC 的 issue 編號")
    ap.add_argument("--model", help="provider/model，例如 claude/claude-opus-5-5 或 codex/gpt-5.6-luna（必填，除非 --no-agent）")
    ap.add_argument("--repo-dir", default=os.getcwd(), help="repo 的 git checkout（預設：目前目錄）")
    ap.add_argument("--base", default=None, help="預設分支（預設：問 GitHub）")
    ap.add_argument("--worktree", default=None, help=f"EPIC worktree 位置（預設：{STATE}/wt/<repo>-<epic>）")
    ap.add_argument("--venv", default=DEFAULT_VENV, help="判準的固定執行環境（PATH 最前面）")
    ap.add_argument("--tries", type=int, default=3, help="每張最多試幾次（契約：3）")
    ap.add_argument("--agent-timeout", type=int, default=1800, help="每次 agent 嘗試的秒數上限")
    ap.add_argument("--check-timeout", type=int, default=900, help="每條判準指令的秒數上限")
    ap.add_argument("--no-agent", action="store_true", help="不叫 agent，只對可接的 sub-issue 跑判準")
    ap.add_argument("--no-push", action="store_true", help="結束時不推送 branch、不開 PR")
    # 蜂群模式（#416）：同時派多隻蜂，偵測停下並推一把，判準過了才合進 EPIC 分支。
    ap.add_argument("--swarm", action="store_true", help="蜂群模式（廣度、深度、合併、總 PR、benchmark）")
    ap.add_argument("--engine", choices=["claude", "codex", "opencode"], help="蜂群模式：用哪種蜂（官方 CLI）")
    ap.add_argument("--max-bees", type=int, help="蜂群模式：同時最多幾隻（覆蓋 swarm.yaml）")
    ap.add_argument("--single", action="store_true", help="蜂群模式的對照組（A 組）：一個 session 一次做完整個 EPIC")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "swarm.yaml"))
    ap.add_argument("--policy", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "model-policy.yaml"))
    a = ap.parse_args(argv)
    if a.swarm:
        return run_swarm(a)

    from epiclib import agent, criteria, judge, retry
    from epiclib import round as epic_round
    from epiclib.github import GitHub

    model = tuple(a.model.split("/", 1)) if a.model else None
    if a.no_agent:
        model = model or ("none", "no-agent")
    elif not model or len(model) != 2:
        ap.error("--model 必填（provider/model），不猜預設值")

    gh = GitHub(a.repo)
    epic = gh.issue(a.epic)
    if not epic.get("go_at"):
        sys.exit(f"EPIC #{a.epic} 沒有 swarm:go：還沒放行")
    base = a.base or subprocess.run(["gh", "repo", "view", a.repo, "--json", "defaultBranchRef", "--jq",
                                     ".defaultBranchRef.name"], capture_output=True, text=True, check=True).stdout.strip()
    branch = f"epic/{a.epic}"
    wt = a.worktree or os.path.join(STATE, "wt", a.repo.replace("/", "-") + f"-{a.epic}")
    os.makedirs(os.path.dirname(wt), exist_ok=True)
    prepare_worktree(os.path.abspath(a.repo_dir), branch, base, wt)
    try:
        go = criteria.go_commit(wt, epic["go_at"], ref=branch)
    except Exception:
        go = criteria.go_commit(wt, epic["go_at"], ref=f"origin/{base}")
    env = {"PATH": os.path.join(a.venv, "bin") + os.pathsep + os.environ.get("PATH", "")}
    print(f"EPIC #{a.epic}  model={'/'.join(model)}  worktree={wt}  $GO={go[:7]}", flush=True)

    acp = None
    if not a.no_agent:
        acp = agent.DshAcp(os.path.join(STATE, "dsh-home")).start()

    def work(ep, it):
        n = it["number"]
        snap = criteria.snapshot(it)
        down = [gh.issue(b) for b in it.get("blocking", [])]

        def check():
            cur = gh.issue(n)
            if not criteria.same(cur, snap):
                return judge.Result(False, [], "判準在這一輪中被改過：停止，等人重新放行")
            return judge.judge(gh, ep, cur, wt, go=go, repo=a.repo, commands=snap["commands"], env=env,
                               timeout=a.check_timeout)

        if a.no_agent:
            r = check()
            print(f"  #{n}: {'通過' if r.ok else '沒通過'}", flush=True)
            return "done" if r.ok else "skipped"

        def attempt(prev):
            p = agent.build_prompt(ep, it, snap["commands"], down, prev=prev)
            r = agent.run(acp, model, wt, p, timeout=a.agent_timeout)
            print(f"  #{n}: agent 回覆 {len(r.text)} 字{'（逾時）' if r.timed_out else ''}", flush=True)
            return r
        out = retry.work(gh, ep, it, attempt, check, max_tries=a.tries)
        print(f"  #{n}: {out}", flush=True)
        return out

    try:
        rep = epic_round.run_round(gh, a.epic, model, work, os.path.join(STATE, "locks"))
    finally:
        if acp:
            acp.close()
    print(f"done={rep.done} stuck={rep.stuck} review={rep.review}")
    for k, v in sorted(rep.skipped.items()):
        print(f"  skip #{k}: {v}")
    if not a.no_push and (rep.done or rep.stuck):
        print("PR:", push_and_pr(wt, a.repo, branch, base, epic))
    return 0


def run_swarm(a) -> int:
    from epiclib import bees, swarm
    from epiclib.github import GitHub
    if not a.engine:
        sys.exit("--swarm 需要 --engine（claude／codex／opencode）")
    model = a.model.split("/", 1)[1] if a.model and "/" in a.model else (a.model or "")
    cfg = swarm.Config.load(a.config)
    if a.max_bees:
        cfg.max_bees = a.max_bees
    cfg.check_timeout = a.check_timeout
    policy = swarm.Policy.load(a.policy)
    gh = GitHub(a.repo)
    base = a.base or subprocess.run(["gh", "repo", "view", a.repo, "--json", "defaultBranchRef", "--jq",
                                     ".defaultBranchRef.name"], capture_output=True, text=True, check=True).stdout.strip()
    repo_dir = os.path.abspath(a.repo_dir)
    git(repo_dir, "fetch", "-q", "origin")

    def open_pr(branch, title, body):
        if a.no_push:
            return "(--no-push)"
        git(repo_dir, "push", "-q", "-u", "origin", branch)
        r = subprocess.run(["gh", "pr", "list", "-R", a.repo, "--head", branch, "--state", "open", "--json", "url",
                            "--jq", ".[0].url"], capture_output=True, text=True)
        if r.stdout.strip():
            return r.stdout.strip()
        r = subprocess.run(["gh", "pr", "create", "-R", a.repo, "--head", branch, "--base", base, "--title", title,
                            "--body", body], capture_output=True, text=True)
        return r.stdout.strip() or r.stderr.strip()

    def pr_mergeable(url):
        """GitHub 的 mergeable：剛開的 PR 可能還在算（UNKNOWN），等一下再問。"""
        for _ in range(6):
            r = subprocess.run(["gh", "pr", "view", url, "--json", "mergeable", "--jq", ".mergeable"],
                               capture_output=True, text=True)
            v = r.stdout.strip()
            if v == "MERGEABLE":
                return True
            if v == "CONFLICTING":
                return False
            time.sleep(5)
        return None

    def merge_pr(url):
        r = subprocess.run(["gh", "pr", "merge", url, "--merge"], capture_output=True, text=True)
        return r.returncode == 0, (r.stdout + r.stderr).strip()[:300]

    def comment_pr(url, body):
        subprocess.run(["gh", "pr", "comment", url, "--body-file", "-"], input=body, capture_output=True,
                       text=True, check=True)

    env = {"PATH": os.path.join(a.venv, "bin") + os.pathsep + os.environ.get("PATH", "")}
    orch = swarm.Orchestrator(gh, epic=a.epic, repo_dir=repo_dir, base=base, cfg=cfg, policy=policy,
                              engine=a.engine, model=model,
                              bee_factory=bees.factory(a.engine, model, cfg.max_minutes * 60 * (4 if a.single else 1)),
                              state_dir=os.path.join(STATE, "swarm"), push=not a.no_push, open_pr=open_pr,
                              env=env, repo=a.repo, pr_mergeable=pr_mergeable, merge_pr=merge_pr,
                              comment_pr=comment_pr)
    rep = orch.run_single() if a.single else orch.run()
    print(f"mode={'single' if a.single else 'swarm'} done={rep.done} stuck={rep.stuck} review={rep.review} pr={rep.pr or '-'} auto_merged={str(rep.metrics.get('auto_merged', False)).lower()}")
    print(f"metrics: {rep.metrics_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
