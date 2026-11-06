"""Tiered auto-approval (config/auto_approval.yaml).

A pending signal is set to review_status = 'auto_approved' only if ALL hold:
  a) its type is not in the excluded set (terminations, watch changes, redemptions)
  b) it came from a deterministic table parser, OR at least one evidence quote string-matches the cited page
  c) it has a source-native date basis (meeting / report / publication / filing / notice date, or a date printed
     on the cited page); a quarter-end fallback does not qualify
  d) its summary passes the evaluative-language lint
Every decision (approve or leave pending) is written to audit_log with the rule version and the reason.
'auto_approved' counts toward readiness tiers; it never satisfies the CSV export, which needs a person's 'approved'.
"""

from __future__ import annotations

import re
from datetime import date

from radar import db
from radar.extract.extractor import quote_on_page
from radar.settings import load_yaml
from radar.wall.lint import lint

ACTOR = "auto-approval"
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]


def _cfg() -> dict:
    return load_yaml("auto_approval.yaml")


def date_printed(d: date, text: str) -> bool:
    """Is this date (or its month and year) printed in the text?"""
    t = (text or "").lower()
    m = _MONTHS[d.month - 1]
    forms = [f"{m} {d.day}, {d.year}", f"{m} {d.day} {d.year}", f"{m[:3]} {d.day}, {d.year}", f"{m[:3]}. {d.day}, {d.year}",
             f"{d.month}/{d.day}/{d.year}", f"{d.month:02d}/{d.day:02d}/{d.year}", f"{d.month}/{d.day}/{str(d.year)[2:]}",
             f"{d.month:02d}-{d.day:02d}-{d.year}", d.isoformat(), f"{d.month}.{d.day}.{d.year}"]
    return any(f in t for f in forms) or bool(re.search(rf"\b{m}\s+{d.year}\b", t))


def infer_date_basis(con, sig: dict, evidence: list[dict]) -> str | None:
    """For machine signals without a recorded basis: the cited document's own date, or a date printed on the page."""
    if sig.get("date_basis"):
        return sig["date_basis"]
    for e in evidence:
        if not e.get("document_sha256"):
            continue
        r = con.execute("""SELECT max(du.doc_date), any_value(d.content_date) FROM document_urls du JOIN documents d USING (sha256)
                           WHERE du.sha256 = ?""", [e["document_sha256"]]).fetchone()
        if r and sig["event_date"] in (r[0], r[1]):
            return "document_date"
        page = con.execute("SELECT text_scrubbed FROM pages WHERE sha256 = ? AND page_no = ?",
                           [e["document_sha256"], e.get("page")]).fetchone()
        if page and date_printed(sig["event_date"], page[0]):
            return "printed_on_page"
    return None


def evaluate(con, sig: dict, evidence: list[dict], cfg: dict | None = None) -> tuple[bool, str, str | None]:
    cfg = cfg or _cfg()
    if sig["signal_type"] in cfg["excluded_signal_types"]:
        return False, f"(a) {sig['signal_type']} always needs a person", None
    deterministic = any(e.get("extractor") in cfg["deterministic_extractors"] for e in evidence)
    faithful = False
    for e in evidence:
        if e.get("document_sha256") and e.get("page"):
            page = con.execute("SELECT text_scrubbed FROM pages WHERE sha256 = ? AND page_no = ?",
                               [e["document_sha256"], e["page"]]).fetchone()
            if page and quote_on_page(e["quote"], page[0]):
                faithful = True
                break
    if not (deterministic or faithful):
        return False, "(b) not from a parsed table and no quote matches its cited page", None
    basis = infer_date_basis(con, sig, evidence)
    table_ok = deterministic and basis in cfg.get("deterministic_only_date_bases", [])
    if not sig.get("event_date") or not (basis in cfg["qualifying_date_bases"] or table_ok):
        why = ("quarter_end qualifies only for table-extracted rows" if basis in cfg.get("deterministic_only_date_bases", [])
               else "is not a source-native meeting/report/filing date")
        return False, f"(c) date basis {basis or 'unknown'} {why}", basis
    # Proper names printed in the source are not our wording (D-A42): for table rows, every printed cell is ignored.
    printed = [c.strip() for e in evidence if e.get("extractor") in cfg["deterministic_extractors"]
               for c in (e.get("quote") or "").split(" | ")]
    if not lint(sig["summary"], ignore=[sig.get("manager_name"), sig.get("bucket_label"), *printed]).ok:
        return False, "(d) summary fails the language lint", basis
    how = "parsed table" if deterministic else "quote matches cited page"
    return True, f"{how}; date basis {basis}; lint clean; type {sig['signal_type']} eligible", basis


def run_pass() -> dict:
    from radar import queries

    cfg = _cfg()
    stats = {"checked": 0, "auto_approved": 0, "left_pending": 0, "reasons": {}}
    with db.connect() as con:
        sigs = queries.signals_for(con, statuses=("new",))
    for s in sigs:
        stats["checked"] += 1
        with db.connect() as con:
            ok, reason, basis = evaluate(con, s, s["evidence"], cfg)
            if basis and not s.get("date_basis"):
                con.execute("UPDATE signals SET date_basis = ? WHERE signal_id = ?", [basis, s["signal_id"]])
            if ok:
                con.execute("UPDATE signals SET review_status = 'auto_approved', reviewed_by = ?, reviewed_at = now(), "
                            "review_note = ? WHERE signal_id = ? AND review_status = 'new'",
                            [f"{ACTOR} {cfg['rule_version']}", reason, s["signal_id"]])
                stats["auto_approved"] += 1
            else:
                stats["left_pending"] += 1
                key = reason.split(")")[0] + ")"
                stats["reasons"][key] = stats["reasons"].get(key, 0) + 1
            db.audit(con, ACTOR, "signal_auto_approved" if ok else "signal_left_pending", s["signal_id"],
                     detail={"rule_version": cfg["rule_version"], "reason": reason, "date_basis": basis})
    return stats
