"""Ingest what the browser collection subagents saved (D-B34).

Subagents never open a database. They download public documents into data/staging/<group>/<id>/ and describe each
file in data/staging/<group>/manifest.yaml (url, listing page, title, type, date). This module is the single writer:
it re-checks each file (a real document, its URL's host is allowlisted and robots-allowed for our tokens), stores it in
the lake with its real public URL, and parses it. Provenance is CRAWL with retrieved_by = 'browser-agent'.
Pension ids are allocator ids; E&F ids are EINs (stored in ef_documents for the organization).
"""

from __future__ import annotations

import hashlib
import mimetypes
from datetime import date
from pathlib import Path

import yaml

from radar import db, ingest
from radar.settings import get_settings

RETRIEVED_BY = "browser-agent"


def _manifest(group: str) -> tuple[Path, dict]:
    root = get_settings().data_dir / "staging" / group
    return root, (yaml.safe_load((root / "manifest.yaml").read_text()) or {})


def _robots_ok(url: str) -> bool:
    from radar.net.client import PoliteClient
    c = PoliteClient(purpose="staging-check")
    try:
        return c.robots_allows(url)[0]
    finally:
        c.close()


def _is_doc(data: bytes) -> bool:
    return data[:5] == b"%PDF-" or data[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0")


def ingest_pensions(group: str, actor: str = "data-session") -> dict:
    from radar.parse import pdf
    root, m = _manifest(group)
    stats = {"files": 0, "new": 0, "skipped": []}
    with db.connect() as con:
        known = {a for (a,) in con.execute("SELECT allocator_id FROM allocators").fetchall()}
    for f in m.get("files") or []:
        path = root / f["file"]
        aid = f["id"]
        if aid not in known or not path.exists():
            stats["skipped"].append((f.get("file"), "unknown plan or missing file")); continue
        data = path.read_bytes()
        if not _is_doc(data):
            stats["skipped"].append((f["file"], "not a document")); continue
        if not _robots_ok(f["url"]):
            stats["skipped"].append((f["file"], "robots disallows the URL")); continue
        d = f.get("doc_date")
        sha, new = ingest.store_document(
            data, url=f["url"], allocator_id=aid, content_type=mimetypes.guess_type(path.name)[0] or "application/pdf",
            provenance="CRAWL", title=(f.get("title") or path.stem)[:200], doc_type=f.get("doc_type"),
            doc_date=date.fromisoformat(str(d)) if d else None, listed_on=f.get("listing_url"))
        with db.connect() as con:
            con.execute("UPDATE document_urls SET retrieved_by = ? WHERE sha256 = ? AND allocator_id = ? AND url = ?",
                        [RETRIEVED_BY, sha, aid, f["url"]])
        pdf.parse_document(sha)
        stats["files"] += 1; stats["new"] += int(new)
    with db.connect() as con:
        db.audit(con, actor, "staging_ingest", group, detail={k: v if k != "skipped" else len(v) for k, v in stats.items()})
    return stats


def ingest_ef(group: str) -> dict:
    """E&F files -> ef_documents (the organization's own published documents). Committee documents are read by
    `radar ef-committee`'s reader; audits and reports are recorded as sources."""
    from radar.sources.endowments import store
    root, m = _manifest(group)
    stats = {"files": 0, "skipped": []}
    store.init()
    for f in m.get("files") or []:
        path = root / f["file"]
        if not path.exists():
            stats["skipped"].append((f.get("file"), "missing")); continue
        data = path.read_bytes()
        if not _is_doc(data):
            stats["skipped"].append((f["file"], "not a document")); continue
        if not _robots_ok(f["url"]):
            stats["skipped"].append((f["file"], "robots disallows the URL")); continue
        from radar import lake
        sha = lake.put(data)
        pages = None
        if data[:5] == b"%PDF-":
            import io

            import pdfplumber
            with pdfplumber.open(io.BytesIO(data)) as doc:
                pages = len(doc.pages)
        d = f.get("doc_date")
        with store.connect() as con:
            con.execute("INSERT OR REPLACE INTO ef_documents (sha256, ein, url, title, doc_date, doc_type, page_count) "
                        "VALUES (?,?,?,?,?,?,?)", [sha, str(f["id"]), f["url"], (f.get("title") or path.stem)[:200],
                                                   date.fromisoformat(str(d)) if d else None, f.get("doc_type"), pages])
        stats["files"] += 1
    return stats
