"""The ONE LLM provider adapter. No model-specific code exists anywhere else.

Backends:
  claude_code  DEFAULT. Headless `claude -p` on the owner's Claude subscription (claude.ai login on the
               Mac; `claude login` or `claude setup-token` as the service user on the VPS). Tools, MCP,
               settings, hooks and session persistence are all disabled; it runs in an empty directory;
               ANTHROPIC_API_KEY is stripped from its environment so it can never bill an API key.
  anthropic    Claude via the official SDK with an API key (structured outputs)
  local        stub for an OpenAI-compatible endpoint (llama.cpp / vLLM); not built yet

Rules enforced here:
  - Callers pass text that has ALREADY been scrubbed (radar.wall.scrubber). As a second
    guard, complete() re-runs the regex detector on every prompt and refuses to send
    anything with a contact-shaped span.
  - Only public-document text ever goes into prompts.
  - Every call is written to audit_log (model, prompt sha256, response sha256, latency)
    and every HTTP request to egress_log.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from radar import db
from radar.settings import get_settings
from radar.wall.scrubber import detect

T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    pass


class UnscrubbedPrompt(ProviderError):
    pass


class Refused(ProviderError):
    pass


@dataclass
class CallMeta:
    backend: str
    model: str
    prompt_sha256: str
    response_sha256: str
    duration_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None


class Provider(Protocol):
    backend: str
    model: str

    def complete(self, *, system: str, user: str, schema: type[T], purpose: str) -> tuple[T, CallMeta]: ...


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _guard(*texts: str) -> None:
    from radar.wall.scrubber import CONTACT_KINDS, contacts_allowed
    for t in texts:
        hits = detect(t)
        if contacts_allowed():  # D-B35: public documents with contacts may be sent; social profiles still may not
            hits = [h for h in hits if h.kind not in CONTACT_KINDS]
        if hits:
            kinds = sorted({h.kind for h in hits})
            raise UnscrubbedPrompt(f"prompt contains contact-shaped text ({', '.join(kinds)}); scrub first")


def _egress_hooks():
    def on_response(response) -> None:  # httpx.Response
        req = response.request
        with db.connect() as con:
            con.execute(
                "INSERT INTO egress_log VALUES (now(), ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [req.method, str(req.url), req.url.host, response.status_code, None, None, None, "llm", None],
            )

    return {"response": [on_response]}


class AnthropicProvider:
    backend = "anthropic"

    def __init__(self, model: str | None = None, effort: str | None = None) -> None:
        import anthropic

        s = get_settings()
        self.model = model or s.llm_model
        self.effort = effort or s.llm_effort
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            http_client=anthropic.DefaultHttpxClient(event_hooks=_egress_hooks()),
            max_retries=3,
        )

    def complete(self, *, system: str, user: str, schema: type[T], purpose: str) -> tuple[T, CallMeta]:
        _guard(system, user)
        prompt_sha = _sha(json.dumps({"system": system, "user": user, "schema": schema.__name__}))
        t0 = time.monotonic()
        status, detail = "ok", None
        try:
            resp = self._client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if resp.stop_reason == "refusal":
                status = "refused"
                cat = getattr(resp.stop_details, "category", None) if resp.stop_details else None
                raise Refused(f"model declined (category={cat})")
            if resp.stop_reason == "max_tokens":
                status = "truncated"
                raise ProviderError("response hit max_tokens")
            parsed = resp.parsed_output
            if parsed is None:
                status = "unparsed"
                raise ProviderError("no parsed output")
            out_json = parsed.model_dump_json()
            meta = CallMeta(
                backend=self.backend,
                model=resp.model,
                prompt_sha256=prompt_sha,
                response_sha256=_sha(out_json),
                duration_ms=int((time.monotonic() - t0) * 1000),
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
            )
            detail = {"request_id": getattr(resp, "_request_id", None), "in": meta.input_tokens, "out": meta.output_tokens}
            return parsed, meta
        except self._anthropic.APIStatusError as e:
            status, detail = "api_error", {"status": e.status_code, "message": str(e)[:300]}
            raise ProviderError(str(e)) from e
        except self._anthropic.APIConnectionError as e:
            status, detail = "connection_error", {"message": str(e)[:300]}
            raise ProviderError(str(e)) from e
        finally:
            with db.connect() as con:
                db.audit(
                    con, "llm", purpose, schema.__name__, status=status, detail=detail,
                    model=self.model, inputs_hash=prompt_sha,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )


class ClaudeCodeProvider:
    """Claude via the Claude Code CLI in print mode, on the owner's subscription."""

    backend = "claude_code"
    _STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                  "CLAUDE_CODE_USE_VERTEX")

    def __init__(self, model: str | None = None, effort: str | None = None) -> None:
        import shutil

        s = get_settings()
        self.model = model or s.llm_model
        self.effort = effort or s.llm_effort
        self.timeout = s.llm_timeout_s
        self.bin = shutil.which(s.claude_bin) or s.claude_bin
        self.cwd = s.data_dir / "llm-cwd"  # empty dir: no CLAUDE.md, no project settings, no hooks
        self.cwd.mkdir(parents=True, exist_ok=True)

    def complete(self, *, system: str, user: str, schema: type[T], purpose: str) -> tuple[T, CallMeta]:
        import os
        import subprocess

        _guard(system, user)
        prompt_sha = _sha(json.dumps({"system": system, "user": user, "schema": schema.__name__}))
        env = {k: v for k, v in os.environ.items() if k not in self._STRIP_ENV}
        cmd = [self.bin, "-p", "--model", self.model, "--effort", self.effort, "--tools", "",
               "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
               "--output-format", "json", "--system-prompt", system,
               "--json-schema", json.dumps(schema.model_json_schema())]
        t0 = time.monotonic()
        status, detail, model_used = "ok", None, self.model
        try:
            try:
                proc = subprocess.run(cmd, input=user, capture_output=True, text=True, cwd=self.cwd, env=env,
                                      timeout=self.timeout)
            except subprocess.TimeoutExpired as e:
                status = "timeout"
                raise ProviderError(f"claude -p timed out after {self.timeout}s") from e
            try:
                out = json.loads(proc.stdout)
            except json.JSONDecodeError as e:
                status = "bad_output"
                detail = {"rc": proc.returncode, "stderr": proc.stderr[-300:], "stdout": proc.stdout[-300:]}
                raise ProviderError(f"claude -p returned non-JSON (rc={proc.returncode})") from e
            usage = out.get("usage") or {}
            models = list((out.get("modelUsage") or {}).keys())
            model_used = models[0] if models else self.model
            detail = {"session_id": out.get("session_id"), "turns": out.get("num_turns"),
                      "in": usage.get("input_tokens"), "cache_create": usage.get("cache_creation_input_tokens"),
                      "cache_read": usage.get("cache_read_input_tokens"), "out": usage.get("output_tokens"),
                      "list_price_usd": out.get("total_cost_usd"), "subtype": out.get("subtype")}
            if out.get("is_error") or out.get("subtype") != "success":
                status = "error"
                detail["result"] = str(out.get("result"))[:300]
                raise ProviderError(f"claude -p error: {str(out.get('result'))[:200]}")
            raw = out.get("structured_output")
            if raw is None:
                status = "unparsed"
                raise ProviderError("claude -p returned no structured_output")
            try:
                parsed = schema.model_validate(raw)
            except Exception as e:
                status = "schema_invalid"
                raise ProviderError(f"structured output failed validation: {str(e)[:200]}") from e
            meta = CallMeta(backend=self.backend, model=model_used, prompt_sha256=prompt_sha,
                            response_sha256=_sha(parsed.model_dump_json()),
                            duration_ms=int((time.monotonic() - t0) * 1000),
                            input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))
            return parsed, meta
        finally:
            with db.connect() as con:
                # The CLI talks to Anthropic itself; record the call in the egress log as the one allowed POST host.
                con.execute("INSERT INTO egress_log VALUES (now(), 'POST', 'claude-code://subscription', "
                            "'api.anthropic.com', NULL, NULL, NULL, NULL, 'llm', ?)",
                            [None if status == "ok" else status])
                db.audit(con, "llm", purpose, schema.__name__, status=status, detail=detail, model=model_used,
                         inputs_hash=prompt_sha, duration_ms=int((time.monotonic() - t0) * 1000))


class LocalProvider:
    """OpenAI-compatible endpoint (llama.cpp / vLLM). TODO: implement in the local-model phase.

    Kept as a stub so the backend switch exists from day one and nothing else in the
    codebase has to change when it lands.
    """

    backend = "local"

    def __init__(self, model: str | None = None) -> None:
        s = get_settings()
        self.model = model or s.llm_model
        self.base_url = s.llm_local_base_url

    def complete(self, *, system: str, user: str, schema: type[T], purpose: str) -> tuple[T, CallMeta]:
        _guard(system, user)
        raise NotImplementedError("local backend is a stub; set RADAR_LLM_BACKEND=anthropic")


def get_provider() -> Provider:
    backend = get_settings().llm_backend
    if backend == "claude_code":
        return ClaudeCodeProvider()
    if backend == "anthropic":
        return AnthropicProvider()
    if backend == "local":
        return LocalProvider()
    raise ValueError(f"unknown LLM backend {backend!r}")
