"""Signal definitions, enforced in code (D-B36, owner 2026-11-11).

1. A presentation or agenda item is a search signal only if its own words say search, candidate, finalist, interview,
   RFP or replacement. Otherwise it is MANAGER_PRESENTATION (qualifies for no tier).
2. Before any search / finalist / hire signal, the firm is looked up in that plan's mandates. A firm the plan already
   holds is an incumbent update: searches become INCUMBENT_UPDATE (no tier); an award becomes COMMITMENT_MADE with
   qualifier re_up (a commitment to an existing manager's new fund).
3. Service providers (auditor, attorney, actuary, custodian, investment consultant, OCIO) never drive a manager tier:
   such rows carry qualifier service_provider or consultant, never manager.
"""

from __future__ import annotations

import re

SEARCH_TYPES = {"SEARCH_OPENED", "SEARCH_SCHEDULED", "RFP_ISSUED", "FINALISTS_NAMED"}
HIRE_TYPES = SEARCH_TYPES | {"MANDATE_AWARDED"}
SEARCH_WORDS = re.compile(r"\bsearch\w*|\bcandidates?\b|\bfinalists?\b|\binterview\w*|\brfp\b|request for proposals?|"
                          r"\breplac\w*|\bselection\b|new investment managers?|new managers?|"
                          r"fee (?:discussions?|negotiations?)|contract negotiations?", re.I)  # search stages, incl. fee/contract
PRESENTATION = re.compile(r"\bpresentation\b|\bpresents?\b|\bupdate\b|\breview\b|\banalysis\b", re.I)
_SP = r"(?:independent\s+)?(?:auditors?|audit(?:ing)? (?:firm|services)|actuar(?:y|ies|ial (?:firm|services))|custod(?:ian|ial)|attorneys?|legal counsel|counsel|legal services|investment consultants?|consultants?|consulting services|ocio|outsourced chief investment)"
# the service provider is the OBJECT of the search or hire, not merely mentioned near it
SERVICE = re.compile(rf"\b(?:rfp|request for proposals?|search|selection|proposals?)\s+(?:for|of)\s+(?:an?\s+|the\s+|new\s+)*{_SP}\b|"
                     rf"\b{_SP}\s+(?:search|rfp|selection|proposals?|services contract)\b|"
                     rf"\b(?:engag\w*|hir\w*|select\w*|appoint\w*|retain\w*)\s+[^.;]{{0,60}}?\bas\s+(?:the\s+|its\s+|an?\s+)*(?:new\s+)?{_SP}\b", re.I)
MANAGER_SEARCH = re.compile(r"\bmanagers?\s+(?:search|selection|candidates?)|investment managers?\b", re.I)
_STOP = {"fund", "funds", "capital", "partners", "management", "advisors", "advisers", "investment", "investments", "asset",
         "global", "group", "the", "inc", "llc", "lp", "ltd", "trust", "company", "core", "value", "growth", "equity",
         "income", "fixed", "real", "estate", "private", "credit", "large", "small", "cap", "mid", "international", "us",
         "property", "realty", "strategy", "strategies", "portfolio", "account", "class", "institutional", "index",
         "strategic", "alpha", "infra", "infrastructure", "opportunities", "opportunity", "holdings", "co-investment",
         "co-invest", "partnership", "dynamic", "diversified", "total", "return", "select", "market", "access", "special",
         "emerging", "markets", "world", "domestic", "absolute", "senior", "lending", "direct", "debt", "bond", "american",
         "national", "first", "new", "capital's", "associates", "company's", "investors"}


def firm_core(name: str) -> str | None:
    """The distinctive word of a manager name ("Brightwood Fund V" -> "brightwood"); None if nothing distinctive."""
    for tok in re.findall(r"[A-Za-z][A-Za-z&'-]+", name or ""):
        t = tok.lower().strip("'")
        if len(t) >= 4 and t not in _STOP:
            return t
    return None


def held_firm(con, allocator_id: str, text: str, before=None) -> str | None:
    """A manager the plan already held (a mandate dated BEFORE the signal) named in the text, else None. A manager hired
    by the very search appears in later holdings, so only earlier holdings count."""
    sql = "SELECT DISTINCT manager_name_as_disclosed FROM mandates WHERE allocator_id = ?"
    params = [allocator_id]
    if before is not None:
        sql += " AND as_of < ?"; params.append(before)
    names = [r[0] for r in con.execute(sql, params).fetchall()]
    low = (text or "").lower()
    for n in names:
        c = firm_core(n)
        if c and re.search(rf"\b{re.escape(c)}\b", low):
            return n
    return None


MULTI = re.compile(r"\b(?:candidates|finalists|firms|managers)\b", re.I)
RE_UP = re.compile(r"\bretain\w*|\brenew\w*|re-?up|\badditional\b|\bincrease\w*|top[- ]up|successor fund|follow[- ]on", re.I)


def apply(con, allocator_id: str, signal_type: str, qualifier: str | None, summary: str, quote: str | None,
          event_date=None, table_row: bool = False) -> tuple[str, str | None, str | None]:
    """(signal_type, qualifier, note). note says what changed and why, or None. table_row: a deterministic SBA table row,
    whose own "New Manager?" column decides incumbency."""
    words = f"{quote or ''} {summary or ''}"
    if signal_type in HIRE_TYPES and qualifier == "manager" and SERVICE.search(words) and not MANAGER_SEARCH.search(words):
        q = "consultant" if re.search(r"consult|ocio|outsourced chief", words, re.I) else "service_provider"
        return signal_type, q, f"definition 3: service provider ({q}), not a manager hire"
    if signal_type in SEARCH_TYPES and not SEARCH_WORDS.search(quote or summary or ""):
        if PRESENTATION.search(words) or signal_type == "SEARCH_SCHEDULED":
            return "MANAGER_PRESENTATION", qualifier, "definition 1: the item does not say search, candidate, finalist, interview, RFP or replacement"
    if signal_type in HIRE_TYPES and qualifier == "manager" and not table_row and event_date is not None:
        held = held_firm(con, allocator_id, words, before=event_date)
        if held:
            if signal_type == "MANDATE_AWARDED" and RE_UP.search(words):
                return "COMMITMENT_MADE", "re_up", f"definition 2: plan already held {held}; re-up or retention, not a new hire"
            if (signal_type in SEARCH_TYPES and not MULTI.search(words) and words.count(",") < 2
                    and not re.search(r"\badd\w*\s+(?:a|an|another|a second|a new)\b|\balongside\b|\bin addition to\b", words, re.I)):
                return "INCUMBENT_UPDATE", "incumbent", f"definition 2: plan already held {held}; incumbent update, not a search"
    return signal_type, qualifier, None
