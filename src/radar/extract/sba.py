"""Florida SBA (FRS Pension Plan) reports from the manual inbox.

The owner downloads these by hand in a browser (sbafla.com blocks automated clients; we never fetch it).
  - ingest(): MANUAL provenance, retrieved_by = human, date and type from the file name
  - New Investments / New Manager reports (ruled tables) -> one signal per row, deterministic (no LLM):
        New Manager? = Yes -> MANDATE_AWARDED (qualifier manager), else COMMITMENT_MADE
        event_date = last day of the report quarter (the reports print no per-row date)
  - Strategic Investments report with a ruled table -> current holdings by fund (mandates), deterministic
  - everything else (text-layout SI quarters, PE performance, Annual Investment Report) -> the regular extractor
Every cell is scrubbed before storage and every row is checked against the scrubbed page text.
All signals are created as review_status = 'new'.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date
from pathlib import Path

from radar import db, ingest, lake
from radar.buckets import hf_relevant, to_bucket
from radar.models import new_id
from radar.parse import pdf
from radar.settings import get_settings
from radar.wall.lint import lint
from radar.wall.scrubber import detect, scrub_text

log = logging.getLogger(__name__)
ALLOCATOR = "fl-sba-frs"
EXTRACTOR = "sba-table-parser"
VERSION = "0.1.0"
SITE = "https://www.sbafla.com/"


def _quarter_end(name: str) -> date | None:
    m = re.search(r"q([1-4])-(20\d{2})", name)
    if not m:
        return None
    q, y = int(m[1]), int(m[2])
    return date(y, q * 3, 30 if q in (2, 3) else 31)


def classify_file(name: str) -> tuple[str, str, date | None]:
    n = name.lower()
    if "air" in n:  # Annual Investment Report, fiscal year July 1 - June 30
        m = re.search(r"(20\d{2})-(20\d{2})", n)
        return "ANNUAL_REPORT", "PERFORMANCE", date(int(m[2]), 6, 30) if m else None
    if "new-investment" in n or "new-manager" in n:
        return "NEW_INVESTMENTS", "OTHER", _quarter_end(n)
    if "si-performance" in n:
        return "SI_PERFORMANCE", "PERFORMANCE", _quarter_end(n)
    if "pe-performance" in n:
        return "PE_PERFORMANCE", "PERFORMANCE", _quarter_end(n)
    return "OTHER", "OTHER", _quarter_end(n)


def _title(name: str) -> str:
    return re.sub(r"[-_]+", " ", Path(name).stem).replace("reportfinal", "report final").strip().title()


def ingest_inbox(actor: str = "human") -> list[dict]:
    folder = get_settings().manual_inbox / ALLOCATOR
    out = []
    for path in sorted(folder.glob("*.pdf")):
        kind, doc_type, d = classify_file(path.name)
        data = path.read_bytes()
        sha, new = ingest.store_document(
            data, url=f"manual://{ALLOCATOR}/{path.name}", allocator_id=ALLOCATOR, content_type="application/pdf",
            provenance="MANUAL", title=_title(path.name), doc_type=doc_type, doc_date=d,
            listed_on=f"{SITE} (downloaded by hand in a browser; exact URL not recorded)")
        with db.connect() as con:
            con.execute("UPDATE document_urls SET retrieved_by = ? WHERE sha256 = ? AND allocator_id = ?", [actor, sha, ALLOCATOR])
            db.audit(con, actor, "manual_ingest", sha, detail={"file": path.name, "kind": kind, "new": new,
                                                               "retrieved_at": date.today().isoformat()})
        pdf.parse_document(sha)
        out.append({"sha": sha, "file": path.name, "kind": kind, "doc_date": d, "new": new})
    return out


def _cell(c: str | None) -> str:
    return re.sub(r"\s+", " ", (c or "")).strip()


def _money(s: str) -> float | None:
    t = s.replace("$", "").replace(",", "").replace(" ", "")
    try:
        return float(t)
    except ValueError:
        return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _tables(sha: str):
    import pdfplumber

    with pdfplumber.open(io.BytesIO(lake.get(sha))) as doc:
        for i, page in enumerate(doc.pages, start=1):
            for t in page.extract_tables():
                yield i, t


def _page_text(con, sha: str, page: int) -> str:
    r = con.execute("SELECT text_scrubbed FROM pages WHERE sha256 = ? AND page_no = ?", [sha, page]).fetchone()
    return r[0] if r else ""


def _on_page(cells: list[str], page_text: str) -> bool:
    p = _norm(page_text)
    return all(_norm(c) in p for c in cells if c and len(c) > 2)


def parse_new_investments(sha: str, quarter_end: date, file_name: str) -> dict:
    stats = {"rows": 0, "signals": 0, "hf": 0, "failed": []}
    rows = []
    for page, table in _tables(sha):
        header = None
        for raw in table:
            cells = [_cell(c) for c in raw]
            low = [c.lower() for c in cells]
            if "asset class" in low and any("manager" in c for c in low):
                header = low
                continue
            if header is None or not any(cells):
                continue
            row = dict(zip(header, cells))
            rows.append((page, row, cells))
    q = (quarter_end.month - 1) // 3 + 1
    with db.connect() as con:
        for page, row, cells in rows:
            stats["rows"] += 1
            manager = row.get("manager name", "")
            account = row.get("account name", "")
            amount = _money(row.get("commitment", ""))
            asset = row.get("asset class", "")
            style = row.get("investment style", "")
            consultant = row.get("consultant", "")
            new_mgr = row.get("new manager?", row.get("new manager", "")).strip().lower().startswith("y")
            if not manager or amount is None:
                stats["failed"].append(f"{file_name} p{page}: unreadable row {cells}")
                continue
            clean = [scrub_text(c) for c in cells]
            if clean != cells or any(detect(c) for c in clean):
                stats["failed"].append(f"{file_name} p{page}: row had contact-shaped text, skipped")
                continue
            if not _on_page([manager.split()[0], account.split()[0] if account else ""], _page_text(con, sha, page)):
                stats["failed"].append(f"{file_name} p{page}: row not found in page text: {manager} / {account}")
                continue
            label = f"{asset} / {style}".strip(" /")
            bucket = to_bucket(asset, style, account)
            hf = hf_relevant(bucket, asset, style, account)
            stype = "MANDATE_AWARDED" if new_mgr else "COMMITMENT_MADE"
            summary = (f"The SBA reported a ${amount:,.0f} commitment to {account or manager} ({manager}), "
                       f"{label}, in its Q{q} {quarter_end.year} new investments report"
                       f"{'; new manager relationship' if new_mgr else ''}.")[:300]
            if not lint(summary).ok:
                stats["failed"].append(f"{file_name} p{page}: summary failed lint: {summary}")
                continue
            key = f"sba|{stype}|{manager.lower()}|{account.lower()}|{amount}|{quarter_end}"
            if con.execute("SELECT 1 FROM signals WHERE dedupe_key = ?", [key]).fetchone():
                continue
            eid, sid = new_id(), new_id()
            quote = " | ".join(c for c in clean if c)[:500]
            con.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, NULL, now(), 1.0, 'MANUAL')",
                        [eid, sha, f"manual://{ALLOCATOR}/{file_name}", page, quote, EXTRACTOR, VERSION])
            con.execute(
                "INSERT INTO signals (signal_id, allocator_id, signal_type, qualifier, event_date, observed_at, summary, "
                "review_status, origin, dedupe_key, manager_name, amount_usd, bucket_label, bucket, hf_relevant, review_note) "
                "VALUES (?, ?, ?, ?, ?, now(), ?, 'new', 'MACHINE', ?, ?, ?, ?, ?, ?, ?)",
                [sid, ALLOCATOR, stype, "manager" if new_mgr else None, quarter_end, summary, key, manager, amount,
                 label, bucket, hf, f"event date = quarter end (report prints no per-row date); consultant: {consultant}"])
            con.execute("INSERT INTO row_evidence VALUES ('signal', ?, ?)", [sid, eid])
            stats["signals"] += 1
            stats["hf"] += int(hf)
        db.audit(con, "extractor", "sba_new_investments", sha, detail={k: v for k, v in stats.items() if k != "failed"}
                 | {"failed": len(stats["failed"])})
    return stats


def parse_si_table(sha: str, as_of: date, file_name: str) -> dict:
    stats = {"rows": 0, "holdings": 0, "hf": 0, "failed": [], "tabular": False}
    with db.connect() as con:
        for page, table in _tables(sha):
            header = None
            for raw in table:
                cells = [_cell(c) for c in raw]
                low = [c.lower() for c in cells]
                if any("holding" in c for c in low):
                    header = low
                    stats["tabular"] = True
                    continue
                if header is None or not any(cells):
                    continue
                row = dict(zip(header, cells))
                category = row.get("asset type by level", "")
                name = row.get("holding name", "")
                mv = _money(row.get("adjusted market value", ""))
                if not name or "total" in name.lower() or mv is None:
                    continue
                stats["rows"] += 1
                clean = [scrub_text(c) for c in cells]
                if clean != cells:
                    stats["failed"].append(f"{file_name} p{page}: row had contact-shaped text, skipped")
                    continue
                if not _on_page([name.split()[0]], _page_text(con, sha, page)):
                    stats["failed"].append(f"{file_name} p{page}: holding not found in page text: {name}")
                    continue
                bucket = to_bucket(category, name)
                key = f"sba-si|{name.lower()}|{as_of}"
                if con.execute("SELECT 1 FROM mandates WHERE dedupe_key = ?", [key]).fetchone():
                    continue
                eid, mid = new_id(), new_id()
                con.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, NULL, now(), 1.0, 'MANUAL')",
                            [eid, sha, f"manual://{ALLOCATOR}/{file_name}", page, " | ".join(c for c in clean if c)[:500],
                             EXTRACTOR, VERSION])
                con.execute("INSERT INTO mandates VALUES (?, ?, ?, ?, ?, ?, 'market_value', ?, ?, NULL, ?)",
                            [mid, ALLOCATOR, name[:200], f"Strategic Investments: {category}"[:200], bucket, mv, as_of,
                             "ACTIVE" if mv > 0 else "UNKNOWN", key])
                con.execute("INSERT INTO row_evidence VALUES ('mandate', ?, ?)", [mid, eid])
                stats["holdings"] += 1
                stats["hf"] += int(hf_relevant(bucket, category, name))
        db.audit(con, "extractor", "sba_si_table", sha, detail={k: v for k, v in stats.items() if k != "failed"}
                 | {"failed": len(stats["failed"])})
    return stats


def mark_extracted(sha: str) -> None:
    """Keep the generic extractor off documents the deterministic parser has handled."""
    from radar.extract.extractor import EXTRACTOR_VERSION

    with db.connect() as con:
        con.execute("INSERT INTO extraction_runs VALUES (?, 0, ?, now(), 'sba-parser') ON CONFLICT DO NOTHING",
                    [sha, EXTRACTOR_VERSION])
