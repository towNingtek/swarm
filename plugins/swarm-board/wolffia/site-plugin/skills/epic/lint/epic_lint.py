#!/usr/bin/env python3
"""離線檢查一份 EPIC／sub-issue 草稿（epic skill 內附，不需要 GitHub）。

  python3 epic_lint.py --file draft.md --labels by:swarm     # sub-issue
  python3 epic_lint.py --file epic.md --labels epic          # EPIC

exit code：0 通過；1 不通過；2 參數錯誤或讀不到檔案。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from epiclib import lint  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--labels", default="", help="逗號分隔的 labels（by:swarm、by:human 或 epic）")
    a = ap.parse_args(argv)

    try:
        with open(a.file, encoding="utf-8") as f:
            body = f.read()
    except OSError as e:
        print("error:", e, file=sys.stderr)
        return 2
    rep = lint.lint({"body": body, "labels": [x for x in a.labels.split(",") if x]})
    print("PASS" if rep.ok else "FAIL")
    for p in rep.problems:
        print(" -", p)
    for w in rep.warnings:
        print(" ! 提醒：", w)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
