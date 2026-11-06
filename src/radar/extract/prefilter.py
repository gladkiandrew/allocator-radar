"""Prefilter: only pages that hit this vocabulary are ever sent to the extractor.

Keeps LLM calls few and cheap, and keeps the model's input to the pages that could
carry a signal (AGENDA §3 Prefilter). Terms are matched case-insensitively on scrubbed text.
"""

from __future__ import annotations

import re

# term -> weight. A page goes to extraction when its total weight >= THRESHOLD.
VOCAB: dict[str, int] = {
    # searches and selection
    r"request for proposals?": 3, r"\brfp\b": 3, r"\brfi\b": 2, r"manager search": 3, r"search for an?": 2,
    r"finalists?": 3, r"short ?list": 2, r"semi-?finalists?": 3, r"interview(?:ed|s)? (?:the )?(?:managers?|firms?|candidates?)": 2,
    r"selected": 1, r"hire[ds]?\b": 1, r"award(?:ed)?": 1, r"engag(?:e|ed|ement) of": 1,
    r"fund(?:ed|ing) (?:of|the)": 1, r"commit(?:ment|ted)": 1,
    # incumbents
    r"on watch": 3, r"watch ?list": 3, r"placed on (?:probation|watch)": 3, r"terminat(?:e|ed|ion)": 2,
    r"replace(?:d|ment)? (?:the )?manager": 2, r"redemption": 1, r"liquidat(?:e|ion)": 1,
    # policy and structure
    r"investment policy statement": 2, r"\bips\b": 2, r"target allocation": 3, r"asset allocation": 2,
    r"asset[- /]liability": 3, r"pacing (?:plan|study)": 3, r"rebalanc": 1, r"policy target": 2,
    r"consultant": 1, r"investment consultant": 2, r"\brfq\b": 2,
    # asset classes where hedge-fund-adjacent mandates live
    r"hedge funds?": 2, r"absolute return": 2, r"alternative": 1, r"private credit": 2, r"private equity": 1,
    r"real estate": 1, r"infrastructure": 1, r"multi-?strategy": 2, r"diversifying": 2, r"opportunistic": 1,
    # motions
    r"motion (?:was )?(?:made|carried|passed|approved)": 2, r"voted": 1, r"unanimously": 1,
}
THRESHOLD = 5
_COMPILED = [(t, re.compile(t, re.I), w) for t, w in VOCAB.items()]


def match_terms(text: str) -> list[str]:
    return [t for t, pat, _ in _COMPILED if pat.search(text or "")]


def score(terms: list[str]) -> int:
    return sum(VOCAB.get(t, 0) for t in terms)


def is_hit(terms: list[str]) -> bool:
    return score(terms) >= THRESHOLD


# Plan-fact pages (allocation tables, assets, providers, meetings). Used for report-type documents.
FACT_VOCAB: dict[str, int] = {
    r"target allocation|policy target|target %|target weight|strategic allocation": 4,
    r"asset allocation": 3, r"actual allocation|current allocation|% of (total )?(fund|portfolio)": 3,
    r"total fund|total plan|total portfolio": 2, r"market value": 2, r"net position|fiduciary net position": 3,
    r"assumed (rate of )?return|investment return assumption|assumed rate": 3, r"rate of return": 1,
    r"custodian": 3, r"actuary|actuarial firm|enrolled actuary": 2, r"auditor|independent auditor": 2,
    r"investment consultant|performance consultant|investment monitor": 3,
    r"next (regular )?meeting|meeting schedule|meeting dates|will be held on": 3,
    r"(domestic|international|global) equity|fixed income|real estate|private (equity|credit|debt)|cash": 1,
}
_FACT = [(re.compile(t, re.I), w) for t, w in FACT_VOCAB.items()]
FACT_THRESHOLD = 5
FACT_DOC_KINDS = {"IPS", "PERFORMANCE", "ACTUARIAL", "FINANCIAL", "DISCLOSURE_112664"}


def fact_score(text: str) -> int:
    return sum(w for pat, w in _FACT if pat.search(text or ""))
