"""讓測試從 repo 根目錄（`python3 -m unittest scheduler/tests/epic/test_x.py`）或任何地方都能 import。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEDULER = os.path.dirname(os.path.dirname(HERE))
for p in (SCHEDULER, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
