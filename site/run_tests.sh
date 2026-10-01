#!/usr/bin/env bash
# Site unit tests. Hermetic: a fixed test domain and container prefix, so the
# result never depends on the operator's real settings.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1
export SWARM_DOMAIN=example.com SWARM_CONTAINER_PREFIX=site-
unset CLOUDFLARE_API_TOKEN CLOUDFLARE_ZONE_ID SWARM_ISOLATION_PROBES
cd "$HERE"
PYTHONPATH="$HERE:$HERE/tests" "$PYTHON" -B -m unittest discover -s tests -v "$@"
"${NODE:-node}" --test templates/test-*.mjs
