"""Phase C load: universe -> ProPublica filing summaries -> IRS e-file XML -> 990 signals.

`radar ef-load` runs it. Every request goes through radar.net.client. Blocked items go to
data/manual_downloads.md (the system never retries around a block).
"""

from __future__ import annotations

import json
import logging
import pickle
from collections import defaultdict
from datetime import date
from pathlib import Path

from radar.settings import get_settings
from radar.sources.endowments import irs, store, universe
from radar.sources.endowments import signals as S

log = logging.getLogger(__name__)
PP_API = "https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json"
INDEX_YEARS = (2023, 2024, 2025)
RETURNS_PER_ORG = 3


def _cache() -> Path:
    return get_settings().data_dir / "ef_cache"


def manual_note(item: str, why: str) -> None:
    p = get_settings().data_dir / "manual_downloads.md"
    if not p.exists():
        p.write_text("# Manual downloads (Phase C)\n\nItems the system could not fetch under its rules (robots.txt, bot "
                     "wall, no public URL). A person may download them in a browser; the system never works around a block.\n\n")
    line = f"- {item}: {why}\n"
    if line not in p.read_text():
        with p.open("a") as fh:
            fh.write(line)


def build_universe(client) -> list[dict]:
    cache = _cache() / "bmf_fl.pkl"
    if cache.exists():
        bmf = pickle.loads(cache.read_bytes())
    else:
        r = client.get(universe.BMF_URL, max_bytes=200 * 1024 * 1024)
        bmf = universe.load_bmf(r.content.decode("utf-8", errors="replace"))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(bmf))  # EIN, name, city, codes, assets only
    picked = universe.select(bmf)
    universe.write_csv(picked, get_settings().data_dir / "ef_universe.csv", date.today().isoformat())
    store.init()
    with store.connect() as con:
        for r in picked:
            con.execute("""INSERT OR REPLACE INTO ef_orgs (ein, name, kind, city, ntee, foundation_code, bmf_assets,
                           bmf_tax_period, rank_in_kind) VALUES (?,?,?,?,?,?,?,?,?)""",
                        [r["EIN"], r["NAME"], r["kind"], r["CITY"].title(), r["NTEE_CD"], r["FOUNDATION"], r["ASSET_AMT"],
                         r["TAX_PERIOD"], r["rank_in_kind"]])
    return picked


def load_propublica(client, eins: list[str]) -> int:
    n = 0
    for ein in eins:
        try:
            d = json.loads(client.get(PP_API.format(ein=int(ein))).content)
        except Exception as e:  # noqa: BLE001 - one organization's failure never stops the run
            log.warning("propublica %s: %s", ein, e)
            manual_note(f"ProPublica API record for EIN {ein}", f"request failed ({type(e).__name__})")
            continue
        with store.connect() as con:
            for f in d.get("filings_with_data", [])[:6]:
                form = {0: "990", 1: "990EZ", 2: "990PF"}.get(f.get("formtype"), str(f.get("formtype")))
                total = f.get("totassetsend") or f.get("fairmrktvalamt")
                con.execute("INSERT OR REPLACE INTO ef_pp_filings VALUES (?,?,?,?,?,?,?,?)",
                            [ein, str(f.get("tax_prd")), f.get("tax_prd_yr"), form, total, f.get("totliabend"),
                             f.get("totnetassetend"), f.get("totrevenue") or f.get("totrcptperbks")])
        n += 1
    return n


def load_irs(client, eins: list[str], reparse: bool = False) -> dict:
    """reparse: re-read returns already stored (the raw XML is never kept, so new fields mean a fresh ranged GET)."""
    rows: list[irs.IndexRow] = []
    for y in INDEX_YEARS:
        rows += irs.index_rows(client, y, set(eins), _cache())
    by_org: dict[str, dict[str, irs.IndexRow]] = defaultdict(dict)
    for r in rows:
        if r.return_type not in ("990", "990PF"):
            continue
        cur = by_org[r.ein].get(r.tax_period)
        if cur is None or r.object_id > cur.object_id:  # an amended return supersedes
            by_org[r.ein][r.tax_period] = r
    stats = {"returns": 0, "failed": 0, "orgs_with_returns": 0}
    for ein in eins:
        periods = sorted(by_org.get(ein, {}).items(), reverse=True)[:RETURNS_PER_ORG]
        if not periods:
            manual_note(f"IRS e-file XML for EIN {ein}", f"no 990/990-PF in the {INDEX_YEARS[0]}-{INDEX_YEARS[-1]} TEOS indexes "
                        "(paper filer, or not yet published)")
            continue
        stats["orgs_with_returns"] += 1
        for _, r in periods:
            with store.connect() as con:
                if con.execute("SELECT 1 FROM ef_filings WHERE object_id = ?" + (" AND officers_read" if reparse else ""),
                               [r.object_id]).fetchone():
                    continue
            try:
                f = irs.parse_return(irs.fetch_return(client, r, _cache()), r)
            except Exception as e:  # noqa: BLE001
                log.warning("irs %s %s: %s", ein, r.object_id, e)
                manual_note(f"IRS e-file XML for EIN {ein}, object {r.object_id} (TY ending {r.tax_period})",
                            f"{type(e).__name__}: {str(e)[:100]}; the PDF can be opened on ProPublica's filing page")
                stats["failed"] += 1
                continue
            with store.connect() as con:
                con.execute("""INSERT OR REPLACE INTO ef_filings (object_id, ein, return_type, tax_year, tax_period_end,
                                   total_assets, investments_public, investments_other, contractors_over_100k, index_year,
                                   batch, fetched_at, endowment_eoy, endowment_boy, investments_program, investments_land,
                                   investments_mortgage, investments_other_book, officers_read)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?, now(), ?,?,?,?,?,?, true)""",
                            [f.object_id, ein, f.return_type, f.tax_year, f.tax_period_end or None, f.total_assets_eoy,
                             f.investments_public_eoy, f.investments_other_eoy, f.contractor_count_over_100k,
                             r.index_year, r.batch, f.endowment_eoy, f.endowment_boy, f.investments_program_eoy,
                             f.investments_land_eoy, f.investments_mortgage_eoy, f.investments_other_book_eoy])
                con.execute("DELETE FROM ef_officers WHERE object_id = ?", [f.object_id])
                ocite = irs.citation(f, "officers")
                for o in f.officers:
                    con.execute("INSERT INTO ef_officers VALUES (?,?,?,?,?,?,?)",
                                [f.object_id, ein, f.tax_year, o.name[:120], o.title[:120], o.role, ocite])
                con.execute("DELETE FROM ef_contractors WHERE object_id = ?", [f.object_id])
                con.execute("DELETE FROM ef_other_investments WHERE object_id = ?", [f.object_id])
                for c in f.contractors:
                    con.execute("INSERT INTO ef_contractors (object_id, ein, tax_year, firm, service, amount, investment) "
                                "VALUES (?,?,?,?,?,?,?)", [f.object_id, ein, f.tax_year, c.firm, c.service, c.amount, c.investment])
                for o in f.other_investments:
                    con.execute("INSERT INTO ef_other_investments (object_id, ein, tax_year, description, book_value, fmv, valuation) "
                                "VALUES (?,?,?,?,?,?,?)", [f.object_id, ein, f.tax_year, o.description, o.book_value, o.fmv,
                                                          o.valuation])
            stats["returns"] += 1
    return stats


def prune_hospitals() -> list[str]:
    """Drop hospital systems whose latest return shows investments under $100M (D-B32). Returns the EINs dropped."""
    from radar.sources.endowments.exposure import total_investments
    dropped = []
    with store.connect() as con:
        for (ein,) in con.execute("SELECT ein FROM ef_orgs WHERE kind = 'HOSPITAL_SYSTEM'").fetchall():
            f = store.rows(con, "SELECT * FROM ef_filings WHERE ein = ? ORDER BY tax_period_end DESC LIMIT 1", [ein])
            inv = total_investments(f[0]) if f else None
            if inv is not None and inv >= universe.HOSPITAL_MIN_INVESTMENTS:
                continue
            dropped.append(ein)
            for t in ("ef_signals", "ef_contractors", "ef_other_investments", "ef_officers", "ef_exposure", "ef_filings",
                      "ef_pp_filings", "ef_orgs"):
                con.execute(f"DELETE FROM {t} WHERE ein = ?", [ein])
    return dropped


def derive_signals() -> int:
    """Rebuild the 990 signals from the stored filings (committee signals are kept)."""
    with store.connect() as con:
        lists = irs._firm_lists()
        for firm, svc in con.execute("SELECT DISTINCT firm, service FROM ef_contractors").fetchall():  # current rules
            cls, basis = irs.classify_contractor(firm, svc, lists)
            con.execute("UPDATE ef_contractors SET contractor_class = ?, class_basis = ?, investment = ? "
                        "WHERE firm = ? AND service IS NOT DISTINCT FROM ?",
                        [cls, basis, cls in ("manager", "consultant"), firm, svc])
        from radar.sources.endowments import exposure as X
        for oid, desc, bv, fmv in con.execute("SELECT object_id, description, book_value, fmv FROM ef_other_investments").fetchall():
            con.execute("UPDATE ef_other_investments SET value_status = ? WHERE object_id = ? AND description = ? "
                        "AND book_value IS NOT DISTINCT FROM ? AND fmv IS NOT DISTINCT FROM ?",
                        [X.value_status({"book_value": bv, "fmv": fmv}), oid, desc, bv, fmv])
        con.execute("DELETE FROM ef_exposure")
        for f in store.rows(con, "SELECT * FROM ef_filings"):
            lines = store.rows(con, "SELECT * FROM ef_other_investments WHERE object_id = ?", [f["object_id"]])
            e = X.compute(f, lines)
            con.execute("INSERT INTO ef_exposure VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [f["object_id"], f["ein"], f["tax_year"], _cite_form(f), e["investments_total"], e["hf_usd"],
                         e["alt_usd"], e["hf_pct"], e["alt_pct"], e["hf_status"], e["alt_status"], e["basis"],
                         _cite(f, "other_investments"), _cite(f, "investments_total"), e["n_lines"]])
        orgs = store.rows(con, "SELECT * FROM ef_orgs")
        con.execute("DELETE FROM ef_signals WHERE source_type = '990'")
        n = 0
        for o in orgs:
            fs = store.rows(con, "SELECT * FROM ef_filings WHERE ein = ? ORDER BY tax_period_end DESC", [o["ein"]])
            ids = [f["object_id"] for f in fs]
            if not ids:
                continue
            ph = ",".join("?" * len(ids))
            cs, os_ = defaultdict(list), defaultdict(list)
            for c in store.rows(con, f"SELECT * FROM ef_contractors WHERE object_id IN ({ph})", ids):
                cs[c["object_id"]].append(c)
            for x in store.rows(con, f"SELECT * FROM ef_other_investments WHERE object_id IN ({ph})", ids):
                os_[x["object_id"]].append(x)
            for f in fs:
                cs.setdefault(f["object_id"], [])  # a parsed return with no firm contractors is still "known"
            for s in S.derive(o, fs, cs, os_, irs.filing_url, _cite):
                con.execute("""INSERT OR REPLACE INTO ef_signals (signal_id, ein, signal_type, qualifier, event_date, date_basis,
                               summary, source_type, source_url, doc_title, cite, page, quote, hf_relevant, review_status,
                               reviewed_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            [s[k] for k in ("signal_id", "ein", "signal_type", "qualifier", "event_date", "date_basis", "summary",
                                            "source_type", "source_url", "doc_title", "cite", "page", "quote", "hf_relevant",
                                            "review_status", "reviewed_by")])
                n += 1
        _signal_notes(con)
    return n


def _signal_notes(con) -> None:
    """3d: for an organization with no 990 change signal, say why, from what its filings show (D-B29)."""
    for o in store.rows(con, "SELECT ein FROM ef_orgs"):
        ein = o["ein"]
        has = con.execute("SELECT count(*) FROM ef_signals WHERE ein = ? AND signal_type IN "
                          "('EF_MANAGER_ADDED','EF_MANAGER_DROPPED','EF_ALT_HOLDING_ADDED') OR (ein = ? AND source_type='committee')",
                          [ein, ein]).fetchone()[0]
        fs = store.rows(con, "SELECT * FROM ef_filings WHERE ein = ? ORDER BY tax_period_end DESC", [ein])
        if has:
            note = None
        elif not fs:
            note = "No e-filed 990 or 990-PF in the IRS TEOS indexes read (2022-2026); nothing to compare."
        else:
            f = fs[0]
            inv = con.execute("SELECT count(*) FROM ef_contractors WHERE object_id = ? AND contractor_class IN ('manager','consultant')",
                              [f["object_id"]]).fetchone()[0]
            lines = con.execute("SELECT count(*) FROM ef_other_investments WHERE object_id = ?", [f["object_id"]]).fetchone()[0]
            parts = [f"Filings re-read through TY{f['tax_year']} ({len(fs)} return{'s' if len(fs) != 1 else ''})."]
            parts.append(f"{inv} investment manager or consultant among the highest-paid contractors" if inv else
                         "No investment manager or consultant among the highest-paid contractors")
            parts.append(f"{lines} itemized other-investment lines, none new against the prior return" if lines else
                         "no itemized other-investment lines (Schedule D Part VII / 990-PF line 13 statement)")
            if len(fs) < 2:
                parts.append("only one return on file, so no year-on-year change can be read")
            note = parts[0] + " " + "; ".join(parts[1:]) + "."
        con.execute("UPDATE ef_orgs SET signal_note = ? WHERE ein = ?", [note, ein])


def _cite_form(f: dict) -> str:
    return "990-PF" if str(f["return_type"]).upper().startswith("990PF") else "990"


def _cite(f: dict, what: str) -> str:
    return irs.citation(irs.Filing(f["ein"], f["object_id"], f["return_type"], str(f["tax_period_end"]), f["tax_year"]), what)


def run(client=None) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    from radar.net.client import PoliteClient

    client = client or PoliteClient(purpose="ef-990")
    picked = build_universe(client)
    eins = [r["EIN"] for r in picked]
    with ThreadPoolExecutor(max_workers=2) as ex:  # two hosts, each with its own polite delay
        pp = ex.submit(load_propublica, client, eins)
        ir = ex.submit(load_irs, client, eins)
        out = {"orgs": len(picked), "propublica": pp.result(), "irs": ir.result()}
    out["hospitals_dropped_under_100m"] = prune_hospitals()
    out["signals_990"] = derive_signals()
    return out
