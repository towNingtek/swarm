#!/usr/bin/env bash
# Run every test suite in the repository. Suites are added as code moves in.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1

"$PYTHON" -B -m unittest discover -s "$ROOT/scripts" -p 'test_*.py' -v
PYTHON="$PYTHON" bash "$ROOT/site/run_tests.sh"
PYTHON="$PYTHON" bash "$ROOT/platform/run_tests.sh"
bash "$ROOT/deploy/tests/test_swarm_run.sh"

# Board plugin (Wolffia) and the swarm orchestrator. The board's default org in tests is "acme".
B="$ROOT/plugins/swarm-board/wolffia"
( cd "$B/site-plugin" && SWARM_BOARD_ORG=acme node --test test/*.test.js )
for t in "$B"/tests/v8*.js; do node "$t" >/dev/null || { echo "FAIL $t"; exit 1; }; done
SWARM_BOARD_ORG=acme WOLFFIA_ORG=acme WOLFFIA_PROJECTS="$B/projects.example.json" \
  "$PYTHON" -B -m unittest discover -s "$B/tests" -p 'test_*.py'
"$PYTHON" -B -m unittest discover -s "$ROOT/swarm/tests" -p 'test_*.py'
