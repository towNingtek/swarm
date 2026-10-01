#!/usr/bin/env bash
# Install Swarm on a fresh host (run as root from the repository checkout).
#   sudo deploy/install.sh            install files, users and units; start nothing new
#   sudo deploy/install.sh --start    also enable and start the services
# Safe to re-run: existing settings and secrets are never overwritten.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
START=0; [ "${1:-}" = "--start" ] && START=1

[ "$REPO" = /opt/swarm ] || echo "note: units expect the checkout at /opt/swarm (found $REPO)"

# A system user that runs the platform. It needs Docker to manage site
# containers; docker group membership is root-equivalent, so keep this host
# dedicated to Swarm.
id swarm >/dev/null 2>&1 || useradd --system --home-dir /var/lib/swarm --shell /usr/sbin/nologin swarm
usermod -aG docker swarm

install -d -m 0755 /etc/swarm
install -d -m 0750 -o root -g swarm /etc/swarm/secrets
install -d -m 0750 -o swarm -g swarm /var/lib/swarm /var/lib/swarm/platform /var/lib/swarm/sites
# Staging: the platform writes, the root watcher reads.
install -d -m 0770 -o root -g swarm /var/lib/swarm/nginx-staging

if [ ! -e /etc/swarm/platform.env ]; then
  install -m 0640 -o root -g swarm "$REPO/deploy/platform.env.example" /etc/swarm/platform.env
  echo "created /etc/swarm/platform.env: edit it before starting"
fi
umask 077
if [ ! -s /etc/swarm/secrets/entry-secret ]; then
  head -c 48 /dev/urandom | base64 | tr -d '\n/+=' > /etc/swarm/secrets/entry-secret
fi
if [ ! -s /etc/swarm/secrets/admin-password ]; then
  head -c 24 /dev/urandom | base64 | tr -d '\n/+=' > /etc/swarm/secrets/admin-password
  echo "generated the admin password: /etc/swarm/secrets/admin-password"
fi
chown root:swarm /etc/swarm/secrets/* && chmod 0640 /etc/swarm/secrets/*
[ -s /etc/swarm/secrets/model-key ] || echo "missing: /etc/swarm/secrets/model-key (your model gateway key)"

for unit in "$REPO"/deploy/systemd/*.service "$REPO"/deploy/systemd/*.path; do
  install -m 0644 "$unit" /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --now swarm-site-firewall.service swarm-nginx-apply.path

if [ "$START" = 1 ]; then
  systemctl enable --now swarm-site-relay swarm-platform swarm-platform-admin
  systemctl --no-pager --lines=0 status swarm-site-relay swarm-platform swarm-platform-admin | grep -E "●|Active:"
else
  echo "next: edit /etc/swarm/platform.env, add secrets/model-key, build the site image, then: $0 --start"
fi
