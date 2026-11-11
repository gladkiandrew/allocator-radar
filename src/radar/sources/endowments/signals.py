"""E&F signals and readiness.

From the 990s (source_type '990'), deterministic, cited to form / tax year / part and line:
  EF_CONTRACTOR_LISTED   an investment firm among the five highest-paid independent contractors (latest return)
  EF_MANAGER_ADDED       an investment firm listed this year that the prior return did not list
  EF_MANAGER_DROPPED     an investment firm listed on the prior return and not on this one
  EF_ALT_HOLDING         a hedge fund / absolute return / diversifying line in Schedule D Part VII or the 990-PF
                         Part II line 13 statement (latest return)
  EF_ALT_HOLDING_ADDED   such a line that was not on the prior return
From committee or board documents (source_type 'committee'): the pension signal types (SEARCH_OPENED, MANDATE_AWARDED,
MANAGER_TERMINATED, TARGET_ALLOCATION_CHANGED ...), judged by the same readiness rules as the pensions.

Readiness: 990s are annual and lag by a year or more, so a 990 alone never sets a tier above WATCHING.
DEPLOYING or PREPARING needs a dated committee or board document (ef_readiness).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone

from radar.readiness import COUNTING_STATUSES, Readiness, compute
from radar.wall.lint import lint

HF = re.compile(r"hedge|absolute return|diversif|managed futures|\bcta\b|multi[- ]?strat|global macro|\bmacro\b|"
                r"risk mitigat|crisis risk|tail risk|market neutral|long[/ -]short|arbitrage|event driven|"
                r"\bsystematic\b|\bquant(itative)?\b|alternative investment|marketable alt", re.I)
DETERMINISTIC = "ef-990-parser (deterministic)"
CAP_MEANING = ("990 data only. Annual filings lag by a year or more, so a 990 alone never sets a tier above "
               "WATCHING; DEPLOYING or PREPARING needs a dated committee or board document.")


def _norm(firm: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", firm.upper())
    s = re.sub(r"\b(LLC|INC|LP|LLP|LTD|CORP|CORPORATION|CO|COMPANY|THE|NA|N A|PLC)\b", " ", s)
    return " ".join(s.split())


def _sid(*parts) -> str:
    return "ef-" + hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:20]


def _money(v) -> str:
    return f"${v:,.0f}" if v is not None else "an undisclosed amount"


def _status(summary: str, names: list[str]) -> str:
    """Deterministic parse of the filing -> auto-approved, unless the summary fails the language lint."""
    return "auto_approved" if lint(summary, ignore=names).ok else "new"


def derive(org: dict, filings: list[dict], contractors: dict[str, list[dict]], others: dict[str, list[dict]],
           filing_url, citation) -> list[dict]:
    """filings: newest first (one per tax period). contractors / others: object_id -> rows."""
    out: list[dict] = []
    if not filings:
        return out

    def sig(f, stype, summary, what, names, qualifier=None, hf=False):
        d = f["tax_period_end"] if isinstance(f["tax_period_end"], date) else date.fromisoformat(str(f["tax_period_end"]))
        out.append(dict(signal_id=_sid(org["ein"], f["object_id"], stype, summary), ein=org["ein"], signal_type=stype,
                        qualifier=qualifier, event_date=d, date_basis="tax_period_end", summary=summary, source_type="990",
                        source_url=filing_url(org["ein"], f["object_id"]), doc_title=f"Form {_form(f)} TY{f['tax_year']}",
                        cite=citation(f, what), page=None, quote=None, hf_relevant=hf,
                        review_status=_status(summary, names), reviewed_by=DETERMINISTIC))

    latest, prev = filings[0], (filings[1] if len(filings) > 1 else None)
    inv = lambda f: {_norm(c["firm"]): c for c in contractors.get(f["object_id"], []) if c["investment"]}
    now_inv = inv(latest)

    def same_firm(k: str, others: dict) -> bool:
        """Exact normalized match, or the same first two words (filers garble long names across name lines)."""
        head = " ".join(k.split()[:2])
        return k in others or any(" ".join(o.split()[:2]) == head and len(head) >= 8 for o in others)
    for c in now_inv.values():
        sig(latest, "EF_CONTRACTOR_LISTED",
            f"The TY{latest['tax_year']} return lists {c['firm']} ({c['service'].lower() or 'services not described'}, "
            f"{_money(c['amount'])}) among its five highest-paid independent contractors.", "contractors", [c["firm"], c["service"]])
    if prev is not None and prev["object_id"] in contractors and latest["object_id"] in contractors:
        before = inv(prev)
        for k, c in now_inv.items():
            if not same_firm(k, before):
                sig(latest, "EF_MANAGER_ADDED",
                    f"{c['firm']} ({c['service'].lower() or 'services not described'}) is listed among the five highest-paid "
                    f"independent contractors on the TY{latest['tax_year']} return; the TY{prev['tax_year']} return did not list it.",
                    "contractors", [c["firm"], c["service"]], qualifier="manager")
        for k, c in before.items():
            if not same_firm(k, now_inv):
                sig(latest, "EF_MANAGER_DROPPED",
                    f"{c['firm']} ({c['service'].lower() or 'services not described'}) was listed among the five highest-paid "
                    f"independent contractors on the TY{prev['tax_year']} return and is not listed on the TY{latest['tax_year']} return.",
                    "contractors", [c["firm"], c["service"]], qualifier="manager")

    def hf_lines(f):
        return {o["description"].upper(): o for o in others.get(f["object_id"], []) if HF.search(o["description"])}
    now_hf = hf_lines(latest)
    before_hf = hf_lines(prev) if prev is not None else None
    for k, o in sorted(now_hf.items(), key=lambda kv: -((kv[1]["fmv"] or kv[1]["book_value"] or 0)))[:12]:
        val = o["fmv"] if o["fmv"] is not None else o["book_value"]
        sig(latest, "EF_ALT_HOLDING",
            f"The TY{latest['tax_year']} return reports {_money(val)} in \"{o['description']}\" at the end of the tax year.",
            "other_investments", [o["description"]], hf=True)
        if before_hf is not None and others.get(prev["object_id"]) and k not in before_hf:
            sig(latest, "EF_ALT_HOLDING_ADDED",
                f"\"{o['description']}\" ({_money(val)}) appears on the TY{latest['tax_year']} return and not on the "
                f"TY{prev['tax_year']} return.", "other_investments", [o["description"]], qualifier="increase", hf=True)
    return out


def _form(f: dict) -> str:
    return "990-PF" if str(f["return_type"]).upper().startswith("990PF") else "990"


def ef_readiness(signals: list[dict], has_filings: bool, today: date | None = None) -> Readiness:
    """Pension rules on committee/board signals only; 990 signals can at most make an organization WATCHING."""
    today = today or date.today()
    committee = [s for s in signals if s["source_type"] == "committee"]
    r = compute(committee, datetime.now(timezone.utc), today)  # coverage judged below, not by fetch date
    if r.tier in ("DEPLOYING", "PREPARING"):
        return r
    approved_990 = sorted((s for s in signals if s["source_type"] == "990" and s["review_status"] in COUNTING_STATUSES),
                          key=lambda s: s["event_date"], reverse=True)
    if approved_990 or has_filings or committee:
        return Readiness("WATCHING", approved_990[:3], CAP_MEANING)
    return Readiness("STALE", [], "No filing or committee document on file.", None, True)
