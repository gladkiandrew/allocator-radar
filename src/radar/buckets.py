"""Strategy-bucket crosswalk (AGENDA §4.3) and the hedge-fund relevance flag.

The source's own label is always kept verbatim; the bucket is a reviewable mapping, never a replacement.
"""

from __future__ import annotations

import re

HF_BUCKETS = {
    "EQUITY_LONG_SHORT", "EQUITY_MARKET_NEUTRAL", "EVENT_DRIVEN", "MERGER_ARB", "DISTRESSED", "CREDIT_LONG_SHORT",
    "CONVERTIBLE_ARB", "FIXED_INCOME_RV", "GLOBAL_MACRO", "MANAGED_FUTURES_CTA", "MULTI_STRATEGY",
    "FUND_OF_HEDGE_FUNDS", "QUANT_SYSTEMATIC", "VOLATILITY", "INSURANCE_LINKED",
}
HF_TEXT = re.compile(r"hedge|absolute return|diversifying|managed futures|\bcta\b|multi[- ]?strat|global macro|\bmacro\b|"
                     r"relative value|market neutral|long[/ -]short|event driven|pure alpha|tactical credit|risk premia|"
                     r"style premia", re.I)

# (pattern on "asset class | style | name", bucket). First match wins; order matters.
RULES = [
    (r"managed futures|\bcta\b|trend", "MANAGED_FUTURES_CTA"),
    (r"global macro|\bmacro\b|pure alpha", "GLOBAL_MACRO"),
    (r"multi[- ]?strat", "MULTI_STRATEGY"),
    (r"fund of hedge|hedge fund of funds", "FUND_OF_HEDGE_FUNDS"),
    (r"relative value|fixed income rv", "FIXED_INCOME_RV"),
    (r"market neutral", "EQUITY_MARKET_NEUTRAL"),
    (r"long[/ -]short equity|equity long[/ -]short", "EQUITY_LONG_SHORT"),
    (r"merger arb", "MERGER_ARB"),
    (r"event driven", "EVENT_DRIVEN"),
    (r"distressed", "DISTRESSED"),
    (r"style premia|risk premia|quant|systematic", "QUANT_SYSTEMATIC"),
    (r"tactical credit|credit long|credit gamma|credit opportunit", "CREDIT_LONG_SHORT"),
    (r"insurance|reinsurance|catastrophe|\bils\b", "INSURANCE_LINKED"),
    (r"hedge", "MULTI_STRATEGY"),
    (r"direct lending|private credit|private debt|credit facility|mezzanine", "PRIVATE_CREDIT"),
    (r"venture", "VENTURE"),
    (r"private equity|buyout|growth equity|co-?invest|\bpe\b|secondar", "PRIVATE_EQUITY"),
    (r"real estate|\bre\b|joint venture|multifamily|logistics|reit", "REAL_ESTATE"),
    (r"infrastructure", "INFRASTRUCTURE"),
    (r"natural resources|energy|oil|gas|timber|agricultur", "NATURAL_RESOURCES"),
    (r"commodit", "COMMODITIES"),
    (r"high yield|emerging market(s)? debt|\bemd\b|core fixed|fixed income|active credit|bond|treasur", "LONG_ONLY_FIXED_INCOME"),
    (r"equity|large cap|small cap|mid cap|\brei\b", "LONG_ONLY_EQUITY"),
    (r"cash", "CASH_OVERLAY"),
]
_RULES = [(re.compile(p, re.I), b) for p, b in RULES]


def to_bucket(*texts: str | None) -> str:
    t = " | ".join(x for x in texts if x)
    for pat, b in _RULES:
        if pat.search(t):
            return b
    return "OTHER"


def hf_relevant(bucket: str | None, *texts: str | None) -> bool:
    return bool((bucket and bucket in HF_BUCKETS) or any(t and HF_TEXT.search(t) for t in texts))
