"""Read side of the E&F store for the web pages (read-only connections)."""

from __future__ import annotations

import re
from collections import defaultdict

from radar.readiness import TIER_ORDER
from radar.sources.endowments import irs, store
from radar.sources.endowments.signals import HF, ef_readiness
from radar.sources.endowments.universe import KIND_LABEL, PP_ORG

PUBLIC_STATUSES = ("approved", "auto_approved")
SOURCE_LABEL = {"990": "IRS 990", "committee": "Committee document"}


def _signals(con, eins: list[str] | None, public: bool) -> dict[str, list[dict]]:
    where = ["1=1"]
    params: list = []
    if public:
        where.append("review_status IN ('approved', 'auto_approved')")
    if eins is not None:
        if not eins:
            return {}
        where.append(f"ein IN ({','.join('?' * len(eins))})")
        params += eins
    out: dict[str, list[dict]] = defaultdict(list)
    for s in store.rows(con, f"SELECT * FROM ef_signals WHERE {' AND '.join(where)} ORDER BY event_date DESC, signal_type", params):
        s["source_label"] = SOURCE_LABEL.get(s["source_type"], s["source_type"])
        out[s["ein"]].append(s)
    return out


def _filings(con, eins: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    if not eins:
        return out
    for f in store.rows(con, f"SELECT * FROM ef_filings WHERE ein IN ({','.join('?' * len(eins))}) "
                             "ORDER BY tax_period_end DESC", eins):
        f["form"] = "990-PF" if str(f["return_type"]).upper().startswith("990PF") else "990"
        f["url"] = irs.filing_url(f["ein"], f["object_id"])
        out[f["ein"]].append(f)
    return out


def org_table(public: bool = True) -> list[dict]:
    with store.connect(read_only=True) as con:
        orgs = store.rows(con, "SELECT * FROM ef_orgs")
        eins = [o["ein"] for o in orgs]
        sigs = _signals(con, eins, public)
        fil = _filings(con, eins)
        inv = defaultdict(list)
        for c in store.rows(con, "SELECT * FROM ef_contractors WHERE investment"):
            inv[c["object_id"]].append(c["firm"])
        hf_lines = defaultdict(int)
        for o in store.rows(con, "SELECT object_id, description FROM ef_other_investments"):
            if HF.search(o["description"] or ""):
                hf_lines[o["object_id"]] += 1
    rows = []
    for o in orgs:
        fs = fil.get(o["ein"], [])
        latest = fs[0] if fs else None
        r = ef_readiness(sigs.get(o["ein"], []), bool(fs))
        rows.append({**o, "kind_label": KIND_LABEL.get(o["kind"], o["kind"]), "filings": fs, "latest": latest,
                     "readiness": r, "signals": sigs.get(o["ein"], []),
                     "managers": sorted(set(inv.get(latest["object_id"], []))) if latest else [],
                     "hf_lines": hf_lines.get(latest["object_id"], 0) if latest else 0,
                     "total_assets": (latest or {}).get("total_assets") or o["bmf_assets"],
                     "propublica_url": PP_ORG.format(ein=int(o["ein"]))})
    rows.sort(key=lambda r: (TIER_ORDER.index(r["readiness"].tier), -(r["total_assets"] or 0)))
    return rows


def org_brief(ein: str, public: bool = True) -> dict | None:
    with store.connect(read_only=True) as con:
        o = store.rows(con, "SELECT * FROM ef_orgs WHERE ein = ?", [ein])
        if not o:
            return None
        o = o[0]
        fs = _filings(con, [ein]).get(ein, [])
        ids = [f["object_id"] for f in fs]
        ph = ",".join("?" * len(ids)) or "''"
        contractors = defaultdict(list)
        for c in store.rows(con, f"SELECT * FROM ef_contractors WHERE object_id IN ({ph}) ORDER BY amount DESC NULLS LAST", ids):
            contractors[c["object_id"]].append(c)
        others = defaultdict(list)
        for x in store.rows(con, f"SELECT * FROM ef_other_investments WHERE object_id IN ({ph})", ids):
            x["hf"] = bool(HF.search(x["description"] or ""))
            others[x["object_id"]].append(x)
        pp = store.rows(con, "SELECT * FROM ef_pp_filings WHERE ein = ? ORDER BY tax_prd DESC", [ein])
        docs = store.rows(con, "SELECT * FROM ef_documents WHERE ein = ? ORDER BY doc_date DESC NULLS LAST", [ein])
        sigs = _signals(con, [ein], public).get(ein, [])
    for f in fs:
        f["contractors"] = contractors.get(f["object_id"], [])
        f["cite_contractors"] = irs.citation(_filing_obj(f), "contractors")
        f["cite_total"] = irs.citation(_filing_obj(f), "total_assets")
        f["cite_other"] = irs.citation(_filing_obj(f), "investments_other")
        f["cite_public"] = irs.citation(_filing_obj(f), "investments_public")
        f["cite_other_list"] = irs.citation(_filing_obj(f), "other_investments")
    latest = fs[0] if fs else None
    other_latest = sorted(others.get(latest["object_id"], []) if latest else [],
                          key=lambda x: (not x["hf"], -((x["fmv"] if x["fmv"] is not None else x["book_value"]) or 0)))
    return {**o, "kind_label": KIND_LABEL.get(o["kind"], o["kind"]), "filings": fs, "latest": latest,
            "other_latest": other_latest, "n_other_latest": len(other_latest), "pp_filings": pp, "documents": docs,
            "signals": sigs, "readiness": ef_readiness(sigs, bool(fs)), "propublica_url": PP_ORG.format(ein=int(ein))}


def _filing_obj(f: dict) -> irs.Filing:
    return irs.Filing(f["ein"], f["object_id"], f["return_type"], str(f["tax_period_end"]), f["tax_year"])


def eins() -> list[str]:
    with store.connect(read_only=True) as con:
        return [r[0] for r in con.execute("SELECT ein FROM ef_orgs ORDER BY ein").fetchall()]


def strip_qualifies(s: dict) -> bool:
    """A 990 signal belongs on the hedge fund strip only when the line or service it reports is itself hedge fund,
    absolute return, risk-mitigation, managed futures or diversifying-strategy. A firm newly listed among the
    highest-paid contractors (law firms, custodians, banks, brokers, long-only managers) does not qualify unless
    the service described on the return is hedge-fund-type."""
    from radar.web.present import STRIP_HF

    return bool(STRIP_HF.search(s.get("summary") or ""))


def hf_rows(public: bool = True, limit: int = 6) -> list[dict]:
    """E&F rows for the main page strip: hedge-fund-type 990 lines that are new this year (and, rarely, a firm newly
    listed for a hedge-fund-type service). Labeled as 990 data (annual, lagging)."""
    with store.connect(read_only=True) as con:
        names = dict(con.execute("SELECT ein, name FROM ef_orgs").fetchall())
        q = ("SELECT * FROM ef_signals WHERE signal_type IN ('EF_ALT_HOLDING_ADDED', 'EF_MANAGER_ADDED') "
             + ("AND review_status IN ('approved', 'auto_approved') " if public else "")
             + "ORDER BY signal_type = 'EF_MANAGER_ADDED', event_date DESC")  # new hedge fund lines first
        sigs = store.rows(con, q)
    seen, out = defaultdict(int), []
    for s in sigs:
        if not strip_qualifies(s):
            continue
        if seen[s["ein"]] >= 2:
            continue
        seen[s["ein"]] += 1
        out.append({**s, "org_name": names.get(s["ein"], s["ein"])})
        if len(out) >= limit:
            break
    return out


# ---------------- Hedge fund buyers (/ef/hedge-fund-buyers) ----------------
# A line counts toward the hedge fund figure only when its own description names a hedge fund strategy. This is
# signals.HF without its generic "alternative investment" / "marketable alt" / bare "diversif" wording (an
# "ALTERNATIVE INVESTMENTS" line can hold private equity or real estate), plus truncated "MULTI-STR" labels.
HF_NAMED = re.compile(r"hedge|absolute return|diversifying strat|managed futures|\bcta\b|multi[- ]?str(at|\b)|global macro|"
                      r"\bmacro\b|risk mitigat|crisis risk|tail risk|market neutral|long[/ -]short|arbitrage|"
                      r"event driven|\bsystematic\b|\bquant(itative)?\b", re.I)
ALT_WORDING = re.compile(r"alternative|marketable alt", re.I)


def _short_cite(c: str) -> str:
    """'Form 990 TY2023 Part X line 12 (investments, other securities)' -> without the parenthetical."""
    return c.split(" (")[0]


def hf_buyers() -> list[dict]:
    """Organizations ranked by the hedge fund exposure their latest 990 / 990-PF discloses (read-only, no writes).

    hf:    sum of the latest return's other-investment lines whose description names a hedge fund strategy (990:
           Schedule D Part VII; 990-PF: Part II line 13 statement). FMV when the line carries one, else book value.
    alt:   the broader "alternatives (other investments)" total: 990 Part X line 12 (book value, end of year) or
           990-PF Part II line 13 (fair market value). It also holds anything else the filer put there.
    denom: investments = public securities + other investments (990 Part X lines 11 + 12; 990-PF Part II lines
           10a-10c + 13). A line left blank on the return counts as zero; if both are blank, no percent is computed.
    Rank: hedge fund figure first; rows without one follow, by the broader figure; then rows with neither.
    Values not on the return are None ("not disclosed"), never 0."""
    with store.connect(read_only=True) as con:
        orgs = store.rows(con, "SELECT * FROM ef_orgs")
        fil = _filings(con, [o["ein"] for o in orgs])
        latest_ids = [fs[0]["object_id"] for fs in fil.values() if fs]
        lines = defaultdict(list)
        if latest_ids:
            for x in store.rows(con, f"SELECT * FROM ef_other_investments WHERE object_id IN ({','.join('?' * len(latest_ids))})",
                                latest_ids):
                lines[x["object_id"]].append(x)
    rows = []
    for o in orgs:
        fs = fil.get(o["ein"], [])
        f = fs[0] if fs else None
        row = {"ein": o["ein"], "name": o["name"], "kind_label": KIND_LABEL.get(o["kind"], o["kind"]), "city": o["city"],
               "latest": f, "hf": None, "alt": None, "denom": None, "hf_pct": None, "alt_pct": None,
               "alt_named": [], "n_lines": 0}
        if f is not None:
            fo = _filing_obj(f)
            ls = lines.get(f["object_id"], [])
            row["n_lines"] = len(ls)
            named = []
            row["hf_unstated"] = []
            for x in ls:
                # DATA marks placeholder amounts (0 or $1 entered where no value is stated) as value_status 'not_stated'
                if x.get("value_status") == "not_stated":
                    if HF_NAMED.search(x["description"] or ""):
                        row["hf_unstated"].append(x["description"])
                    continue
                val, basis = (x["fmv"], "FMV") if x["fmv"] is not None else (x["book_value"], "book value")
                if val is None:
                    continue
                item = {"description": x["description"], "value": val, "basis": basis}
                if HF_NAMED.search(x["description"] or ""):
                    named.append(item)
                elif ALT_WORDING.search(x["description"] or ""):
                    row["alt_named"].append(item)
            if named:
                bases = {i["basis"] for i in named}
                row["hf"] = {"amount": sum(i["value"] for i in named), "lines": sorted(named, key=lambda i: -i["value"]),
                             "basis": bases.pop() if len(bases) == 1 else "FMV where reported, else book value",
                             "cite": irs.citation(fo, "other_investments")}
            pf = f["form"] == "990-PF"
            if f["investments_other"] is not None:
                row["alt"] = {"amount": f["investments_other"], "basis": "FMV" if pf else "book value",
                              "cite": irs.citation(fo, "investments_other")}
            parts = [("public securities", f["investments_public"], irs.citation(fo, "investments_public")),
                     ("other investments", f["investments_other"], irs.citation(fo, "investments_other"))]
            if any(v is not None for _, v, _ in parts):
                row["denom"] = {"amount": sum(v or 0 for _, v, _ in parts), "basis": "FMV" if pf else "book value",
                                "blank": [label for label, v, _ in parts if v is None],
                                "cite": "; ".join(_short_cite(c) for _, _, c in parts)}
            d = row["denom"]["amount"] if row["denom"] else 0
            if d > 0:
                row["hf_pct"] = 100 * row["hf"]["amount"] / d if row["hf"] else None
                row["alt_pct"] = 100 * row["alt"]["amount"] / d if row["alt"] else None
        rows.append(row)
    amt = lambda x: x["amount"] if x else None
    rows.sort(key=lambda r: (r["hf"] is None, -(amt(r["hf"]) or 0), r["alt"] is None, -(amt(r["alt"]) or 0),
                             r["latest"] is None, r["name"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
