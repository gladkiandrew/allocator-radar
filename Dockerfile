# Allocator Radar — single image for the web app and the nightly worker.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 \
    RADAR_DATA_DIR=/data RADAR_LAKE_KEY_FILE=/secrets/lake.key RADAR_MANUAL_INBOX=/data/inbox/manual

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates nodejs npm && rm -rf /var/lib/apt/lists/*
# Claude Code CLI: the LLM backend runs `claude -p` on the owner's subscription (CLAUDE_CODE_OAUTH_TOKEN).
RUN npm install -g @anthropic-ai/claude-code && claude --version
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY config ./config
COPY registry ./registry
RUN uv sync --frozen --no-dev && uv run playwright install --with-deps chromium

RUN useradd -m -u 10001 radar && mkdir -p /data /secrets && chown -R radar /data /app
USER radar
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "radar", "serve", "--host", "0.0.0.0", "--port", "8000"]
