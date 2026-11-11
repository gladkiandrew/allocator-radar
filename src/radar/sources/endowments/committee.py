"""Committee and board documents that an E&F organization publishes itself (agendas, minutes, meeting binders).

These are the only E&F source that can set DEPLOYING or PREPARING (same readiness rules as the pensions).
Listing pages are fetched through radar.net.client (allowlist, robots.txt, delay); PDFs are read in memory,
every page is scrubbed before it reaches the model, and only document metadata plus verified quotes are stored.

A proposed signal is kept only if its quote appears on the cited page. It is auto-approved only when the quote
matches, the summary passes the language lint, and the type is not one that always goes to a person
(config/auto_approval.yaml excluded_signal_types). Everything else waits as 'new' and never shows publicly.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import date
from typing import Literal
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field

from radar.sources.endowments import store
from radar.wall.lint import lint

log = logging.getLogger(__name__)

# Organization -> its own published meeting-document pages. Hosts are allowlisted in config/sources.yaml (Phase C).
SOURCES = {
    "596166292": {"name": "UWF Foundation", "pages": ["https://uwf.edu/university-advancement/departments/foundation/board/meeting-documents/"]},
    "590917284": {"name": "FAU Foundation", "pages": ["https://fauf.fau.edu/past-meeting-agendas/"]},
}
# UCF Foundation (596211832) is not read: its Board of Directors page (ucffoundation.org/?p=429) now redirects to a 404
# on advance.ucf.edu, and the newest public minutes found are from April 2025. Listed in data/manual_downloads.md.
DOC_LINK = re.compile(r"invest|minutes|agenda|binder|executive|board|meeting|committee", re.I)
RECENT = re.compile(r"20(25|26)")
PER_ORG = 8
PREFILTER = re.compile(r"invest|manager|allocation|asset class|search|RFP|request for proposal|hire|terminat|commit|"
                       r"rebalanc|hedge|absolute return|private (equity|credit)|real estate|consultant|policy statement", re.I)
MAX_PAGES = 14


class CSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_type: Literal["SEARCH_OPENED", "SEARCH_SCHEDULED", "RFP_ISSUED", "FINALISTS_NAMED", "MANDATE_AWARDED",
                         "MANDATE_FUNDED", "COMMITMENT_MADE", "MANAGER_TERMINATED", "MANAGER_ON_WATCH",
                         "TARGET_ALLOCATION_CHANGED", "IPS_AMENDED", "CONSULTANT_CHANGED", "REBALANCE_APPROVED"]
    qualifier: Literal["manager", "consultant", "increase", "decrease", "none"] = "none"
    event_date: date = Field(description="the meeting or action date printed in the document")
    summary: str = Field(description="one factual sentence; verbs like approved, voted, reported, issued, awarded")
    page: int = Field(description="1-based page number in the excerpt where the quote appears")
    quote: str = Field(description="an exact, contiguous quote (12-60 words) copied from that page")
    hedge_fund_related: bool = False


class CResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_date: date | None = Field(default=None, description="meeting date of this document, if printed")
    document_kind: Literal["MINUTES", "AGENDA", "MATERIALS", "OTHER"] = "OTHER"
    signals: list[CSignal] = []


SYSTEM = """You read public meeting documents published by a Florida university foundation (board, executive \
committee and investment committee agendas, minutes and meeting materials). Report only investment actions about \
the foundation's own endowment or investment pool: manager searches or RFPs, finalists, hires or awards, commitments \
to funds, terminations or watch-list placements, target allocation changes, investment policy amendments, \
investment consultant changes, and approved rebalancing. Ignore fundraising, scholarships, budgets, real estate the \
foundation occupies, and performance reviews with no decision. Each signal needs the date printed for the meeting or \
action, the page number from the excerpt headers, and an exact quote copied from that page. Summaries are one \
factual sentence with neutral verbs (approved, voted, reported, issued, awarded, committed, terminated); never \
evaluate performance. Set qualifier to "manager" for investment-manager searches/hires/awards, "consultant" for \
consultant ones, "increase"/"decrease" for allocation changes. If nothing qualifies, return an empty list."""


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9$%.]+", " ", s.lower()).split())


def quote_on_page(quote: str, page_text: str) -> bool:
    q = _norm(quote)
    return len(q) >= 20 and q in _norm(page_text)


def discover(client, ein: str) -> list[tuple[str, str]]:
    """(url, link text) of recent meeting PDFs on the organization's own pages."""
    out, seen = [], set()
    for page in SOURCES[ein]["pages"]:
        try:
            html = client.get(page).content.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 - blocked or failed: record it, never work around it
            from radar.sources.endowments.pipeline import manual_note
            manual_note(f"{SOURCES[ein]['name']} meeting documents ({page})", f"{type(e).__name__}: {str(e)[:120]}")
            continue
        for href, text in re.findall(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', html, re.I | re.S):
            url = urljoin(page, href.replace("&amp;", "&"))
            label = " ".join(re.sub(r"<[^>]+>", " ", text).split()) or url.rsplit("/", 1)[-1]
            if url in seen or not DOC_LINK.search(url + " " + label) or not RECENT.search(url + " " + label):
                continue
            seen.add(url)
            out.append((url, label))
    # investment-committee items first, then newest-looking (by the year/month digits in the URL)
    out.sort(key=lambda u: (not re.search(r"invest", u[0] + u[1], re.I), [-int(x) for x in re.findall(r"\d+", u[0])[-3:]]))
    return out[:PER_ORG]


_COMMITTEE = [(re.compile(p, re.I), n) for p, n in [
    (r"invest", "Investment Committee"), (r"exec", "Executive Committee"), (r"audit", "Audit Committee"),
    (r"financ", "Finance Committee"), (r"full[- ]?board|board[- ]of[- ]directors|fbod|\bboard\b", "Board of Directors")]]
_KIND = {"MINUTES": "minutes", "AGENDA": "agenda", "MATERIALS": "materials"}


def citation_label(org: str, label: str, url: str, doc_date: date | None, kind: str | None) -> str:
    """Our own citation label (D-B27): organization, committee, document kind and the meeting date read from the
    document. The listing's link text is not used for the date, because it can be wrong (UWF lists the August 26,
    2026 Executive Committee minutes as "9/26/26 Executive Committee Minutes")."""
    hay = f"{label} {url.rsplit('/', 1)[-1]}"
    committee = next((n for pat, n in _COMMITTEE if pat.search(hay)), "")
    k = _KIND.get(kind or "", "") or ("minutes" if re.search(r"minute", hay, re.I) else
                                      "agenda" if re.search(r"agenda", hay, re.I) else "document")
    when = f", {doc_date:%B} {doc_date.day}, {doc_date.year}" if doc_date else f" ({label[:80]})"
    return " ".join(x for x in (org, committee, k) if x) + when


def _pages(pdf: bytes) -> list[str]:
    import pdfplumber

    from radar.wall.scrubber import scrub_text

    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return [scrub_text(p.extract_text() or "") for p in doc.pages]


def read_document(client, provider, ein: str, url: str, label: str) -> dict:
    from radar.settings import load_yaml

    with store.connect() as con:
        if con.execute("SELECT 1 FROM ef_documents WHERE ein = ? AND url = ?", [ein, url]).fetchone():
            return {"url": url, "skipped": "already read"}
    r = client.get(url)
    if r.status != 200 or not r.content.startswith(b"%PDF"):
        return {"url": url, "error": f"status {r.status}, not a PDF"}
    return read_bytes(provider, ein, url, label, r.content)


def read_bytes(provider, ein: str, url: str, label: str, content: bytes, org_name: str | None = None) -> dict:
    """Read a committee/board PDF already in hand (fetched now, or stored in the lake from a staged download, D-B34)."""
    from radar.settings import load_yaml

    class _R:  # keep the body below unchanged
        pass
    r = _R(); r.content = content
    pages = _pages(r.content)
    keep = [i for i, t in enumerate(pages) if PREFILTER.search(t)][:MAX_PAGES]
    res = CResult()
    if keep:
        excerpt = "\n\n".join(f"=== PAGE {i + 1} ===\n{pages[i][:6000]}" for i in keep)
        res, _ = provider.complete(system=SYSTEM, user=f"Document: {label}\nURL: {url}\n\n{excerpt}", schema=CResult,
                                   purpose="ef-committee")
    excluded = set((load_yaml("auto_approval.yaml") or {}).get("excluded_signal_types", []))
    sha = hashlib.sha256(r.content).hexdigest()
    cite_label = citation_label(org_name or SOURCES.get(ein, {}).get("name", ""), label, url, res.document_date, res.document_kind)
    kept = dropped = 0
    with store.connect() as con:
        con.execute("INSERT OR REPLACE INTO ef_documents (sha256, ein, url, title, doc_date, doc_type, page_count) "
                    "VALUES (?,?,?,?,?,?,?)", [sha, ein, url, label[:200], res.document_date, res.document_kind, len(pages)])
        for s in res.signals:
            if not (1 <= s.page <= len(pages)) or not quote_on_page(s.quote, pages[s.page - 1]):
                dropped += 1
                continue
            ok = lint(s.summary).ok and s.signal_type not in excluded
            sid = "efc-" + hashlib.sha256(f"{sha}|{s.page}|{s.signal_type}|{s.quote}".encode()).hexdigest()[:20]
            con.execute("""INSERT OR REPLACE INTO ef_signals (signal_id, ein, signal_type, qualifier, event_date, date_basis, summary,
                           source_type, source_url, doc_title, cite, page, quote, hf_relevant, review_status, reviewed_by)
                           VALUES (?,?,?,?,?,?,?,'committee',?,?,?,?,?,?,?,?)""",
                        [sid, ein, s.signal_type, None if s.qualifier == "none" else s.qualifier, s.event_date,
                         "meeting_date", s.summary, f"{url}#page={s.page}", cite_label[:160], f"{cite_label[:160]}, page {s.page}",
                         s.page, s.quote, s.hedge_fund_related, "auto_approved" if ok else "new",
                         "ef-committee (quote on page, lint)" if ok else None])
            kept += 1
    return {"url": url, "pages": len(pages), "excerpt_pages": len(keep), "signals": kept, "dropped_quote_not_on_page": dropped}


def run(client=None, provider=None) -> list[dict]:
    from radar.llm.provider import get_provider
    from radar.net.client import PoliteClient

    client = client or PoliteClient(purpose="ef-committee")
    provider = provider or get_provider()
    store.init()
    out = []
    for ein in SOURCES:
        for url, label in discover(client, ein):
            try:
                out.append({"ein": ein, **read_document(client, provider, ein, url, label)})
            except Exception as e:  # noqa: BLE001 - one document never stops the run
                log.warning("committee %s %s: %s", ein, url, e)
                out.append({"ein": ein, "url": url, "error": f"{type(e).__name__}: {str(e)[:160]}"})
    return out
