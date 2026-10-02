# Demo media

`docs/media/` (GIF, MP4, screenshots) is made by these scripts on a
**throwaway** install. They create a customer and a site; never run them
against a real platform.

| File | What it is |
|---|---|
| `demo_gateway.py` | A scripted OpenAI-compatible model (no real AI). Replies are picked by keywords and streamed |
| `record.mjs` | Playwright: drives the operator, the customer and the site; writes WebM clips, screenshots and `marks.json` |
| `compose.py` | ffmpeg: cuts the clips at the marks, burns in captions, writes `demo.mp4` and `demo.gif` |

1. Start a throwaway host and leave it running:

   ```sh
   KEEP=1 bash deploy/tests/clean-install/run.sh
   ```

   It serves `platform.example.com` on the host container's address
   (`docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' swarm-clean-host`,
   used as `HOST_IP` below), with a stub model gateway on `172.17.0.1:4000`
   inside the host. Swap the stub for the scripted model:

   ```sh
   docker cp demo/demo_gateway.py swarm-clean-host:/root/
   docker exec swarm-clean-host sh -c 'kill $(pgrep -f "^python3 /root/fake_gateway")'
   docker exec -d swarm-clean-host python3 /root/demo_gateway.py
   ```

   Add the demo site to the host's `/etc/hosts` so the site's self-test can
   reach it. The file is bind-mounted, so rewrite it in place instead of
   replacing it:

   ```sh
   docker exec swarm-clean-host sh -c 'h=$(cat /etc/hosts); printf "%s\n127.0.0.1 cafe.example.com\n" "$h" > /etc/hosts'
   ```

2. Record. The invite link and the customer's password stay in memory, and
   the invite field only ever paints a masked copy of the link:

   ```sh
   OUT=demo-out HOST_IP=<host address> \
   ADMIN_PASSWORD="$(docker exec swarm-clean-host cat /etc/swarm/secrets/admin-password)" \
   PLAYWRIGHT_FROM=/path/to/node_modules/ node demo/record.mjs
   ```

3. Compose (needs ffmpeg and a CJK font such as WenQuanYi Zen Hei or Noto
   Sans CJK), then copy `demo.mp4`, `demo.gif` and the PNGs into `docs/media/`:

   ```sh
   python3 demo/compose.py demo-out demo-out
   ```

Before committing new media, check every frame of the operator clip for an
unmasked invite token, e.g. OCR it with `tesseract` and look for `token=`
followed by anything other than dots.

Clean up: `docker rm -f swarm-clean-host; docker network rm swarm-clean-net;
docker volume rm swarm-clean-docker swarm-clean-containerd`.
