#!/usr/bin/env bash
# End-to-end check of the nginx staging protocol: site/nginx_staging.py (the
# platform side) against deploy/bin/swarm-nginx-apply (the root side), with a
# real nginx. Runs inside a throwaway container as root; needs docker.
#   bash deploy/tests/test_nginx_apply.sh
set -euo pipefail
REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="${NGINX_TEST_IMAGE:-nginx:1.27}"

docker run --rm -i -v "$REPO:/repo:ro" "$IMAGE" bash -s <<'IN_CONTAINER'
set -euo pipefail
apt-get update -qq >/dev/null && apt-get install -y -qq python3 python3-yaml >/dev/null
# The watcher calls "systemctl reload nginx"; here nginx runs directly.
printf '#!/bin/sh\nexec nginx -s reload\n' > /usr/local/bin/systemctl && chmod +x /usr/local/bin/systemctl
nginx -g "error_log /dev/null crit;"
export SWARM_ENV=/tmp/platform.env SWARM_NGINX_OWNED_DIR=/tmp/owned
STAGE=/tmp/stage; mkdir -p "$STAGE"
echo "SWARM_NGINX_STAGING=$STAGE   # comment" > "$SWARM_ENV"
APPLY=/repo/deploy/bin/swarm-nginx-apply
CONF=/etc/nginx/conf.d
pass=0; fail() { echo "FAIL: $*"; exit 1; }; ok() { pass=$((pass+1)); echo "ok - $*"; }

# A background loop plays the systemd path unit.
( while :; do bash "$APPLY" >/dev/null 2>&1 || true; sleep 0.2; done ) & WATCH=$!
trap 'kill $WATCH' EXIT

py() { SWARM_NGINX_STAGING="$STAGE" PYTHONPATH=/repo/site python3 -c "$1"; }
SITE='server { listen 80; server_name a.example.com; location / { return 204; } }'

py "import nginx_staging as n; n.apply_config('a', '''$SITE''', timeout=10)"
[ -f "$CONF/swarm-site-a.conf" ] && [ -f /tmp/owned/swarm-site-a.conf ] || fail "apply"
[ "$(stat -c %U "$CONF/swarm-site-a.conf")" = root ] || fail "owner"
ok "staged config is applied, root-owned and recorded as owned"

py "import nginx_staging as n; n.apply_config('a', '''${SITE/204/200}''', timeout=10)"
grep -q 'return 200' "$CONF/swarm-site-a.conf" || fail "update"
ok "an owned config can be updated"

if py "import nginx_staging as n; n.apply_config('b', 'this is not nginx', timeout=10)" 2>/dev/null; then fail "bad config accepted"; fi
[ ! -e "$CONF/swarm-site-b.conf" ] && nginx -t 2>/dev/null || fail "bad config left behind"
ok "an invalid config is refused and nginx stays valid"

cp "$CONF/swarm-site-a.conf" /tmp/good
if py "import nginx_staging as n; n.apply_config('a', 'broken {', timeout=10)" 2>/dev/null; then fail "broken update accepted"; fi
cmp -s /tmp/good "$CONF/swarm-site-a.conf" || fail "previous config not restored"
ok "a broken update restores the previous config"

echo "$SITE" > "$CONF/swarm-site-foreign.conf"; nginx -s reload
if py "import nginx_staging as n; n.apply_config('foreign', '''$SITE''', timeout=10)" 2>/dev/null; then fail "overwrote foreign"; fi
if py "import nginx_staging as n; n.remove_config('foreign', timeout=10)" 2>/dev/null; then fail "removed foreign"; fi
[ -f "$CONF/swarm-site-foreign.conf" ] || fail "foreign config gone"
ok "a config this mechanism did not create is never overwritten or removed"

touch "$STAGE/.owned-swarm-site-foreign.conf"   # the old in-staging ownership marker
if py "import nginx_staging as n; n.remove_config('foreign', timeout=10)" 2>/dev/null; then fail "forged ownership honoured"; fi
ok "ownership cannot be forged from the staging directory"

echo 'secret' > /root/secret.txt
ln -s /root/secret.txt "$STAGE/swarm-site-evil.conf"; sleep 1.5
[ ! -e "$CONF/swarm-site-evil.conf" ] || fail "symlink followed"
ok "a symlink in staging is rejected"

for bad in "hub-x.conf" "swarm-site-UP.conf" "swarm-site-a.b.conf" "swarm-site--x-.conf"; do
  echo "$SITE" > "$STAGE/$bad"
done; sleep 1.5
ls "$CONF" | grep -q -v -E '^(default|00-swarm-common|swarm-site-(a|foreign|real))\.conf$' && fail "bad name applied"
ok "malformed names are rejected"

# The real site template needs the shared map that install.sh installs.
REAL="import common, nginx_staging as n; n.apply_config('real', common.render_site_conf('real', 18001), timeout=10)"
if SWARM_DOMAIN=example.com py "$REAL" 2>/dev/null; then fail "real template applied without the shared map"; fi
install -m 0644 /repo/site/templates/nginx-common.conf "$CONF/00-swarm-common.conf"
SWARM_DOMAIN=example.com py "$REAL"
[ -f "$CONF/swarm-site-real.conf" ] || fail "real template"
grep -q -F '00-swarm-common.conf' /repo/deploy/install.sh || fail "install.sh does not install the shared map"
ok "the real site template applies once install.sh's shared map is in place"

py "import nginx_staging as n; n.remove_config('a', timeout=10)"
[ ! -e "$CONF/swarm-site-a.conf" ] && [ ! -e /tmp/owned/swarm-site-a.conf ] || fail "remove"
ok "an owned config is removed"
echo "nginx-apply: $pass checks passed"
IN_CONTAINER
