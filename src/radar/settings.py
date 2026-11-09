"""Runtime configuration. Everything comes from env (prefix RADAR_) or config/*.yaml.

Nothing here names an employer, a firm, or a person. Secrets (API keys, the lake key)
come from the environment or files outside the repo, never from the repo itself.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RADAR_", env_file=".env", extra="ignore")

    data_dir: Path = REPO_ROOT / "data"
    config_dir: Path = REPO_ROOT / "config"
    registry_dir: Path = REPO_ROOT / "registry"
    manual_inbox: Path = REPO_ROOT / "inbox" / "manual"

    # Storage. DuckDB by default; the SQL is kept to the portable subset so a
    # Postgres backend can be added behind db.connect() (TODO, step 8+).
    db_backend: Literal["duckdb", "postgres"] = "duckdb"
    db_path: Path | None = None  # defaults to data_dir / "radar.duckdb"

    # Raw-document lake encryption key (Fernet). Either the key itself, or a file.
    lake_key: str | None = None
    lake_key_file: Path = Path.home() / ".config" / "allocator-radar" / "lake.key"

    # Declared user agent. No personal contact string by default.
    user_agent: str = "AllocatorRadar/0.1 (public-records research; GET-only; honors robots.txt)"
    min_host_delay_s: float = 10.0

    # LLM. Model names live here (config), never in code.
    # claude_code = headless `claude -p` on the owner's Claude subscription (default; local Mac and VPS).
    # anthropic   = Anthropic API key (ANTHROPIC_API_KEY). local = OpenAI-compatible stub.
    llm_backend: Literal["claude_code", "anthropic", "local"] = "claude_code"
    claude_bin: str = "claude"
    llm_timeout_s: int = 900
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_local_base_url: str = "http://127.0.0.1:8080/v1"  # OpenAI-compatible (stub)

    # Feature flags for deferred work (built later or never; see DECISIONS.md).
    flag_vision_fallback: bool = False  # TODO: page-image VLM fallback for scanned PDFs
    flag_presidio: bool = False  # TODO: Presidio recognizers in addition to regex + GLiNER
    flag_two_family_adjudication: bool = False  # TODO: second model family for TERMINATED/ON_WATCH
    flag_gliner: bool = True  # GLiNER PII pass in the scrubber (regex always runs)
    flag_docling_tables: bool = False  # Docling table structure on hit pages
    flag_ocr: bool = True  # local OCR (Apple Vision / Tesseract) for scanned pages; output is scrubbed like any text

    # Web / auth (step 8). "none" is for localhost only.
    auth_mode: Literal["none", "basic"] = "none"
    auth_user: str = "owner"
    auth_password_hash: str | None = None  # sha256 hex of the password
    reviewer: str = "owner"  # reviewer = repo owner (AGENDA §10 Q11)
    # Public read-only site (D-B14): GET only; review queue, tools and export hidden; pending signals
    # hidden; /doc/<sha> redirects to the original public URL instead of serving the encrypted lake.
    public_mode: bool = False

    @property
    def duckdb_path(self) -> Path:
        return self.db_path or (self.data_dir / "radar.duckdb")

    @property
    def lake_dir(self) -> Path:
        return self.data_dir / "lake"


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml(name: str) -> Any:
    path = get_settings().config_dir / name
    with path.open() as fh:
        return yaml.safe_load(fh)


@functools.lru_cache
def wall_config() -> dict[str, Any]:
    return load_yaml("wall.yaml")


@functools.lru_cache
def sources_config() -> dict[str, Any]:
    return load_yaml("sources.yaml")


def readiness_config() -> dict[str, Any]:
    return load_yaml("readiness_rules.yaml")


def review_policy() -> dict[str, Any]:
    try:
        return load_yaml("review_policy.yaml") or {}
    except FileNotFoundError:
        return {}


def firm_posture() -> str:
    posture = load_yaml("firm_posture.yaml").get("posture", "both")
    if posture not in {"ria", "cpo_cta", "both", "none"}:
        raise ValueError(f"config/firm_posture.yaml: unknown posture {posture!r}")
    return posture
