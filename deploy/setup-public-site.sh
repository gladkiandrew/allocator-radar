#!/usr/bin/env bash
# Runs ON the VPS as ubuntu. Installs/updates the public read-only site on 127.0.0.1:4710.
# The data snapshot (radar.duckdb + lance/) is copied separately by deploy/push-snapshot.sh.
set -euo pipefail
APP=/home/ubuntu/allocator-radar
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
if [ -d "$APP/.git" ]; then git -C "$APP" pull --ff-only; else git clone https://github.com/gladkiandrew/allocator-radar "$APP"; fi
cd "$APP"
mkdir -p data
uv sync --frozen
sudo cp deploy/allocator-radar.service /etc/systemd/system/allocator-radar.service
sudo systemctl daemon-reload
sudo systemctl enable --now allocator-radar
sudo systemctl restart allocator-radar
sleep 4
curl -fsS -o /dev/null -w "local check: HTTP %{http_code}\n" http://127.0.0.1:4710/about
