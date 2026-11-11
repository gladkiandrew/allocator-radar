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


# ---------------- hedge fund highlights ----------------
HIGHLIGHT_TYPES = {  # a search, a hire, a termination, or a target allocation change
    "SEARCH_OPENED": "Search", "SEARCH_SCHEDULED": "Search", "RFP_ISSUED": "Search", "FINALISTS_NAMED": "Search",
    "MANDATE_AWARDED": "Hire", "MANDATE_FUNDED": "Hire",
    "MANAGER_TERMINATED": "Termination",
    "TARGET_ALLOCATION_CHANGED": "Allocation change",
}
HIGHLIGHT_DAYS = 365
# In addition to the hedge fund buckets and buckets.HF_TEXT (read here, never edited): risk-mitigation and
# "diversifying strategies" wording that a board uses for the same sleeve.
EXTRA_HF = re.compile(r"risk[- ]mitigat|crisis risk offset|diversifying strateg|absolute return|managed futures|\bcta\b|"
                      r"hedge fund|tail risk|defensive strateg", re.I)


def is_hf(s: dict) -> bool:
    from radar.buckets import HF_BUCKETS, HF_TEXT

    if s.get("hf_relevant") or s.get("bucket") in HF_BUCKETS:
        return True
    text = " ".join([s.get("summary") or ""] + [e.get("quote") or "" for e in s.get("evidence") or []])
    return bool(HF_TEXT.search(text) or EXTRA_HF.search(text))


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
