"""Make the orchestrator (swarm/) and gh_budget (plugins/swarm-board/scheduler/) importable from any cwd."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SWARM = os.path.dirname(HERE)
SCHEDULER = SWARM  # name kept for the tests
ROOT = os.path.dirname(SWARM)
for p in (SWARM, os.path.join(ROOT, "plugins", "swarm-board", "scheduler"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
