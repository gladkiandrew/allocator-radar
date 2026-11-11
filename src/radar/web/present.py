"""Presentation helpers for the public pages: quote cleanup, consultant display names, the coverage note,
and the hedge fund highlights strip. Read-only: nothing here changes stored data or scoring.
"""

from __future__ import annotations

import csv
import functools
import json
import re
from datetime import date, datetime, timedelta

# ---------------- quotes as a reader sees them ----------------
# PDF text extraction artifacts. The stored quote stays verbatim (the quote-on-page check depends on it);
# only the displayed copy is cleaned.
CID = {"415": "ti"}  # Calibri "ti" ligature that pdfplumber cannot map, e.g. "Ac(cid:415)ve" -> "Active"
_CID = re.compile(r"\(cid:(\d+)\)")
# A vote-tally column interleaved into the text: "... at the October 19, Rossi X\n2026 meeting" -> "... October 19, 2026 meeting"
_VOTE_MARK = re.compile(r"\s*\b[A-Z][A-Za-z’'.\-]+ X\s*\n\s*")
_SPACE = re.compile(r"\s+")


def clean_quote(text: str | None) -> str:
    if not text:
        return ""
    t = _CID.sub(lambda m: CID.get(m.group(1), ""), text)
    t = _VOTE_MARK.sub(" ", t)
    t = t.replace("­", "").replace("�", "").replace(" ", " ")
    return _SPACE.sub(" ", t).strip()


# ---------------- consultant display names ----------------
def _key(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9& ]", " ", s.lower()).split())


@functools.lru_cache(maxsize=1)
def consultant_map() -> dict[str, str]:
    """data/consultant_map.{json,yaml,yml,csv}, or Session A's data/consultant_aliases.yaml (D-B18), if present:
    raw name -> display name. Accepted shapes: {raw: canonical}, {canonical: [aliases]}, {"canonical": {canonical:
    [aliases]}, "unsure": ...} (unsure names stay unmapped), [{raw|alias|name: ..., canonical|display: ...}], or a CSV
    with those columns. Missing or unreadable -> {} (the raw name is shown)."""
    from radar.settings import get_settings

    d = get_settings().data_dir
    for name in ("consultant_map.json", "consultant_map.yaml", "consultant_map.yml", "consultant_map.csv",
                 "consultant_aliases.yaml"):
        p = d / name
        if not p.exists():
            continue
        try:
            if p.suffix == ".json":
                data = json.loads(p.read_text())
            elif p.suffix in (".yaml", ".yml"):
                import yaml
                data = yaml.safe_load(p.read_text())
            else:
                data = list(csv.DictReader(p.open()))
        except Exception:  # noqa: BLE001 - a bad map never breaks the site
            return {}
        return _flatten(data)
    return {}


def _flatten(data) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for wrapper in ("canonical", "consultants"):  # {"canonical": {...}, "unsure": [...]}: unsure stays unmapped
            if isinstance(data.get(wrapper), (dict, list)):
                data = data[wrapper]
                break
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list):  # canonical: [aliases]
                out[_key(k)] = k
                out.update({_key(a): k for a in v if isinstance(a, str)})
            elif isinstance(v, str):
                out[_key(k)] = v
    elif isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            raw = row.get("raw") or row.get("alias") or row.get("name") or row.get("name_as_disclosed")
            can = row.get("canonical") or row.get("display") or row.get("display_name")
            if raw and can:
                out[_key(raw)] = can
    return out


_SPLIT = re.compile(r"\s*\|\s*|,\s*(?!(?:LLC|L\.L\.C|INC|LP|L\.P|LLP|LTD|CO|CORP|N\.A)\b\.?)", re.I)


def consultant_name(raw: str | None) -> str:
    if not raw:
        return ""
    m = consultant_map()
    if _key(raw) in m:
        return m[_key(raw)]
    parts = [p.strip() for p in _SPLIT.split(raw) if p.strip()]  # "A | B" or "A, B", never "Firm, LLC"
    return ", ".join(dict.fromkeys(m.get(_key(p), p) for p in parts))


# ---------------- coverage note ----------------
CURRENT_DAYS = 183  # "under 6 months"


def coverage_note(newest_doc_date, today: date | None = None) -> str | None:
    """None when coverage is current (newest document under 6 months old); otherwise 'last document: <Month YYYY>'."""
    today = today or date.today()
    if isinstance(newest_doc_date, datetime):
        newest_doc_date = newest_doc_date.date()
    if newest_doc_date and (today - newest_doc_date) <= timedelta(days=CURRENT_DAYS):
        return None
    return f"last document: {newest_doc_date.strftime('%B %Y')}" if newest_doc_date else "no dated document on file"


# ---------------- approval state as a reader sees it ----------------
APPROVAL_TITLE = {
    "reviewed": "A person read the source page and approved this signal.",
    "manual": "Passed the four automatic checks; the source file was downloaded by hand from the publisher. "
              "No person has reviewed this signal yet.",
    "awaiting": "Passed the four automatic checks, so it counts toward the tier, but no person has reviewed it yet.",
    "pending": "Not yet checked; not shown on the public site and never counts toward a tier.",
    "rejected": "Rejected by a person; never counts toward a tier.",
}


def manual_source(s: dict) -> bool:
    """Was the signal's source file added by hand (the manual inbox), not fetched by the crawler?"""
    if (s.get("source_url") or "").startswith("manual://"):
        return True
    return any((e.get("source_url") or "").startswith("manual://") or e.get("provenance") == "MANUAL"
               for e in s.get("evidence") or [])


def approval(s: dict) -> dict:
    """The real review state of a signal: 'reviewed' only when a person approved it. Auto-approved signals are
    labeled as such, never as reviewed. Returns {key, label, cls, title}."""
    st = s.get("review_status")
    if st == "approved":
        key, label, cls = "reviewed", "reviewed", "ok"
    elif st == "auto_approved" and manual_source(s):
        key, label, cls = "manual", "auto-approved (manual source)", ""
    elif st == "auto_approved":
        key, label, cls = "awaiting", "awaiting review", ""
    elif st == "rejected":
        key, label, cls = "rejected", "rejected", "bad"
    else:
        key, label, cls = "pending", "pending review", "warn"
    return {"key": key, "label": label, "cls": cls, "title": APPROVAL_TITLE[key]}


def doc_when(d, today: date | None = None) -> str:
    """A document's date as a reader should see it: a date after today is an upcoming meeting, not a new document."""
    if not d:
        return ""
    if isinstance(d, datetime):
        d = d.date()
    today = today or date.today()
    return f"upcoming meeting {d.isoformat()}" if d > today else d.isoformat()


# ---------------- hedge fund highlights ----------------
HIGHLIGHT_TYPES = {  # a search, a hire, a termination, or a target allocation change
    "SEARCH_OPENED": "Search", "SEARCH_SCHEDULED": "Search", "RFP_ISSUED": "Search", "FINALISTS_NAMED": "Search",
    "MANDATE_AWARDED": "Hire", "MANDATE_FUNDED": "Hire",
    "MANAGER_TERMINATED": "Termination",
    "TARGET_ALLOCATION_CHANGED": "Allocation change",
}
HIGHLIGHT_DAYS = 365
# What qualifies for the hedge fund strip. A signal qualifies when DATA flagged it (hf_relevant), when its bucket is a
# hedge fund bucket, or when its own summary names the strategy in specific terms. Evidence quotes are NOT searched:
# a page that mentions hedge funds in passing does not make a large cap value search a hedge fund signal.
STRIP_HF = re.compile(r"hedge fund|absolute return|risk[- ]mitigat|crisis risk offset|diversifying strateg|"
                      r"managed futures|\bcta\b|global macro|tail risk|market neutral|long[/ -]short|"
                      r"multi[- ]strategy hedge|systematic macro|trend[- ]following", re.I)


def is_hf(s: dict) -> bool:
    from radar.buckets import HF_BUCKETS

    if s.get("hf_relevant") or s.get("bucket") in HF_BUCKETS:
        return True
    return bool(STRIP_HF.search(" ".join(filter(None, [s.get("summary"), s.get("bucket_label")]))))


def hf_highlights(con, plans: list[dict], today: date | None = None, per_plan: int = 3) -> list[dict]:
    """Approved hedge-fund-type searches, hires, terminations and allocation changes in the last 12 months
    (a scheduled search up to 90 days ahead counts). Up to `per_plan` rows per plan, newest first; the rest
    are counted in the last row's `more`."""
    from radar import queries

    today = today or date.today()
    by_id = {p["allocator_id"]: p for p in plans}
    sigs = queries.signals_for(con, list(by_id), statuses=("approved", "auto_approved"))  # newest first
    rows: list[dict] = []
    shown: dict[str, list[dict]] = {}
    for s in sigs:
        if s["signal_type"] not in HIGHLIGHT_TYPES or not is_hf(s):
            continue
        if not (today - timedelta(days=HIGHLIGHT_DAYS) <= s["event_date"] <= today + timedelta(days=90)):
            continue
        mine = shown.setdefault(s["allocator_id"], [])
        if len(mine) >= per_plan:
            mine[-1]["more"] += 1
            continue
        p = by_id[s["allocator_id"]]
        row = {"plan": p, "signal": s, "kind": HIGHLIGHT_TYPES[s["signal_type"]], "more": 0,
               "consultant": consultant_name((p.get("consultant") or {}).get("consultant"))}
        mine.append(row)
        rows.append(row)
    return sorted(rows, key=lambda r: r["signal"]["event_date"], reverse=True)


# ---------------- strategy label as a reader sees it ----------------
# DATA owns classification (signals.bucket / bucket_label). Until a signal has a bucket, the page shows a display-only
# label: plan-level events are "total plan", hires of service providers are "service provider", and specific
# wording in the summary gives an asset class marked "(inferred)". Nothing here is written back to the store.
TOTAL_PLAN_TYPES = {"IPS_AMENDED", "ASSET_LIABILITY_STUDY", "REBALANCE_APPROVED"}
SERVICE_TYPES = {"CONSULTANT_CHANGED", "FEE_RENEGOTIATED", "CIO_CHANGED", "OFFICER_CHANGED"}
_INFER = [  # first match wins; service providers first, so "custodian for the fixed income portfolio" is a service hire
    (re.compile(r"custod|audit|actuar|counsel\b|attorney|litigation|plan administrator|administrative services|financial statements|"
                r"transition manag|investment consultant|consulting services|general consultant", re.I), "service provider"),
    (re.compile(r"real estate|\breits?\b", re.I), "real estate"),
    (re.compile(r"private (credit|debt)|direct lending", re.I), "private credit"),
    (re.compile(r"infrastructure", re.I), "infrastructure"),
    (re.compile(r"private equity|buyout|venture capital", re.I), "private equity"),
    (re.compile(r"\b(large|mid|small|smid)[- ]cap|s&p 500|index fund|international equity|global equity|"
                r"domestic equity|equity index|emerging markets? equity", re.I), "long only equity"),
    (re.compile(r"fixed income|core plus|\bbonds?\b|multi[- ]sector credit|duration|bank loans?|\btips\b", re.I),
     "long only fixed income"),
]


def strategy_label(s: dict) -> dict:
    """{text, inferred}: the stored bucket when DATA set one; else a display-only label."""
    b = s.get("bucket")
    if b and b != "OTHER":
        return {"text": b.replace("_", " ").lower(), "inferred": not s.get("bucket_label")}
    summary = s.get("summary") or ""
    if s.get("signal_type") in SERVICE_TYPES:
        return {"text": "service provider", "inferred": False}
    for pat, label in _INFER:
        if pat.search(summary):
            return {"text": label, "inferred": True}
    if s.get("signal_type") in TOTAL_PLAN_TYPES or s.get("signal_type") == "TARGET_ALLOCATION_CHANGED":
        return {"text": "total plan", "inferred": False}
    return {"text": "not classified", "inferred": False}
