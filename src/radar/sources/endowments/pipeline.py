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


def load_irs(client, eins: list[str]) -> dict:
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
                if con.execute("SELECT 1 FROM ef_filings WHERE object_id = ?", [r.object_id]).fetchone():
                    continue
            try:
                f = irs.parse_return(irs.fetch_return(client, r, _cache()), r)
            except Exception as e:  # noqa: BLE001
                log.warning("irs %s %s: %s", ein, r.object_id, e)
                stats["failed"] += 1
                continue
            with store.connect() as con:
                con.execute("INSERT OR REPLACE INTO ef_filings VALUES (?,?,?,?,?,?,?,?,?,?,?, now())",
                            [f.object_id, ein, f.return_type, f.tax_year, f.tax_period_end or None, f.total_assets_eoy,
                             f.investments_public_eoy, f.investments_other_eoy, f.contractor_count_over_100k,
                             r.index_year, r.batch])
                con.execute("DELETE FROM ef_contractors WHERE object_id = ?", [f.object_id])
                con.execute("DELETE FROM ef_other_investments WHERE object_id = ?", [f.object_id])
                for c in f.contractors:
                    con.execute("INSERT INTO ef_contractors VALUES (?,?,?,?,?,?,?)",
                                [f.object_id, ein, f.tax_year, c.firm, c.service, c.amount, c.investment])
                for o in f.other_investments:
                    con.execute("INSERT INTO ef_other_investments VALUES (?,?,?,?,?,?)",
                                [f.object_id, ein, f.tax_year, o.description, o.book_value, o.fmv])
            stats["returns"] += 1
    return stats


def derive_signals() -> int:
    """Rebuild the 990 signals from the stored filings (committee signals are kept)."""
    with store.connect() as con:
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
    return n


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
    out["signals_990"] = derive_signals()
    return out
