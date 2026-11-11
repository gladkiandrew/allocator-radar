"""Hedge fund and alternatives exposure from the 990s (D-B27).

Per filing: the itemized lines of Form 990 Schedule D Part VII (investments, other securities) or the Form 990-PF
Part II line 13 statement (investments, other) are sorted into hedge fund, other alternatives and traditional by
their printed description. Dollars are the line's fair market value when printed, else its book value. The share is
taken of total investments (990 Part X lines 11-13; 990-PF Part II lines 10a-13).

"not_disclosed" means the filing does not break the figure out: no itemized lines at all, or (hedge funds) no line
that names a hedge fund strategy. A lump line such as "Alternative investments" counts toward alternatives and leaves
hedge funds not disclosed. Placeholder amounts (0, $1 ...) are not stated and are left out of the dollars.
"""

from __future__ import annotations

import re

from radar.sources.endowments.signals import HF

PLACEHOLDER_MAX = 100.0  # a holding printed at $0-$100 is a placeholder, not a value (Arison Arts: "$1" Rokos)
ALT = re.compile(r"private equity|venture|buyout|growth equity|real estate|real assets|\breit\b|infrastructure|"
                 r"private credit|private debt|direct lending|mezzanine|distressed|natural resources|timber|energy fund|"
                 r"commodit|hedge|absolute return|alternative|limited partnership|partnership interest|\bl\.?\s?p\.?\b|"
                 r"\bpartners\b|offshore|\bspc\b|\bfund (?:ltd|lp|l\.p|llc)\b|feeder|co-?invest|secondar", re.I)
TRADITIONAL = re.compile(r"mutual fund|corporate (?:stock|bond)|bond|treasur|government|money market|cash|equity securit|"
                         r"common stock|fixed income|exchange[- ]traded|\betf\b|certificate of deposit|municipal|"
                         r"publicly traded|index fund|annuit", re.I)
LUMP_ALT = re.compile(r"^\W*(?:other\s+)?alternative investments?\W*$|^\W*alternatives?\W*$", re.I)


def stated_value(o: dict) -> float | None:
    v = o.get("fmv") if o.get("fmv") is not None else o.get("book_value")
    if v is None or abs(v) <= PLACEHOLDER_MAX:
        return None
    return v


def value_status(o: dict) -> str:
    return "stated" if stated_value(o) is not None else "not_stated"


def line_class(desc: str) -> str:
    d = desc or ""
    if HF.search(d) and not LUMP_ALT.search(d) and not re.search(r"alternative investment|marketable alt", d, re.I):
        return "hedge_fund"
    if ALT.search(d) and not TRADITIONAL.search(d):
        return "alternative"
    if TRADITIONAL.search(d):
        return "traditional"
    return "unclassified"


def total_investments(f: dict) -> float | None:
    pf = str(f["return_type"]).upper().startswith("990PF")
    parts = [f.get("investments_public"), f.get("investments_other")]
    parts += [f.get("investments_land"), f.get("investments_mortgage")] if pf else [f.get("investments_program")]
    vals = [v for v in parts if v]
    return sum(vals) if vals else None


def compute(f: dict, lines: list[dict]) -> dict:
    total = total_investments(f)
    hf = alt = 0.0
    n_hf = n_alt = n_unclassified = n_placeholder = 0
    for o in lines:
        cls = line_class(o["description"])
        v = stated_value(o)
        if v is None:
            n_placeholder += 1
            continue
        if cls == "hedge_fund":
            hf += v; alt += v; n_hf += 1
        elif cls == "alternative":
            alt += v; n_alt += 1
        elif cls == "unclassified":
            n_unclassified += 1
    itemized = bool(lines)
    hf_status = "computed" if n_hf else "not_disclosed"
    alt_status = "computed" if itemized and (n_hf or n_alt or n_unclassified == 0) else "not_disclosed"
    basis = (f"{len(lines)} itemized lines: {n_hf} hedge fund, {n_alt} other alternative, {n_unclassified} unclassified, "
             f"{n_placeholder} with no stated amount" if itemized else "the filing itemizes no other-investment lines")
    pct = lambda x: round(100 * x / total, 2) if total and x is not None else None
    return {"investments_total": total,
            "hf_usd": hf if hf_status == "computed" else None, "alt_usd": alt if alt_status == "computed" else None,
            "hf_pct": pct(hf) if hf_status == "computed" else None, "alt_pct": pct(alt) if alt_status == "computed" else None,
            "hf_status": hf_status, "alt_status": alt_status, "basis": basis, "n_lines": len(lines)}
