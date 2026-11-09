#!/usr/bin/env bash
# Runs on the Mac. Copies the current data snapshot to the VPS and restarts the public site.
# Only the database and the search index go over: never the encrypted document lake or its key
# (the public site redirects citations to the original public URLs instead).
set -euo pipefail
HOST="${RADAR_VPS:-ubuntu@15.204.172.174}"
cd "$(dirname "$0")/.."
ssh "$HOST" 'mkdir -p ~/allocator-radar/data && (sudo systemctl stop allocator-radar 2>/dev/null || true)'
rsync -az --info=progress2 data/radar.duckdb "$HOST:allocator-radar/data/radar.duckdb"
rsync -az --delete --info=progress2 data/lance/ "$HOST:allocator-radar/data/lance/"
ssh "$HOST" 'sudo systemctl start allocator-radar 2>/dev/null || echo "service not installed yet: run deploy/setup-public-site.sh on the VPS"'
