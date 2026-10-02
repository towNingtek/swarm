#!/usr/bin/env bash
# Clean-install test: build a throwaway host (systemd, Docker, nginx), follow
# deploy/README.md inside it, then drive a customer from invite to their site
# and back out again. Nothing on the machine running this is changed except
# a few Docker objects named swarm-clean-*, removed at the end.
#   bash deploy/tests/clean-install/run.sh            # keep nothing
#   KEEP=1 bash deploy/tests/clean-install/run.sh     # leave the host running
# Tests the COMMITTED tree (it clones the repository), like a real install.
# Needs: docker with --privileged. Takes ~10 minutes (it builds the site image).
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "$HERE/../../.." && pwd)"
N=swarm-clean-host
cleanup() {
  [ "${KEEP:-0}" = 1 ] && { echo "kept: docker exec -it $N bash"; return; }
  docker rm -f "$N" >/dev/null 2>&1 || true
  docker network rm swarm-clean-net >/dev/null 2>&1 || true
  docker volume rm swarm-clean-docker swarm-clean-containerd >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build -q -t swarm-clean-host:latest -f "$HERE/host.Dockerfile" "$HERE" >/dev/null
docker network create --subnet 10.250.7.0/24 swarm-clean-net >/dev/null 2>&1 || true
# The inner Docker keeps its data on volumes: overlay cannot nest on overlay.
docker run -d --name "$N" --hostname swarm-clean --privileged --network swarm-clean-net \
  -v "$REPO:/src:ro" -v swarm-clean-docker:/var/lib/docker -v swarm-clean-containerd:/var/lib/containerd \
  swarm-clean-host:latest >/dev/null
for _ in $(seq 60); do docker exec "$N" systemctl is-active -q docker 2>/dev/null && break; sleep 2; done

docker exec -i "$N" bash -s <<'IN_HOST'
set -euo pipefail
say() { printf '\n== %s\n' "$*"; }

say "deploy/README.md: Install"
git config --global --add safe.directory '*'
git clone -q /src /opt/swarm            # the README clones from GitHub
cd /opt/swarm
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
docker build -q -f site/Dockerfile -t swarm-site:latest . >/dev/null
deploy/install.sh

say "operator settings (what a person would edit)"
# A stand-in model gateway on the host, and its key.
cp /src/deploy/tests/clean-install/fake_gateway.py /root/ && (nohup python3 /root/fake_gateway.py >/dev/null 2>&1 &)
printf sk-test-gateway > /etc/swarm/secrets/model-key
chown root:swarm /etc/swarm/secrets/model-key && chmod 0640 /etc/swarm/secrets/model-key
cp deploy/nginx/swarm-platform.conf.example /etc/nginx/conf.d/swarm-platform.conf
# TLS in front of nginx, as the README requires: a throwaway certificate.
cd /root
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=example.com" \
  -addext "subjectAltName=DNS:example.com,DNS:*.example.com" -keyout tls.key -out tls.crt 2>/dev/null
cat > /etc/nginx/conf.d/00-test-tls.conf <<'NGINX'
server {
    listen 443 ssl default_server;
    ssl_certificate /root/tls.crt; ssl_certificate_key /root/tls.key;
    access_log /var/log/nginx/access.log swarm_noquery;  # a real TLS proxy needs the same care
    location / { proxy_pass http://127.0.0.1:80; proxy_set_header Host $host; proxy_http_version 1.1;
                 proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; proxy_buffering off; }
}
NGINX
cp tls.crt /usr/local/share/ca-certificates/swarm-clean.crt && update-ca-certificates >/dev/null 2>&1
echo "127.0.0.1 platform.example.com acme.example.com" >> /etc/hosts
nginx -t 2>/dev/null && systemctl reload nginx
cd /opt/swarm

say "deploy/README.md: start"
deploy/install.sh --start >/dev/null
for _ in $(seq 60); do
  curl -sf -o /dev/null https://platform.example.com/ && curl -sf -o /dev/null https://platform.example.com/admin/login \
    && systemctl is-active -q swarm-site-relay && break
  sleep 2
done
for s in swarm-site-relay swarm-platform swarm-platform-admin swarm-site-firewall swarm-nginx-apply.path; do
  printf '%-24s %s\n' "$s" "$(systemctl is-active "$s")"
done

say "customer journey"
mkdir -p /root/clean-install && cp /src/deploy/tests/clean-install/in_site.mjs /root/clean-install/
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt PYTHONUNBUFFERED=1 \
  .venv/bin/python /src/deploy/tests/clean-install/journey.py

say "no secrets in the journal"
for f in /etc/swarm/secrets/*; do
  if journalctl --no-pager | grep -qF -- "$(cat "$f")"; then echo "BAD $(basename "$f") appears in the journal"; exit 1; fi
done
journalctl --no-pager | grep -qE 'token=[A-Za-z0-9_-]{43}' && { echo "BAD an invite token appears in the journal"; exit 1; }
echo "ok  no secret file content and no invite token in the journal"
grep -q 'GET /auth/enter' /var/log/nginx/access.log || { echo "BAD the site entry was not logged at all"; exit 1; }
grep -qE '(token|ticket)=' /var/log/nginx/access.log && { echo "BAD a query credential appears in the nginx access log"; exit 1; }
echo "ok  no invite token or entry ticket in the nginx access log"
echo CLEAN INSTALL OK
IN_HOST
