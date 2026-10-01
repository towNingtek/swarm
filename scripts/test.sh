#!/usr/bin/env bash
# Run every test suite in the repository. Suites are added as code moves in.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1

"$PYTHON" -B -m unittest discover -s "$ROOT/scripts" -p 'test_*.py' -v
PYTHON="$PYTHON" bash "$ROOT/site/run_tests.sh"
PYTHON="$PYTHON" bash "$ROOT/platform/run_tests.sh"
