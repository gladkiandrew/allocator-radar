"""Strategy-bucket crosswalk (AGENDA §4.3) and the hedge-fund relevance flag.

The source's own label is always kept verbatim; the bucket is a reviewable mapping, never a replacement.
"""

from __future__ import annotations

import re

HF_BUCKETS = {
    "EQUITY_LONG_SHORT", "EQUITY_MARKET_NEUTRAL", "EVENT_DRIVEN", "MERGER_ARB", "DISTRESSED", "CREDIT_LONG_SHORT",
    "CONVERTIBLE_ARB", "FIXED_INCOME_RV", "GLOBAL_MACRO", "MANAGED_FUTURES_CTA", "MULTI_STRATEGY",
    "FUND_OF_HEDGE_FUNDS", "QUANT_SYSTEMATIC", "VOLATILITY",
}  # owner's flag: hedge fund / absolute return / diversifying / managed futures / multi-strat (+ standard HF strategies)
HF_TEXT = re.compile(r"hedge|absolute return|diversifying|managed futures|\bcta\b|multi[- ]?strat|global macro|\bmacro\b|"
                     r"relative value|market neutral|long[/ -]short|event driven|pure alpha|tactical credit|risk premia|"
                     r"style premia", re.I)

# (pattern on "asset class | style | name", bucket). First match wins; order matters.
RULES = [
    # D-B30 / R3: narrower hedge-fund words (commentary says "trend", "systematic", "board insurance")
    (r"managed futures|\bcta\b|trend[- ]following", "MANAGED_FUTURES_CTA"),
    (r"global macro|\bmacro\b|pure alpha", "GLOBAL_MACRO"),
    (r"multi[- ]?strat", "MULTI_STRATEGY"),
    (r"fund of hedge|hedge fund of funds", "FUND_OF_HEDGE_FUNDS"),
    (r"relative value|fixed income rv", "FIXED_INCOME_RV"),
    (r"market neutral", "EQUITY_MARKET_NEUTRAL"),
    (r"long[/ -]short equity|equity long[/ -]short", "EQUITY_LONG_SHORT"),
    (r"merger arb", "MERGER_ARB"),
    (r"convertible arb", "CONVERTIBLE_ARB"),
    (r"event driven", "EVENT_DRIVEN"),
    (r"distressed", "DISTRESSED"),
    (r"style premia|risk premia|\bquant(?:itative)?\b|systematic (?:strateg|macro|equity|trend)", "QUANT_SYSTEMATIC"),
    (r"tactical credit|credit long|credit gamma|credit opportunit", "CREDIT_LONG_SHORT"),
    (r"insurance[- ]linked|reinsurance|catastrophe bond|\bils\b|^\s*insurance\s*$", "INSURANCE_LINKED"),
    (r"hedge", "MULTI_STRATEGY"),
    (r"bitcoin|\bibit\b|digital assets?|crypto", "DIGITAL_ASSETS"),
    (r"direct lending|private credit|private debt|credit facility|mezzanine", "PRIVATE_CREDIT"),
    (r"(?<!joint )venture", "VENTURE"),
    (r"private equity|buyout|(?<!cap )growth equity|co-?invest|\bpe\b|secondar", "PRIVATE_EQUITY"),
    (r"real estate|joint venture|multifamily|logistics|\breit", "REAL_ESTATE"),
    (r"infrastructure", "INFRASTRUCTURE"),
    (r"natural resources|\benergy\b|\boil\b|\bgas\b|timber|agricultur", "NATURAL_RESOURCES"),
    (r"commodit", "COMMODITIES"),
    (r"convertible", "LONG_ONLY_CONVERTIBLES"),
    (r"high yield|emerging market(s)? debt|\bemd\b|core fixed|core plus|fixed income|active credit|multi[- ]sector credit|"
     r"(?:ultra[- ]?)?short(?:[- ]term)? duration|ultra[- ]?short|bank loans?|\btips\b|bond|treasur", "LONG_ONLY_FIXED_INCOME"),
    (r"equity|(?:large|mid|small|smid|all)[- ]?cap|\bsmid\b|\brei\b|international|index fund|s&p|passive", "LONG_ONLY_EQUITY"),
    (r"cash overlay|cash equivalents|cash management|money market|short[- ]term investment fund", "CASH_OVERLAY"),
]
_RULES = [(re.compile(p, re.I), b) for p, b in RULES]


_ASSET_FIRST = [  # the printed asset class decides first; names only refine hedge-fund / strategic rows
    (re.compile(r"venture", re.I), "VENTURE"),
    (re.compile(r"private equity|^pe$|buyout", re.I), "PRIVATE_EQUITY"),
    (re.compile(r"real estate|^re$", re.I), "REAL_ESTATE"),
    (re.compile(r"infrastructure", re.I), "INFRASTRUCTURE"),
    (re.compile(r"global equity|domestic equity|international equity|^equity", re.I), "LONG_ONLY_EQUITY"),
]


def _rules(t: str) -> str:
    for pat, b in _RULES:
        if pat.search(t):
            return b
    return "OTHER"


def to_bucket(asset_class: str | None, style: str | None = None, name: str | None = None) -> str:
    a, st = (asset_class or "").strip(), (style or "").strip()
    for pat, b in _ASSET_FIRST:
        if pat.search(a):
            return "VENTURE" if b == "PRIVATE_EQUITY" and re.search(r"venture", st, re.I) else b
    if re.search(r"active credit|fixed income|credit", a, re.I):
        return "PRIVATE_CREDIT" if re.search(r"direct lending|private credit|private debt|lender|mezz", st, re.I) \
            else _rules(st) if re.search(r"hedge|macro|relative value|long/short", st, re.I) else "LONG_ONLY_FIXED_INCOME"
    # Strategic Investments, hedge funds, unknown: style first, then the fund name
    return _rules(st) if _rules(st) != "OTHER" else _rules(f"{a} | {name or ''}")


def hf_relevant(bucket: str | None, *labels: str | None) -> bool:
    """Bucket in the hedge-fund set, or the source's own asset-class/style label says hedge fund, absolute return,
    diversifying, managed futures, multi-strategy (fund names are not used: "Opportunities Fund" is not a strategy)."""
    return bool((bucket and bucket in HF_BUCKETS) or any(t and HF_TEXT.search(t) for t in labels))


# ---------------- signal buckets (D-B30) ----------------
# Non-strategy values: a service-provider or consultant engagement, or an event about the whole plan.
NON_STRATEGY = {"SERVICE_PROVIDER", "TOTAL_PLAN"}
TOTAL_PLAN_TYPES = {"IPS_AMENDED", "ASSET_LIABILITY_STUDY", "PACING_PLAN_ADOPTED", "REBALANCE_APPROVED", "CIO_CHANGED"}


def _match(text: str) -> tuple[str, str] | None:
    for pat, b in _RULES:
        if (m := pat.search(text or "")):
            return b, m.group(0)
    return None


def signal_bucket(signal_type: str, qualifier: str | None, summary: str, quote: str | None) -> tuple[str, str | None]:
    """(bucket, bucket_label). The label is the source's own words, set only when the matched wording is in the quote;
    a bucket found only in our summary has no label (shown as inferred)."""
    if qualifier in ("service_provider", "consultant") or signal_type == "CONSULTANT_CHANGED":
        return "SERVICE_PROVIDER", None
    q = _match(quote or "")
    if q:
        return q[0], q[1]
    m = _match(summary)
    if m:
        return m[0], None
    if signal_type in TOTAL_PLAN_TYPES or signal_type == "TARGET_ALLOCATION_CHANGED":
        return "TOTAL_PLAN", None
    return "OTHER", None
