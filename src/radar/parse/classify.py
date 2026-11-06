"""Content-based document kind and date (deterministic, from scrubbed page text).

Link text is often useless ("View", "Doc_1234"), so the kind and date of a fetched document are also read
from its first pages. Content wins when it is confident; the link classification is the fallback.
"""

from __future__ import annotations

import re
from datetime import date

from radar.crawl.links import parse_date

# (kind, pattern, weight). Patterns are matched on the first ~4,000 characters of pages 1-2.
RULES = [
    ("DISCLOSURE_112664", r"112\.664|chapter 112|f\.s\. 112|section 112\.664|mortality.*disclosure", 6),
    ("IPS", r"investment policy statement|statement of investment polic|investment policy and guidelines|"
            r"investment objectives and guidelines", 6),
    ("ACTUARIAL", r"actuarial valuation|actuarial impact statement|valuation report as of|experience study", 5),
    ("FINANCIAL", r"independent auditor|financial statements and (supplementary|required)|statement of fiduciary net position|"
                  r"annual comprehensive financial report", 5),
    ("PERFORMANCE", r"investment performance (review|report|analysis)|quarterly (investment|performance)|"
                    r"performance review|total fund (performance|composite)|asset allocation & performance|"
                    r"flash report|executive summary.*quarter", 5),
    ("MINUTES", r"\bminutes\b|minutes of the|meeting was called to order|motion (was )?(made|carried)|seconded by", 4),
    ("AGENDA", r"\bagenda\b|call to order|roll call|public comment", 3),
    ("RFP", r"request for proposals?|\brfp\b|request for qualifications", 4),
]


def classify_text(text: str) -> str | None:
    head = (text or "")[:4000].lower()
    scores: dict[str, int] = {}
    for kind, pat, w in RULES:
        n = len(re.findall(pat, head))
        if n:
            scores[kind] = scores.get(kind, 0) + w * min(n, 3)
    if not scores:
        return None
    # Minutes beat agendas when both appear (minutes repeat the agenda items).
    if "MINUTES" in scores and "AGENDA" in scores and scores["MINUTES"] >= 8:
        scores["AGENDA"] = 0
    kind, best = max(scores.items(), key=lambda kv: kv[1])
    return kind if best >= 5 else None


def content_date(text: str, not_after: date | None = None) -> date | None:
    """First plausible date on page 1 (meeting date, report as-of date)."""
    not_after = not_after or date.today()
    head = (text or "")[:2500]
    for m in re.finditer(r"([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+20\d{2}|\d{1,2}/\d{1,2}/20\d{2}|20\d{2}-\d{2}-\d{2}|"
                         r"(?:as of|quarter ended|period ending)\s+[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d{2})", head, re.I):
        d = parse_date(m.group(0))
        if d and date(2000, 1, 1) <= d <= not_after:
            return d
    return None
