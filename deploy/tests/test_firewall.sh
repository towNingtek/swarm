#!/usr/bin/env bash
# Check deploy/bin/swarm-site-firewall inside a throwaway container (its own
# network namespace; the host's rules are never touched): settings are read
# from the env file, rules are applied, re-applied without duplicates and
# removed without a trace. Needs docker with --cap-add NET_ADMIN.
#   bash deploy/tests/test_firewall.sh
set -euo pipefail
REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

docker run --rm -i --cap-add NET_ADMIN -v "$REPO:/repo:ro" debian:12-slim bash -s <<'IN_CONTAINER'
set -euo pipefail
apt-get update -qq >/dev/null && apt-get install -y -qq iptables >/dev/null
export SWARM_ENV=/tmp/platform.env SWARM_IPTABLES=iptables-legacy
cat > "$SWARM_ENV" <<'ENV'
SWARM_SITE_BRIDGE=br-test     # comment
SWARM_SITE_SUBNET=172.29.99.0/24
SWARM_MODEL_RELAY="172.17.0.1:9999"
ENV
FW=/repo/deploy/bin/swarm-site-firewall
pass=0; fail() { echo "FAIL: $*"; exit 1; }; ok() { pass=$((pass+1)); echo "ok - $*"; }
IPT=iptables-legacy
before="$($IPT -S | sort)"

bash "$FW" >/dev/null
rules="$($IPT -S)"
grep -q -- '-A SWARM-SITES-IN -d 172.17.0.1/32 -p tcp -m tcp --dport 9999 -j ACCEPT' <<<"$rules" || fail "relay rule"
grep -q -- '-A INPUT -i br-test -j SWARM-SITES-IN' <<<"$rules" || fail "input jump"
grep -q -- '-A SWARM-SITES-FWD -d 169.254.0.0/16 -i br-test -j DROP' <<<"$rules" || fail "metadata drop"
grep -q -- '-A SWARM-SITES-IN -j DROP' <<<"$rules" || fail "default drop"
ok "rules use the bridge and relay from the settings file"

bash "$FW" >/dev/null
[ "$($IPT -S | sort)" = "$(sort <<<"$rules")" ] || fail "re-apply changed rules"
ok "re-applying is idempotent"

bash "$FW" --remove >/dev/null
[ "$($IPT -S | grep -v DOCKER-USER | sort)" = "$(grep -v DOCKER-USER <<<"$before")" ] || fail "remove left rules"
ok "--remove leaves no rules behind"

echo 'SWARM_SITE_BRIDGE=br-x;reboot' > "$SWARM_ENV"
if bash "$FW" >/dev/null 2>&1; then fail "bad bridge accepted"; fi
echo 'SWARM_MODEL_RELAY=$(id)' > "$SWARM_ENV"
if bash "$FW" >/dev/null 2>&1; then fail "bad relay accepted"; fi
ok "malformed settings are refused, never executed"
echo "firewall: $pass checks passed"
IN_CONTAINER
