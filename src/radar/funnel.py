"""Deploying funnel: the before/after scoreboard for finding more DEPLOYING plans.

Counts only; no judgments. Computed over the plans in scope (config/scope.yaml).
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from radar import coverage, queries, scope
from radar.readiness import COUNTING_STATUSES
from radar.settings import readiness_config


def deploying_types() -> list[str]:
    rule = next(r for r in readiness_config()["tiers"] if r["tier"] == "DEPLOYING")
    return list(rule["signal_types"])


def compute(con, today: date | None = None) -> dict:
    today = today or date.today()
    in_scope = scope.ids(con)
    rows = [r for r in queries.allocator_table(con, scoped=False)
            if in_scope is None or r["allocator_id"] in in_scope]
    ids = [r["allocator_id"] for r in rows]
    d90, d180 = today - timedelta(days=90), today - timedelta(days=180)

    recent_meeting_docs = {a for (a,) in con.execute(
        """SELECT DISTINCT allocator_id FROM document_urls
           WHERE doc_type IN ('AGENDA', 'MINUTES') AND doc_date BETWEEN ? AND ?""",
        [d90, today + timedelta(days=90)]).fetchall()} & set(ids)  # posted agendas for upcoming meetings count

    types = deploying_types()
    sigs = con.execute(
        f"""SELECT allocator_id, signal_type, coalesce(qualifier, '-'), event_date, review_status FROM signals
            WHERE signal_type IN ({','.join('?' * len(types))})""", types).fetchall()
    sigs = [s for s in sigs if s[0] in set(ids)]

    def age(d):
        if d >= d90:
            return "le90"
        return "91_180" if d >= d180 else "gt180"

    search_by_qual_age = Counter(f"{q}/{age(d)}" for _, _, q, d, st in sigs if st in COUNTING_STATUSES)
    pending = Counter(st for _, _, _, _, st in sigs if st not in COUNTING_STATUSES and st != "rejected")
    all_pending = con.execute("SELECT count(*) FROM signals WHERE review_status IN ('new', 'pending')").fetchone()[0]
    gaps = Counter(coverage.gap_category(con, r)[0] for r in rows if r["bar_reasons"])
    no_docs = sum(1 for r in rows if not (r.get("n_docs") or 0))
    docs_no_sig = [a for a in ids if (con.execute("SELECT count(*) FROM document_urls WHERE allocator_id = ?", [a])
                                      .fetchone()[0] and not con.execute(
                                          "SELECT count(*) FROM signals WHERE allocator_id = ?", [a]).fetchone()[0])]
    return {
        "as_of": today.isoformat(),
        "plans_in_scope": len(rows),
        "tiers": dict(Counter(r["readiness"].tier for r in rows)),
        "deploying": sorted(r["allocator_id"] for r in rows if r["readiness"].tier == "DEPLOYING"),
        "plans_with_agenda_or_minutes_le90": len(recent_meeting_docs),
        "manager_search_signals_le90": search_by_qual_age.get("manager/le90", 0),
        "search_signals_by_qualifier_age": dict(sorted(search_by_qual_age.items())),
        "search_signals_pending": sum(pending.values()),
        "signals_pending_all": all_pending,
        "plans_zero_documents": no_docs,
        "plans_documents_but_no_signals": len(docs_no_sig),
        "gaps_by_cause": {c: gaps.get(c, 0) for c, _, _ in coverage.GAP_CATEGORIES},
    }


def render(f: dict) -> str:
    return "\n".join([
        f"\nDEPLOYING FUNNEL (in scope, as of {f['as_of']})",
        f"  plans in scope                        {f['plans_in_scope']}",
        f"  tiers                                 {f['tiers']}",
        f"  DEPLOYING plans                       {len(f['deploying'])} {f['deploying']}",
        f"  plans with agenda/minutes <=90 days   {f['plans_with_agenda_or_minutes_le90']}",
        f"  manager search signals <=90 days      {f['manager_search_signals_le90']}",
        f"  search signals by qualifier/age       {f['search_signals_by_qualifier_age']}",
        f"  search signals pending review         {f['search_signals_pending']} (all pending: {f['signals_pending_all']})",
        f"  plans with zero documents             {f['plans_zero_documents']}",
        f"  plans with documents, no signals      {f['plans_documents_but_no_signals']}",
        f"  gaps by cause                         {f['gaps_by_cause']}",
    ])
