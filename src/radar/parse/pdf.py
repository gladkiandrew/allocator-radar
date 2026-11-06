"""Parse lake documents into scrubbed per-page text.

pdfplumber reads the text layer page by page (born-digital PDFs, which is most of what
FL plan sites post). Pages are scrubbed BEFORE they are written. Raw text never lands
in a table.

Deferred behind flags (see settings): Docling table structure on hit pages
(flag_docling_tables), and a vision fallback for scanned pages (flag_vision_fallback).
Scanned pages with no text layer are recorded with empty text and counted, so coverage
gaps are visible.
"""

from __future__ import annotations

import io
import json
import logging
import re

from radar import db, lake
from radar.extract.prefilter import match_terms
from radar.wall.scrubber import scrub

log = logging.getLogger(__name__)
PARSER = "pdfplumber"


def _html_text(data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return [re.sub(r"\n\s*\n+", "\n\n", text).strip()]


def extract_pages(data: bytes, content_type: str | None) -> list[str]:
    ctype = (content_type or "").lower()
    if data[:5] == b"%PDF-" or "pdf" in ctype:
        import pdfplumber

        pages = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                try:
                    pages.append(page.extract_text(x_tolerance=1.5, y_tolerance=3) or "")
                except Exception as e:  # malformed page: keep going, record empty
                    log.warning("page parse failed: %s", e)
                    pages.append("")
                finally:
                    page.close()
        return pages
    if "html" in ctype or data.lstrip()[:1] == b"<":
        return _html_text(data)
    return [data.decode("utf-8", errors="replace")]


def parse_document(sha: str, *, force: bool = False) -> int:
    """Parse one lake document into scrubbed pages. Returns page count."""
    with db.connect() as con:
        row = con.execute("SELECT content_type, parsed_at FROM documents WHERE sha256 = ?", [sha]).fetchone()
    if row is None:
        raise KeyError(sha)
    if row[1] is not None and not force:
        return -1
    try:
        raw_pages = extract_pages(lake.get(sha), row[0])
        err = None
    except Exception as e:
        raw_pages, err = [], repr(e)[:300]
    cleaned = []
    for i, txt in enumerate(raw_pages, start=1):
        res = scrub(txt)
        terms = match_terms(res.text)
        cleaned.append((sha, i, res.text, res.redactions, json.dumps(terms) if terms else None))
    with db.connect() as con:
        con.execute("DELETE FROM pages WHERE sha256 = ?", [sha])
        if cleaned:
            con.executemany("INSERT INTO pages VALUES (?, ?, ?, ?, ?)", cleaned)
        con.execute(
            "UPDATE documents SET parsed_at = now(), parse_method = ?, page_count = ?, parse_error = ? WHERE sha256 = ?",
            [PARSER, len(cleaned), err, sha],
        )
        db.audit(con, "parser", "parse", sha, status="error" if err else "ok",
                 detail={"pages": len(cleaned), "redactions": sum(c[3] for c in cleaned), "error": err})
    return len(cleaned)
