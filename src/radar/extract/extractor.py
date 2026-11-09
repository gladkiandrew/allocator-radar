"""Extraction agent: prefiltered, scrubbed pages -> Signal / Mandate / Officer / Consultant rows.

Pipeline per document:
  hit pages (prefilter) -> batches of <= PAGES_PER_CALL pages -> provider.complete(schema)
  -> deterministic validators:
       * citation faithfulness: the quote must appear on the cited page (normalized match)
       * the cited page must be one we sent
       * event_date parses, is not in the future, not before 2000
       * summary passes the language lint and the contact detector
  -> rows + evidence (dedupe: same allocator/type/qualifier/date merges evidence)
Rows that fail a validator are dropped and counted in audit_log, never stored.
All machine signals start as review_status = 'new' (Touchpoint 3).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from radar import db
from radar.extract.prefilter import is_hit
from radar.llm.provider import ProviderError, get_provider
from radar.models import new_id
from radar.wall.lint import lint
from radar.wall.scrubber import detect

log = logging.getLogger(__name__)
EXTRACTOR = "page-extractor"
EXTRACTOR_VERSION = "0.3.0"
MAX_PAGES_PER_DOC = 15
PAGES_PER_CALL = 6

SignalTypeLit = Literal[
    "SEARCH_OPENED", "RFP_ISSUED", "FINALISTS_NAMED", "MANDATE_AWARDED", "MANDATE_FUNDED",
    "MANAGER_TERMINATED", "MANAGER_ON_WATCH", "WATCH_REMOVED", "REDEMPTION_APPROVED", "COMMITMENT_MADE",
    "TARGET_ALLOCATION_CHANGED", "IPS_AMENDED", "ASSET_LIABILITY_STUDY", "PACING_PLAN_ADOPTED",
    "CONSULTANT_CHANGED", "CIO_CHANGED", "REBALANCE_APPROVED", "FEE_RENEGOTIATED",
]
BucketLit = Literal[
    "EQUITY_LONG_SHORT", "EQUITY_MARKET_NEUTRAL", "EVENT_DRIVEN", "MERGER_ARB", "DISTRESSED", "CREDIT_LONG_SHORT",
    "STRUCTURED_CREDIT", "CONVERTIBLE_ARB", "FIXED_INCOME_RV", "GLOBAL_MACRO", "MANAGED_FUTURES_CTA",
    "MULTI_STRATEGY", "FUND_OF_HEDGE_FUNDS", "QUANT_SYSTEMATIC", "VOLATILITY", "INSURANCE_LINKED", "COMMODITIES",
    "DIGITAL_ASSETS", "PRIVATE_CREDIT", "PRIVATE_EQUITY", "VENTURE", "REAL_ESTATE", "INFRASTRUCTURE",
    "NATURAL_RESOURCES", "LONG_ONLY_EQUITY", "LONG_ONLY_FIXED_INCOME", "CASH_OVERLAY", "OTHER",
]


class XSignal(BaseModel):
    signal_type: SignalTypeLit
    qualifier: Literal["manager", "consultant", "service_provider", "increase", "decrease", "none"] = Field(
        description="search/RFP/finalist/award types: who is being hired (manager|consultant|service_provider). "
                    "TARGET_ALLOCATION_CHANGED: increase|decrease. Otherwise none.")
    event_date: str = Field(description="YYYY-MM-DD, the date the action happened as stated in the document")
    summary: str = Field(description="One factual sentence, <= 280 characters, factual verbs only")
    page: int = Field(description="page number (from the PAGE markers) holding the quote")
    quote: str = Field(description="verbatim span copied from that page, <= 400 characters")
    confidence: float = Field(description="0 to 1: how directly the page states this")


class XMandate(BaseModel):
    manager_name: str
    bucket_label_as_disclosed: str = Field(description="the document's own asset-class label, verbatim")
    bucket: BucketLit
    amount: float | None = Field(description="number only; dollars for market_value/commitment, percent for *_pct")
    amount_kind: Literal["market_value", "commitment", "target_pct", "actual_pct"] | None
    as_of: str = Field(description="YYYY-MM-DD")
    status: Literal["ACTIVE", "ON_WATCH", "TERMINATED", "PENDING_FUNDING", "REDEEMING", "UNKNOWN"]
    page: int
    quote: str


class XOfficer(BaseModel):
    name_as_printed: str
    title_as_printed: str
    role_type: Literal["TRUSTEE", "CHAIR", "CIO", "EXEC_DIRECTOR", "STAFF", "CONSULTANT_REP", "ACTUARY", "AUDITOR",
                       "COUNSEL", "OTHER"]
    as_of: str
    page: int
    quote: str


class XConsultant(BaseModel):
    consultant_name: str = Field(description="the plan's investment consultant firm, as printed")
    as_of: str
    page: int
    quote: str


class XFact(BaseModel):
    fact_kind: Literal["TOTAL_ASSETS", "TARGET_ALLOCATION", "ACTUAL_ALLOCATION", "NEXT_MEETING", "ACTUARY", "CUSTODIAN",
                       "AUDITOR", "INVESTMENT_CONSULTANT", "ASSUMED_RETURN", "ACTUAL_RETURN"]
    label: str = Field(description="asset-class label verbatim for *_ALLOCATION; firm name as printed for providers; "
                                   "period for ACTUAL_RETURN (e.g. 'FY 2025'); empty otherwise")
    value: float | None = Field(description="number only: dollars for TOTAL_ASSETS, percent for *_ALLOCATION and *_RETURN")
    value_low: float | None = Field(description="lower bound of a target range, percent, if printed")
    value_high: float | None = Field(description="upper bound of a target range, percent, if printed")
    as_of: str = Field(description="YYYY-MM-DD: the as-of date for the figure, or the meeting date for NEXT_MEETING")
    page: int
    quote: str


class XResult(BaseModel):
    signals: list[XSignal]
    mandates: list[XMandate]
    officers: list[XOfficer]
    consultants: list[XConsultant]
    facts: list[XFact] = []


SYSTEM = """You read public records published by Florida public pension plans (board agendas, minutes, \
investment reports, investment policy statements, RFP notices) and extract facts into a fixed schema. \
Your output feeds a review queue; a person approves every signal before anyone uses it.

Rules:
- Extract only what the pages state. Do not infer intent, predict, or characterize.
- Every item cites one page number from the PAGE markers and a quote copied character-for-character from that \
page (<= 400 characters). The quote must itself establish the fact: it contains the action and, when the summary \
names a firm, fund or amount, that name or amount. If you cannot quote it, do not extract it.
- event_date / as_of: the date the document gives for the action or holding (meeting date when the minutes record a \
vote; issue date for an RFP; report "as of" date for holdings). Use the document date from the metadata only when \
the page is the record of that meeting or report. Format YYYY-MM-DD.
- Signals are actions or events that occurred or were decided: an RFP or search issued/opened, finalists named, \
a manager or consultant selected/hired/awarded, funded, terminated, placed on or removed from watch, a target \
allocation changed (give increase or decrease), an IPS amended or adopted, an asset-liability or pacing study \
adopted or accepted, a consultant changed, a CIO changed, a rebalance or redemption approved, a fee renegotiated. \
An agenda line that only lists a topic for discussion is not a signal, unless it records a decision or announces \
an open solicitation.
- For SEARCH_OPENED / RFP_ISSUED / FINALISTS_NAMED / MANDATE_AWARDED set qualifier to manager (investment \
manager), consultant (investment consultant), or service_provider (legal, actuary, custodian, auditor, other).
- Summaries: one sentence, factual verbs only (reported, disclosed, approved, voted, issued, awarded, selected, \
terminated, placed on watch, removed from watch, increased ... from X to Y, decreased ... from X to Y, committed, \
redeemed). Never use evaluative or predictive words such as strong, weak, poor, top, best, worse, attractive, \
concern, outperform, underperform, likely, expected, opportunity. Name the plan, the manager or firm, the amount \
and the asset class when the page gives them.
- mandates: one row per manager holding when the page lists manager-level holdings, allocations or incumbents. \
Keep the document's own asset-class label verbatim; map it to the closest bucket enum (OTHER if unsure). Never \
retype a number differently than printed; amounts are plain numbers.
- officers: trustees, chair, executive director, CIO, plan administrator, consultant representatives, as printed. \
Names and titles only.
- consultants: the plan's investment consultant firm when the page names it.
- facts (plan facts, each with its own quote): TOTAL_ASSETS (total fund market value or fiduciary net position, \
dollars), TARGET_ALLOCATION and ACTUAL_ALLOCATION (one row per asset class, label verbatim, percent; targets from an \
IPS or the policy column of a report, with the range if printed), ASSUMED_RETURN and ACTUAL_RETURN (percent; label \
the period), ACTUARY / CUSTODIAN / AUDITOR / INVESTMENT_CONSULTANT (firm name as printed in label), NEXT_MEETING \
(a scheduled future meeting date stated on the page). Never compute or infer a number; copy it as printed. For \
allocation tables, the quote may be the table row (asset class and its percent).
- Text shown as [REDACTED-CONTACT] was removed on purpose; never try to restore it.
- If a page holds nothing relevant, return empty lists. Precision matters more than recall."""


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_on_page(quote: str, page_text: str) -> bool:
    q, p = _norm(quote), _norm(page_text)
    if len(q) < 12:
        return False
    if q in p:
        return True
    # tolerate ellipses joining two exact fragments
    parts = [x.strip() for x in re.split(r"\.\.\.|…", q) if len(x.strip()) >= 12]
    return bool(parts) and all(x in p for x in parts)


_STOP = {"The", "Board", "Plan", "Fund", "Trust", "System", "Retirement", "Pension", "Police", "Fire", "Firefighters",
         "Officers", "City", "Town", "Trustees", "Trustee", "Investment", "Committee", "Mr", "Ms", "Mrs", "Chair",
         "Chairman", "Motion", "Inc", "LLC", "LP", "And", "Of", "For", "In", "On", "To", "A", "An", "Employees", "General",
         "Request", "Proposals", "RFP", "Florida", "FL", "Policy", "Statement", "IPS"}


def supports(summary: str, quote: str, allocator_name: str = "") -> bool:
    """Deterministic support check: the summary's named entities / numbers must overlap the quote.

    Names of the plan itself and generic words don't count. If the summary names nothing specific,
    the quote passes (the action words are checked by the reviewer)."""
    plan = set(re.findall(r"[A-Za-z][A-Za-z&'.-]+", allocator_name))
    names = {w for w in re.findall(r"\b[A-Z][A-Za-z&'.-]{2,}\b", summary) if w not in _STOP and w not in plan}
    nums = {n.replace(",", "") for n in re.findall(r"\$?\d[\d,.]*", summary) if len(n.strip("$.,")) >= 2}
    if not names and not nums:
        return True
    q = _norm(quote)
    qn = q.replace(",", "")
    return any(n.lower() in q for n in names) or any(n.lower().strip("$") in qn for n in nums)


def _num_in(v: float, quote: str) -> bool:
    q = quote.replace(",", "").replace("$", "")
    cands = {f"{v:g}", f"{v:.1f}", f"{v:.2f}", f"{int(v)}" if float(v).is_integer() else f"{v:g}"}
    if v >= 1e6:  # "$412.3 million" style
        cands |= {f"{v / 1e6:.1f}", f"{v / 1e6:g}", f"{v / 1e9:.2f}", f"{v / 1e9:.1f}"}
    return any(c in q for c in cands)


def _date(s: str, today: date) -> date | None:
    try:
        d = date.fromisoformat(s[:10])
    except ValueError:
        return None
    return d if date(2000, 1, 1) <= d <= today else None


def _doc_kind(con, sha: str) -> str:
    r = con.execute("""SELECT CASE WHEN any_value(du.doc_type) IS NULL OR any_value(du.doc_type) = 'OTHER'
                                   THEN coalesce(any_value(d.doc_kind), 'OTHER') ELSE any_value(du.doc_type) END
                       FROM documents d LEFT JOIN document_urls du USING (sha256) WHERE d.sha256 = ?""", [sha]).fetchone()
    return (r[0] if r else None) or "OTHER"


def select_pages(pages: list[dict], kind: str) -> list[dict]:
    """Signal pages (prefilter) for every document; plan-fact pages for report-type documents. Capped per document."""
    from radar.extract.prefilter import FACT_DOC_KINDS, FACT_THRESHOLD, fact_score, match_terms, score

    scored = []
    for p in pages:
        terms = json.loads(p["prefilter_terms"]) if p["prefilter_terms"] else match_terms(p["text_scrubbed"])
        sig = score(terms) if is_hit(terms) else 0
        fac = fact_score(p["text_scrubbed"]) if (kind in FACT_DOC_KINDS or p["page_no"] <= 2) else 0
        fac = fac if fac >= FACT_THRESHOLD else 0
        if sig or fac:
            scored.append((sig + fac, p))
    scored.sort(key=lambda t: -t[0])
    return sorted([p for _, p in scored[:MAX_PAGES_PER_DOC]], key=lambda p: p["page_no"])


def _pages_to_extract(con, sha: str) -> list[dict]:
    pages = db.rows(con, "SELECT page_no, text_scrubbed, prefilter_terms FROM pages WHERE sha256 = ? ORDER BY page_no", [sha])
    done = {r[0] for r in con.execute(
        "SELECT page_no FROM extraction_runs WHERE sha256 = ? AND extractor_version = ?", [sha, EXTRACTOR_VERSION]).fetchall()}
    if done:
        return []  # a document is extracted once per extractor version
    return select_pages(pages, _doc_kind(con, sha))


def _user_prompt(meta: dict, pages: list[dict]) -> str:
    head = (f"Plan: {meta['display_name']} ({meta['jurisdiction_city']}, FL)\n"
            f"Document title: {meta['title']}\nDocument type (from link): {meta['doc_type']}\n"
            f"Document date (from link, may be empty): {meta['doc_date'] or ''}\n")
    body = "\n\n".join(f"=== PAGE {p['page_no']} ===\n{p['text_scrubbed'][:12000]}" for p in pages)
    return head + "\n" + body


def _dedupe_key(*parts) -> str:
    return hashlib.sha256("|".join(str(p).lower() for p in parts).encode()).hexdigest()[:24]


def _store(con, meta: dict, pages: dict[int, str], res: XResult, prompt_hash: str, today: date) -> dict:
    stats = {"signals": 0, "mandates": 0, "officers": 0, "consultants": 0, "dropped": 0, "drop_reasons": []}
    from radar.review import policy_actor

    auto_actor = policy_actor("signals")
    aid, sha, url = meta["allocator_id"], meta["sha256"], meta["url"]

    def evidence(page: int, quote: str, conf: float) -> str | None:
        if page not in pages or not quote_on_page(quote, pages[page]) or detect(quote):
            return None
        eid = new_id()
        con.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, now(), ?, 'CRAWL')",
                    [eid, sha, url, page, quote[:500], EXTRACTOR, EXTRACTOR_VERSION, prompt_hash, max(0.0, min(1.0, conf))])
        return eid

    def drop(reason: str) -> None:
        stats["dropped"] += 1
        stats["drop_reasons"].append(reason)

    def link(kind: str, row_id: str, eid: str) -> None:
        con.execute("INSERT INTO row_evidence VALUES (?, ?, ?) ON CONFLICT DO NOTHING", [kind, row_id, eid])

    for s in res.signals:
        d = _date(s.event_date, today)
        if d is None:
            drop(f"signal date {s.event_date!r}")
            continue
        summary = s.summary.strip()[:300]
        if not lint(summary).ok or detect(summary):
            drop(f"signal lint: {lint(summary).banned_found}")
            continue
        if not supports(summary, s.quote, meta["display_name"]):
            drop("signal quote does not support summary")
            continue
        eid = evidence(s.page, s.quote, s.confidence)
        if eid is None:
            drop("signal quote not on page")
            continue
        q = None if s.qualifier == "none" else s.qualifier
        key = _dedupe_key(aid, s.signal_type, q, d)
        existing = con.execute("SELECT signal_id FROM signals WHERE dedupe_key = ? AND review_status != 'rejected'", [key]).fetchone()
        if existing:
            link("signal", existing[0], eid)
            continue
        sid = new_id()
        from radar.buckets import hf_relevant
        con.execute(
            "INSERT INTO signals (signal_id, allocator_id, signal_type, qualifier, event_date, observed_at, summary, "
            "review_status, origin, dedupe_key, hf_relevant) VALUES (?, ?, ?, ?, ?, now(), ?, 'new', 'MACHINE', ?, ?)",
            [sid, aid, s.signal_type, q, d, summary, key, hf_relevant(None, summary)])
        link("signal", sid, eid)
        if auto_actor:
            con.execute("UPDATE signals SET review_status = 'approved', reviewed_by = ?, reviewed_at = now(), "
                        "review_note = 'auto-approved under owner policy' WHERE signal_id = ?", [auto_actor, sid])
            db.audit(con, auto_actor, "signal_approved", sid, detail={"policy": "review_policy.yaml"})
        stats["signals"] += 1

    for m in res.mandates:
        d = _date(m.as_of, today)
        eid = evidence(m.page, m.quote, 0.9) if d else None
        if eid is None:
            drop("mandate date/quote")
            continue
        key = _dedupe_key(aid, m.manager_name, m.bucket_label_as_disclosed, m.amount_kind, d)
        row = con.execute("SELECT mandate_id FROM mandates WHERE dedupe_key = ?", [key]).fetchone()
        if row:
            link("mandate", row[0], eid)
            continue
        mid = new_id()
        con.execute("INSERT INTO mandates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                    [mid, aid, m.manager_name[:200], m.bucket_label_as_disclosed[:200], m.bucket, m.amount,
                     m.amount_kind, d, m.status, key])
        link("mandate", mid, eid)
        stats["mandates"] += 1

    for o in res.officers:
        d = _date(o.as_of, today)
        if detect(o.name_as_printed + " " + o.title_as_printed):
            drop("officer contact-shaped")
            continue
        eid = evidence(o.page, o.quote, 0.9) if d else None
        if eid is None:
            drop("officer date/quote")
            continue
        key = _dedupe_key(aid, o.name_as_printed, o.title_as_printed, d)
        row = con.execute("SELECT officer_id FROM officers WHERE dedupe_key = ?", [key]).fetchone()
        if row:
            link("officer", row[0], eid)
            continue
        oid = new_id()
        con.execute("INSERT INTO officers VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [oid, aid, o.name_as_printed[:120], o.title_as_printed[:160], o.role_type, d, key])
        link("officer", oid, eid)
        stats["officers"] += 1

    for f in res.facts:
        d = _date(f.as_of, today + timedelta(days=400) if f.fact_kind == "NEXT_MEETING" else today)
        if d is None:
            drop(f"fact date {f.as_of!r}")
            continue
        label = (f.label or "").strip()[:200]
        if detect(label):
            drop("fact label contact-shaped")
            continue
        token_ok = (not label or _norm(label.split()[0]) in _norm(f.quote)) or (
            f.value is not None and _num_in(f.value, f.quote))
        if not token_ok:
            drop("fact quote does not support label/value")
            continue
        eid = evidence(f.page, f.quote, 0.9)
        if eid is None:
            drop("fact quote not on page")
            continue
        unit = "usd" if f.fact_kind == "TOTAL_ASSETS" else ("pct" if f.value is not None else None)
        key = _dedupe_key(aid, f.fact_kind, label.lower(), f.value, d)
        row = con.execute("SELECT fact_id FROM plan_facts WHERE dedupe_key = ?", [key]).fetchone()
        if row:
            link("fact", row[0], eid)
            continue
        fid = new_id()
        con.execute("INSERT INTO plan_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [fid, aid, f.fact_kind, label or None, f.value, f.value_low, f.value_high, unit, d, key])
        link("fact", fid, eid)
        stats["facts"] = stats.get("facts", 0) + 1
        if f.fact_kind == "INVESTMENT_CONSULTANT" and label:
            ckey = _dedupe_key(aid, label, d)
            if not con.execute("SELECT 1 FROM consultant_engagements WHERE dedupe_key = ?", [ckey]).fetchone():
                cid = new_id()
                con.execute("INSERT INTO consultant_engagements VALUES (?, ?, ?, ?, ?)", [cid, aid, label[:160], d, ckey])
                link("engagement", cid, eid)

    for c in res.consultants:
        d = _date(c.as_of, today)
        eid = evidence(c.page, c.quote, 0.9) if d else None
        if eid is None:
            drop("consultant date/quote")
            continue
        key = _dedupe_key(aid, c.consultant_name, d)
        row = con.execute("SELECT engagement_id FROM consultant_engagements WHERE dedupe_key = ?", [key]).fetchone()
        if row:
            link("engagement", row[0], eid)
            continue
        cid = new_id()
        con.execute("INSERT INTO consultant_engagements VALUES (?, ?, ?, ?, ?)", [cid, aid, c.consultant_name[:160], d, key])
        link("engagement", cid, eid)
        stats["consultants"] += 1
    return stats


SEARCH_SCAN_VERSION = "search-scan-1"
CONSULTANT_SCAN_VERSION = "consultant-scan-1"
CONSULTANT_VOCAB = re.compile(
    r"investment consultant|performance consultant|investment monitor|consultant|consulting|"
    r"andco|mariner|graystone|bogdahn|burgess chambers|segal marco|meketa|captrust|callan|nepc|dahab|"
    r"southeastern advisory|thistle|sawgrass|aon|mercer|marquette|wilshire|asset consulting group|"
    r"actuar|custodian|custody", re.I)
CONSULTANT_FOCUS = """

This pass is a focused scan for the plan's SERVICE PROVIDERS. Return facts only: INVESTMENT_CONSULTANT (the firm that
monitors the plan's investments or prepares its investment performance reports, as printed), ACTUARY, CUSTODIAN and
AUDITOR, each with the firm name in label and a quote that contains that firm name. Use the document date for as_of when the
page states no other date. Signals, mandates and officers may be left empty."""
SEARCH_VOCAB = re.compile(r"request for (proposals?|qualifications?|information)|\brfp\b|\brfq\b|\bsearch\b|finalist|"
                          r"interview|solicitation|semi-?finalist|short ?list|selection committee|evaluation committee|"
                          r"proposals? (were )?received|responses? (were )?received|selected|hire|engage(d|ment)? (of|a|an|the)", re.I)
SEARCH_FOCUS = """

This pass is a focused scan for SEARCHES. On these pages, look specifically for investment-manager and investment-consultant
searches: an RFP or RFQ issued or approved for issue, a search opened or authorized, responses received, a short list or
finalists named, interviews scheduled or held, a manager or consultant selected or hired. Set the qualifier precisely
(manager / consultant / service_provider). Return other signal types only if clearly stated; facts, mandates and officers may
be left empty in this pass."""


def extract_document(sha: str, allocator_id: str, provider=None, all_pages: bool = False, *, max_pages: int | None = None,
                     force: bool = False, search_scan: bool = False, consultant_scan: bool = False) -> dict:
    """all_pages: send every page with text (holdings lists the vocabularies do not select).
    force: re-extract even if this extractor version already ran (dedupe keys prevent duplicate rows).
    search_scan: search-focused pass, tracked under its own version tag."""
    version = CONSULTANT_SCAN_VERSION if consultant_scan else SEARCH_SCAN_VERSION if search_scan else EXTRACTOR_VERSION
    provider = provider or get_provider()
    today = date.today()
    with db.connect() as con:
        meta = db.rows(con, """SELECT du.sha256, du.url, du.title, du.doc_type, du.doc_date, a.allocator_id,
                                      a.display_name, a.jurisdiction_city
                               FROM document_urls du JOIN allocators a USING (allocator_id)
                               WHERE du.sha256 = ? AND du.allocator_id = ? LIMIT 1""", [sha, allocator_id])
        if not meta:
            raise KeyError((sha, allocator_id))
        meta = meta[0]
        cap = max_pages or MAX_PAGES_PER_DOC
        if force:
            con.execute("DELETE FROM extraction_runs WHERE sha256 = ? AND extractor_version = ?", [sha, version])
        if all_pages or search_scan or consultant_scan:
            con.execute("DELETE FROM extraction_runs WHERE sha256 = ? AND extractor_version = ? AND status = 'none'",
                        [sha, version])
            done = con.execute("SELECT 1 FROM extraction_runs WHERE sha256 = ? AND extractor_version = ? LIMIT 1",
                               [sha, version]).fetchone()
            pages = db.rows(con, "SELECT page_no, text_scrubbed, prefilter_terms FROM pages WHERE sha256 = ? ORDER BY page_no", [sha])
            if search_scan:
                pages = [p for p in pages if SEARCH_VOCAB.search(p["text_scrubbed"])]
            if consultant_scan:  # the pages that name providers most often, cover pages first
                pages = sorted([p for p in pages if CONSULTANT_VOCAB.search(p["text_scrubbed"])],
                               key=lambda p: (-len(CONSULTANT_VOCAB.findall(p["text_scrubbed"])), p["page_no"]))
            todo = [] if done else [p for p in pages if len(p["text_scrubbed"]) > 200][:cap]
        else:
            todo = _pages_to_extract(con, sha)
    system = SYSTEM + (SEARCH_FOCUS if search_scan else "") + (CONSULTANT_FOCUS if consultant_scan else "")
    total = {"pages": len(todo), "calls": 0, "signals": 0, "mandates": 0, "officers": 0, "consultants": 0, "facts": 0,
             "dropped": 0}
    if not todo:  # nothing qualifies on this document: mark it done for this extractor version
        with db.connect() as con:
            if not con.execute("SELECT 1 FROM extraction_runs WHERE sha256 = ? AND extractor_version = ? LIMIT 1",
                               [sha, version]).fetchone():
                con.execute("INSERT INTO extraction_runs VALUES (?, 0, ?, now(), 'none') ON CONFLICT DO NOTHING",
                            [sha, version])
        return total
    for i in range(0, len(todo), PAGES_PER_CALL):
        batch = todo[i : i + PAGES_PER_CALL]
        user = _user_prompt(meta, batch)
        try:
            res, call = provider.complete(system=system, user=user, schema=XResult,
                                          purpose="consultant-scan" if consultant_scan else
                                          "search-scan" if search_scan else "extract")
        except ProviderError as e:
            log.warning("extraction failed for %s: %s", sha[:12], e)
            with db.connect() as con:
                db.audit(con, "extractor", "extract_error", sha, status="error", detail={"error": str(e)[:300]})
            continue
        total["calls"] += 1
        with db.connect() as con:
            con.execute("BEGIN")
            st = _store(con, meta, {p["page_no"]: p["text_scrubbed"] for p in batch}, res, call.prompt_sha256, today)
            for p in batch:
                con.execute("INSERT INTO extraction_runs VALUES (?, ?, ?, now(), 'ok') ON CONFLICT DO NOTHING",
                            [sha, p["page_no"], version])
            if not todo[i + PAGES_PER_CALL:]:  # mark the document done even if some pages were not selected
                con.execute("INSERT INTO extraction_runs VALUES (?, 0, ?, now(), 'doc') ON CONFLICT DO NOTHING",
                            [sha, version])
            db.audit(con, "extractor", "extract", sha, model=call.model, inputs_hash=call.prompt_sha256,
                     outputs_hash=call.response_sha256, duration_ms=call.duration_ms, detail=st)
            con.execute("COMMIT")
        for k in ("signals", "mandates", "officers", "consultants", "facts", "dropped"):
            total[k] += st.get(k, 0)
    return total


def pending_documents(con, allocator_ids: list[str] | None = None) -> list[tuple[str, str]]:
    """(sha, allocator_id) pairs not yet extracted by this extractor version, newest documents first."""
    rs = db.rows(con, """SELECT du.sha256, any_value(du.allocator_id) AS allocator_id,
                                max(coalesce(du.doc_date, d.content_date)) AS dd
                         FROM document_urls du JOIN documents d USING (sha256)
                         WHERE d.parsed_at IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM extraction_runs r WHERE r.sha256 = du.sha256
                                           AND r.extractor_version = ?)
                         GROUP BY du.sha256 ORDER BY dd DESC NULLS LAST""", [EXTRACTOR_VERSION])
    out = []
    for r in rs:
        if allocator_ids and r["allocator_id"] not in allocator_ids:
            continue
        if _pages_to_extract(con, r["sha256"]):
            out.append((r["sha256"], r["allocator_id"]))
    return out
