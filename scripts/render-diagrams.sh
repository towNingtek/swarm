#!/usr/bin/env bash
# Render docs/diagrams/*.mmd to PNG (light and dark) for the READMEs.
# GitHub's mobile apps do not draw Mermaid, so the READMEs embed these images and keep the .mmd as source.
# Needs Node and Chromium via Playwright: npx -y playwright install chromium
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
node "$ROOT/scripts/render-diagrams.mjs" "$ROOT/docs/diagrams"
