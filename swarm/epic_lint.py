#!/usr/bin/env python3
"""檢查 issue 品質，依結果加／移除 needs-criteria。

  python3 swarm/epic_lint.py --repo acme/swarm --issue 410
  python3 swarm/epic_lint.py --repo acme/swarm --epic 390      # EPIC 和它所有 sub-issue
  python3 swarm/epic_lint.py ... --dry-run                                  # 只看結果，不改 label
  python3 swarm/epic_lint.py --file body.md --labels by:swarm               # 離線檢查一段內文

exit code：0 全部通過；1 有不通過；2 參數或連線錯誤。
"""
from __future__ import annotations

import argparse
import sys

from epiclib import lint


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--issue", type=int)
    g.add_argument("--epic", type=int)
    g.add_argument("--file")
    ap.add_argument("--labels", default="", help="--file 時用：逗號分隔的 labels")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if a.file:
        body = open(a.file, encoding="utf-8").read()
        rep = lint.lint({"body": body, "labels": [x for x in a.labels.split(",") if x]})
        print("PASS" if rep.ok else "FAIL")
        for p in rep.problems:
            print(" -", p)
        for w in rep.warnings:
            print(" ! 提醒：", w)
        return 0 if rep.ok else 1

    if not a.repo:
        ap.error("--repo is required with --issue/--epic")
    from epiclib.github import GitHub
    try:
        gh = GitHub(a.repo)
        targets = [a.issue] if a.issue else [a.epic] + [s["number"] for s in gh.subs(a.epic)]
        bad = 0
        for n in targets:
            issue = gh.issue(n)
            rep, action = lint.apply(gh, n, issue, dry_run=a.dry_run)
            print(f"#{n} {'PASS' if rep.ok else 'FAIL'} [{action}{' (dry-run)' if a.dry_run else ''}]")
            for p in rep.problems:
                print("   -", p)
            for w in rep.warnings:
                print("   ! 提醒：", w)
            bad += not rep.ok
    except RuntimeError as e:
        print("error:", e, file=sys.stderr)
        return 2
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
