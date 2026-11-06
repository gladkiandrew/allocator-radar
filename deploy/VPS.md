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
