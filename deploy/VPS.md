# Deploying to the VPS (subscription-backed)

Same pattern as the crew runtimes: the app's LLM calls go through `claude -p` on the owner's
Claude subscription, run as the VPS service user. No API key.

1. As the service user that runs the app, confirm the CLI is logged in to the subscription:
   `claude auth status` → `"authMethod": "claude.ai"`. If not, `claude login`. For a headless
   or containerized runtime, use `claude setup-token` and export `CLAUDE_CODE_OAUTH_TOKEN`.
2. `git clone` the repo, `uv sync`, `cp .env.example .env`, and set:
   `RADAR_AUTH_MODE=basic`, `RADAR_AUTH_PASSWORD_HASH=<sha256>`, `RADAR_LAKE_KEY_FILE=<path outside the repo>`.
   Leave `RADAR_LLM_BACKEND` at its default (`claude_code`). Do not set `ANTHROPIC_API_KEY`.
3. Copy the lake key from the Mac (`~/.config/allocator-radar/lake.key`) over a secure channel
   if you move the existing lake. Without the key the encrypted raw copies cannot be read.
4. Run `uv run radar serve --host 127.0.0.1 --port 8000` under systemd, and expose it only behind
   the existing tunnel + login. (Docker Compose and full notes come in build step 8.)
5. Nightly jobs: `uv run radar crawl`, `uv run radar extract`, `uv run radar wall`.

## Public read-only site: allocator.amagna.co (D-B14)

The shareable site runs in **public mode** (`RADAR_PUBLIC_MODE=true`): GET only, no review queue,
no tools, no export, approved signals only, and `/doc/<sha>` redirects to the original public URL.
So the VPS needs neither the encrypted lake nor its key, nor a Claude login.

1. On the Mac: `deploy/push-snapshot.sh` (rsyncs `data/radar.duckdb` + `data/lance/`).
2. On the VPS: `curl -fsSL https://raw.githubusercontent.com/gladkiandrew/allocator-radar/main/deploy/setup-public-site.sh | bash`
   (or run it from the clone). Serves on `127.0.0.1:4710`; never bind it publicly.
3. Cloudflare Zero Trust → Networks → Tunnels → `aios-vps` → Public hostname → add
   `allocator.amagna.co` → `http://localhost:4710`. Do **not** add an Access application for this
   hostname (the site is meant to be public). The dashboard creates the DNS record.
4. To refresh the data later: rerun step 1 (the Mac stays the place where crawling and review happen).
