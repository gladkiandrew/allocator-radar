"""Wall checks that run against a store (the live one nightly, a temp one in tests).

check_all() returns a list of violations; empty = the Wall holds.
  1. schema:      no column name matches the banned pattern
  2. sweep:       no stored text value contains a contact-shaped span
  3. egress:      no non-GET/HEAD request except POSTs to the LLM host; no mailto
  4. canary:      a synthetic packet with fake contact blocks leaves zero contact tokens
  5. deps:        no banned package in uv.lock; no banned import in src/
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tempfile
from pathlib import Path

from radar import db
from radar.settings import REPO_ROOT, wall_config
from radar.wall.scrubber import detect

CANARY_TOKENS = [
    "canary.trustee@example.invalid", "canary-ops@example.invalid", "305-555-0177", "(954) 555-0142",
    "561.555.0199", "4471 Canary Lane", "PO Box 7731", "FL 33131", "linkedin.com/in/canary-trustee",
    "x.com/canarytrustee",
]


def check_schema(con) -> list[str]:
    pat = re.compile(wall_config()["banned_column_pattern"], re.I)
    cols = con.execute("SELECT table_name, column_name FROM information_schema.columns").fetchall()
    return [f"schema: banned column {t}.{c}" for t, c in cols if pat.search(c)]


def check_models() -> list[str]:
    from pydantic import BaseModel

    from radar import models

    pat = re.compile(wall_config()["banned_column_pattern"], re.I)
    out = []
    for name in dir(models):
        obj = getattr(models, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            if obj.model_config.get("extra") != "forbid":
                out.append(f"models: {name} does not forbid extra fields")
            out += [f"models: banned field {name}.{f}" for f in obj.model_fields if pat.search(f)]
    return out


def check_sweep(con, extra_tokens: list[str] | None = None) -> list[str]:
    out = []
    tables = con.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE data_type = 'VARCHAR'"
    ).fetchall()
    tokens = [t.lower() for t in (extra_tokens or [])]
    for table, col in tables:
        url_col = (table, col) in {("egress_log", "url"), ("document_urls", "url"), ("document_urls", "listed_on"),
                                   ("evidence", "source_url"), ("allocators", "home_url"), ("allocator_pages", "url")}
        for (val,) in con.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL').fetchall():
            hits = detect(val)
            if url_col:  # public document URLs: digit runs in filenames are not phone numbers
                hits = [h for h in hits if h.kind in ("email", "mailto", "social_url")]
            if hits:
                out.append(f"sweep: {table}.{col} holds {hits[0].kind}: {hits[0].text[:40]!r}")
            low = val.lower()
            out += [f"sweep: canary token in {table}.{col}" for t in tokens if t in low]
    return out


def check_lance(extra_tokens: list[str] | None = None) -> list[str]:
    from radar import search

    out = []
    tokens = [t.lower() for t in (extra_tokens or [])]
    for text in search.all_payloads():
        hits = detect(text)
        if hits:
            out.append(f"vector: payload holds {hits[0].kind}")
        out += ["vector: canary token in payload" for t in tokens if t in text.lower()]
    return out


def check_egress(con) -> list[str]:
    cfg = wall_config()
    allowed_methods = set(cfg["crawler_methods"])
    post_hosts = set(cfg["post_allowed_hosts"])
    out = []
    for method, host, url, purpose, err in con.execute("SELECT method, host, url, purpose, error FROM egress_log").fetchall():
        if err and err.startswith("refused"):
            continue  # refused before any socket opened
        if method not in allowed_methods and not (method == "POST" and host in post_hosts and purpose == "llm"):
            out.append(f"egress: {method} {host} ({purpose})")
        if url.lower().startswith("mailto:"):
            out.append(f"egress: mailto request {url[:40]}")
    return out


def check_dependencies() -> list[str]:
    cfg = wall_config()
    out = []
    lock = (REPO_ROOT / "uv.lock").read_text() if (REPO_ROOT / "uv.lock").exists() else ""
    names = set(re.findall(r'^name = "([^"]+)"', lock, re.M))
    out += [f"deps: banned package {p} in uv.lock" for p in cfg["banned_packages"] if p in names]
    banned = cfg["banned_imports"]
    http_ok = {str(Path(p)) for p in cfg["http_client_modules"]}
    for path in (REPO_ROOT / "src").rglob("*.py"):
        rel = str(path.relative_to(REPO_ROOT / "src"))
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                for b in banned:
                    if m == b or m.startswith(b + "."):
                        out.append(f"deps: {rel} imports banned module {m}")
                if m.split(".")[0] in ("httpx", "playwright") and rel not in http_ok:
                    out.append(f"deps: {rel} imports httpx (only {sorted(http_ok)} may)")
    return out


def run_canary() -> list[str]:
    """Push the canary packet through the real ingest + parse path in a temp store."""
    import os

    from radar import settings as S

    with tempfile.TemporaryDirectory() as tmp:
        old = dict(os.environ)
        os.environ["RADAR_DATA_DIR"] = tmp
        os.environ["RADAR_LAKE_KEY_FILE"] = str(Path(tmp) / "lake.key")
        S.get_settings.cache_clear()
        try:
            from radar import ingest
            from radar.parse import pdf
            from radar.wall.canary import canary_pdf

            db.init_db()
            sha, _ = ingest.store_document(canary_pdf(), url="https://canary.invalid/packet.pdf",
                                           allocator_id="canary", content_type="application/pdf")
            pdf.parse_document(sha)
            with db.connect() as con:
                return check_sweep(con, CANARY_TOKENS)
        finally:
            os.environ.clear()
            os.environ.update(old)
            S.get_settings.cache_clear()


def check_all(*, include_canary: bool = True) -> list[str]:
    v = check_models() + check_dependencies()
    with db.connect() as con:
        v += check_schema(con) + check_sweep(con) + check_egress(con)
    v += check_lance()  # never skipped silently: a failure to read the vector store fails the wall
    from radar.sources.endowments import wall as ef_wall

    v += ef_wall.check()  # Phase C store (data/ef.duckdb)
    if include_canary:
        v += run_canary()
    return v


def main() -> int:
    db.init_db()
    violations = check_all()
    with db.connect() as con:
        # D-B33: the audit row names table, column and kind only. Echoing the matched text stored the very contact
        # detail the check had found, so the next run failed on the audit log itself.
        import re as _re
        safe = [_re.sub(r"(holds \w+):.*$", r"\1", v) for v in violations[:50]]
        db.audit(con, "watchdog", "wall_check", status="fail" if violations else "ok",
                 detail={"violations": safe, "n": len(violations)})
    if violations:
        print("WALL FAILED:", file=sys.stderr)
        for v in violations:
            print("  " + v, file=sys.stderr)
        return 1
    print(json.dumps({"wall": "ok"}))
    return 0
