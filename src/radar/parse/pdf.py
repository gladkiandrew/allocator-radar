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
PARSER = "pdfplumber+ocr"


def _html_text(data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return [re.sub(r"\n\s*\n+", "\n\n", text).strip()]


OCR_MIN_CHARS = 50  # pages with less text than this are treated as scanned images


def _ocr_engine():
    """Local OCR only (never an LLM): Apple Vision on macOS, Tesseract on Linux. None if unavailable."""
    try:
        from ocrmac import ocrmac  # type: ignore[import-not-found]

        return lambda img: "\n".join(r[0] for r in ocrmac.OCR(img, recognition_level="accurate",
                                                               language_preference=["en-US"]).recognize())
    except ImportError:
        pass
    try:
        import pytesseract  # type: ignore[import-not-found]

        pytesseract.get_tesseract_version()
        return lambda img: pytesseract.image_to_string(img)
    except Exception:
        return None


def _ocr_pages(data: bytes, page_nos: list[int]) -> dict[int, str]:
    from radar.settings import get_settings

    if not page_nos or not get_settings().flag_ocr:
        return {}
    engine = _ocr_engine()
    if engine is None:
        log.warning("no local OCR engine; %d scanned pages left empty", len(page_nos))
        return {}
    import pypdfium2 as pdfium

    out = {}
    doc = pdfium.PdfDocument(data)
    try:
        for n in page_nos:
            try:
                out[n] = engine(doc[n - 1].render(scale=2).to_pil())
            except Exception as e:
                log.warning("OCR failed on page %d: %s", n, e)
    finally:
        doc.close()
    return out


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
        scanned = [i + 1 for i, t in enumerate(pages) if len(t.strip()) < OCR_MIN_CHARS]
        for n, txt in _ocr_pages(data, scanned).items():
            if len(txt.strip()) > len(pages[n - 1].strip()):
                pages[n - 1] = txt
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
        from radar.parse.classify import classify_text, content_date

        head = "\n".join(c[2] for c in cleaned[:2])
        con.execute(
            "UPDATE documents SET parsed_at = now(), parse_method = ?, page_count = ?, parse_error = ?, doc_kind = ?, "
            "content_date = ? WHERE sha256 = ?",
            [PARSER, len(cleaned), err, classify_text(head), content_date(cleaned[0][2] if cleaned else ""), sha],
        )
        db.audit(con, "parser", "parse", sha, status="error" if err else "ok",
                 detail={"pages": len(cleaned), "redactions": sum(c[3] for c in cleaned), "error": err})
    return len(cleaned)
