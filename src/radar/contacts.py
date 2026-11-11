"""Contacts (D-B35, owner 2026-11-11): names, titles, emails, phones and office addresses shown in documents we hold.

recover(): re-parse, from the encrypted originals already on disk (no re-crawl), every page whose stored text had contact
details scrubbed under the old Wall. The page text is replaced with the unscrubbed text when the re-parse is at least as
complete (OCR pages that pdfplumber cannot read keep their old text), and contact rows are extracted from it.

extract(): deterministic. Each email, phone and office address is a contact row; the person's name and title are taken
from the same line or the two lines above it when they look like "Name, Title" or "Title Name". An address or phone with
no person nearby is an office contact (name NULL). Every row cites document, page and the source line. Individual
compensation is never extracted: lines with a pay amount next to a name are skipped.
"""

from __future__ import annotations

import hashlib
import io
import re

from radar import db

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?([2-9]\d{2})\)?[\s.-]?(\d{3})[\s.-](\d{4})(?!\d)")
ADDRESS = re.compile(r"\b\d{2,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][A-Za-z0-9.'-]*\s+){1,4}"
                     r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Parkway|Pkwy|"
                     r"Highway|Hwy|Circle|Terrace|Trail|Plaza|Square)\.?(?:,?\s+(?:Suite|Ste\.?|Floor|Room|Unit|#)\s*[\w-]+)?"
                     r"(?:,?\s+[A-Z][A-Za-z .'-]+,?\s+(?:FL|Florida)\.?\s+\d{5}(?:-\d{4})?)?")
TITLE = re.compile(r"\b(?:Executive Director|Plan Administrator|Pension Administrator|Administrator|Chair(?:man|woman|person)?|"
                   r"Vice[- ]Chair\w*|Secretary|Treasurer|Trustee|Board Member|Director|President|Vice President|"
                   r"Investment Consultant|Consultant|Senior Consultant|Attorney|Counsel|Actuary|Custodian|Clerk|"
                   r"Coordinator|Manager|Assistant|Analyst|Chief Investment Officer|CIO|CFO|Finance Director|"
                   r"Pension Coordinator|Benefits Manager|Fund Administrator|Partner|Principal|Officer)\b", re.I)
NAME = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z'-]+){1,2})\b")
PAY = re.compile(r"\$\s?\d[\d,]{3,}|\bsalary\b|\bcompensation\b|\bper (?:hour|year|annum)\b", re.I)
NOT_NAME = {"Board Of", "Plan Administrator", "Executive Director", "Police Officers", "Retirement System", "Pension Fund",
            "Pension Plan", "Board Meeting", "City Hall", "Regular Meeting", "Special Meeting", "Fire Rescue", "Trust Fund",
            "Investment Consultant", "General Employees", "Firefighters Pension", "Public Comment"}


def _name_title(lines: list[str]) -> tuple[str | None, str | None]:
    blob = " | ".join(lines)
    t = TITLE.search(blob)
    for m in NAME.finditer(blob):
        n = m.group(1)
        if n in NOT_NAME or TITLE.fullmatch(n) or any(w in n for w in ("Florida", "Beach", "City", "County", "Street", "Avenue")):
            continue
        return n, (t.group(0) if t else None)
    return None, (t.group(0) if t else None)


def extract(text: str) -> list[dict]:
    out, seen = [], set()
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        found = [("email", m.group(0)) for m in EMAIL.finditer(line)]
        found += [("phone", f"({m.group(1)}) {m.group(2)}-{m.group(3)}") for m in PHONE.finditer(line)]
        found += [("office_address", " ".join(m.group(0).split())) for m in ADDRESS.finditer(line)]
        if not found:
            continue
        ctx = lines[max(0, i - 2):i + 1]
        if any(PAY.search(c) for c in ctx):
            continue  # never next to a pay amount
        name, title = _name_title(ctx)
        for kind, val in found:
            key = (kind, val.lower(), (name or "").lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": kind, "value": val, "name": name, "title": title, "context": " / ".join(c.strip() for c in ctx)[:400]})
    return out


SCHEMA = """CREATE TABLE IF NOT EXISTS contacts (
    contact_id VARCHAR PRIMARY KEY, allocator_id VARCHAR, name VARCHAR, title VARCHAR, email VARCHAR, phone VARCHAR,
    office_address VARCHAR, role_type VARCHAR, document_sha256 VARCHAR, page INTEGER, source_url VARCHAR, context VARCHAR,
    method VARCHAR, extracted_at TIMESTAMPTZ DEFAULT now())"""


def _store(con, aid: str, sha: str, page: int, url: str, rows: list[dict], method: str) -> int:
    n = 0
    for r in rows:
        cid = hashlib.sha256(f"{aid}|{r['kind']}|{r['value'].lower()}|{(r['name'] or '').lower()}".encode()).hexdigest()[:24]
        if con.execute("SELECT 1 FROM contacts WHERE contact_id = ?", [cid]).fetchone():
            continue
        con.execute("INSERT INTO contacts (contact_id, allocator_id, name, title, email, phone, office_address, role_type, "
                    "document_sha256, page, source_url, context, method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [cid, aid, r["name"], r["title"], r["value"] if r["kind"] == "email" else None,
                     r["value"] if r["kind"] == "phone" else None, r["value"] if r["kind"] == "office_address" else None,
                     "person" if r["name"] else "office", sha, page, url, r["context"], method])
        n += 1
    return n


def recover(limit: int | None = None, rewrite_pages: bool = True) -> dict:
    """Re-parse scrubbed pages from the lake and extract contacts. Idempotent."""
    import pdfplumber

    from radar import lake
    stats = {"docs": 0, "pages_reparsed": 0, "pages_rewritten": 0, "contacts": 0, "errors": 0}
    with db.connect() as con:
        con.execute(SCHEMA)
        docs = con.execute("""SELECT p.sha256, list(p.page_no ORDER BY p.page_no), any_value(d.content_type)
                              FROM pages p JOIN documents d USING (sha256) WHERE p.redactions > 0 GROUP BY 1""").fetchall()
    for sha, page_nos, ctype in docs[:limit] if limit else docs:
        stats["docs"] += 1
        try:
            data = lake.get(sha)
            if not data.startswith(b"%PDF"):
                continue
            new = {}
            with pdfplumber.open(io.BytesIO(data)) as doc:
                for pno in page_nos:
                    if 1 <= pno <= len(doc.pages):
                        new[pno] = doc.pages[pno - 1].extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad file never stops the run
            stats["errors"] += 1
            continue
        with db.connect() as con:
            owners = con.execute("SELECT allocator_id, any_value(url) FROM document_urls WHERE sha256 = ? GROUP BY 1", [sha]).fetchall()
            con.execute("BEGIN")
            for pno, txt in new.items():
                stats["pages_reparsed"] += 1
                old = con.execute("SELECT text_scrubbed FROM pages WHERE sha256 = ? AND page_no = ?", [sha, pno]).fetchone()[0] or ""
                use = txt if len(txt) >= 0.8 * len(old.replace("[REDACTED-CONTACT]", "")) else None
                if use and rewrite_pages:
                    con.execute("UPDATE pages SET text_scrubbed = ?, redactions = 0 WHERE sha256 = ? AND page_no = ?", [use, sha, pno])
                    stats["pages_rewritten"] += 1
                rows = extract(use or txt)
                for aid, url in owners:
                    stats["contacts"] += _store(con, aid, sha, pno, url, rows, "reparse-v1")
            con.execute("COMMIT")
    with db.connect() as con:
        db.audit(con, "data-session", "contacts_recover", "pages", detail=stats)
    return stats
