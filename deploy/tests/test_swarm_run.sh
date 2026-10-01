#!/usr/bin/env bash
# deploy/bin/swarm-run: reads settings, loads only each service's secrets,
# fails closed on a missing required secret, and never prints secret values.
set -euo pipefail
REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir "$T/secrets"
cat > "$T/env" <<ENV
SWARM_HOME=$REPO
SWARM_SECRETS_DIR=$T/secrets
SWARM_STATE=$T/state
PLATFORM_ORIGIN=https://platform.example.com
ENV
RUN="$REPO/deploy/bin/swarm-run"
pass=0; fail() { echo "FAIL: $*"; exit 1; }; ok() { pass=$((pass+1)); echo "ok - $*"; }
export SWARM_ENV="$T/env"

if bash "$RUN" --check platform >/dev/null 2>&1; then fail "started without secrets"; fi
ok "a missing required secret stops the start"

printf 'entry-value-123' > "$T/secrets/entry-secret"
printf 'model-value-456' > "$T/secrets/model-key"
printf 'admin-value-789' > "$T/secrets/admin-password"
out="$(bash "$RUN" --check platform)"
grep -q 'SWARM_ENTRY_SECRET' <<<"$out" && grep -q 'PLATFORM_MODEL_KEY' <<<"$out" || fail "names"
grep -q 'PLATFORM_DB' <<<"$out" || fail "db default"
! grep -q 'ADMIN_PASSWORD' <<<"$out" || fail "platform got the admin password"
! grep -q -E 'value-[0-9]' <<<"$out" || fail "secret printed"
ok "platform gets its secrets, not the admin password, and nothing is printed"

out="$(bash "$RUN" --check relay)"
! grep -q -E 'SWARM_ENTRY_SECRET|ADMIN_PASSWORD' <<<"$out" || fail "relay over-privileged"
ok "the relay only gets the model key"

grep -q 'ADMIN_PASSWORD' <<<"$(bash "$RUN" --check admin)" || fail "admin password"
ok "admin gets the admin password"
echo "swarm-run: $pass checks passed"
