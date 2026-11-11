"""Per-allocator timelines from documents only (D-B37). Anything computed is labelled as computed.

timeline_searches: one row per manager search, grouped by plan and asset class (bucket) from published, not-failed
signals. Stages: hint -> opened -> finalists -> interviews -> fee_contract -> awarded -> funded, each with the date of the
signal that shows it. next_step_* is the next step AS STATED in the latest stage's own quote (a date printed after the
signal's date), or an upcoming scheduled item; never inferred.

timeline_allocators: next meeting (from an upcoming agenda or a NEXT_MEETING fact, with its source), the current tier and
the date it expires (computed: qualifying signal date + the tier window), the last deployment (date, asset class, firm,
amount, signal), and the typical search length from that plan's own completed searches ("based on N past searches"),
left empty when N < 2.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from datetime import date, timedelta

from radar import db

STAGES = ["hint", "opened", "finalists", "interviews", "fee_contract", "awarded", "funded"]
FEE = re.compile(r"fee (?:discussions?|negotiations?)|contract negotiations?|side letter|subscription documents?", re.I)
INTERVIEW = re.compile(r"interview|presentation|candidate", re.I)
GAP_DAYS = 365

SCHEMA = [
    """CREATE OR REPLACE TABLE timeline_searches (
        allocator_id VARCHAR, search_no INTEGER, asset_class VARCHAR, status VARCHAR, stage_reached VARCHAR,
        hint_date DATE, opened_date DATE, finalists_date DATE, interviews_date DATE, fee_contract_date DATE,
        awarded_date DATE, funded_date DATE, first_date DATE, last_date DATE,
        next_step_text VARCHAR, next_step_date DATE, next_step_quote VARCHAR, next_step_signal_id VARCHAR,
        signal_ids VARCHAR, computed_note VARCHAR)""",
    """CREATE OR REPLACE TABLE timeline_allocators (
        allocator_id VARCHAR PRIMARY KEY, tier VARCHAR, tier_signal_id VARCHAR, tier_expires DATE, tier_expires_note VARCHAR,
        next_meeting_date DATE, next_meeting_source VARCHAR,
        last_deploy_date DATE, last_deploy_asset_class VARCHAR, last_deploy_firm VARCHAR, last_deploy_amount DOUBLE,
        last_deploy_summary VARCHAR, last_deploy_signal_id VARCHAR,
        typical_search_days INTEGER, typical_search_note VARCHAR, open_searches INTEGER, computed_at TIMESTAMPTZ DEFAULT now())""",
]


def _stage(sig: dict) -> str | None:
    t, q = sig["signal_type"], f"{sig['quote'] or ''} {sig['summary'] or ''}"
    if t in ("MANAGER_TERMINATED", "MANAGER_ON_WATCH") or (t == "TARGET_ALLOCATION_CHANGED" and sig["qualifier"] == "increase"):
        return "hint"
    if t in ("SEARCH_OPENED", "RFP_ISSUED"):
        return "opened"
    if t == "FINALISTS_NAMED":
        return "finalists"
    if t == "SEARCH_SCHEDULED":
        return "fee_contract" if FEE.search(q) else "interviews" if INTERVIEW.search(q) else "opened"
    if t == "MANDATE_AWARDED":
        return "awarded"
    if t in ("MANDATE_FUNDED", "COMMITMENT_MADE"):
        return "funded"
    return None


def _stated_next(sig: dict) -> tuple[str | None, date | None]:
    from radar.crawl.links import parse_date
    q = sig["quote"] or ""
    for m in re.finditer(r"(?:[A-Z][a-z]+\.?\s+\d{1,2},?\s+20\d{2}|\d{1,2}/\d{1,2}/20\d{2})", q):
        d = parse_date(m.group(0))
        if d and d > sig["event_date"]:
            s = max(0, m.start() - 120)
            return q[s:m.end() + 40].strip(), d
    return None, None


def build(today: date | None = None) -> dict:
    from radar.readiness import compute, readiness_config
    today = today or date.today()
    cfg = readiness_config()
    windows = {r["tier"]: r["window_days"] for r in cfg["tiers"]}
    with db.connect() as con:
        sigs = db.rows(con, """SELECT s.signal_id, s.allocator_id, s.signal_type, s.qualifier, s.event_date, s.summary, s.bucket,
                                      s.manager_name, s.amount_usd, s.review_status,
                                      (SELECT e.quote FROM row_evidence r JOIN evidence e USING (evidence_id)
                                       WHERE r.row_id = s.signal_id ORDER BY e.extracted_at LIMIT 1) AS quote
                               FROM signals s WHERE s.review_status IN ('approved','auto_approved')
                                 AND coalesce(s.verification_status,'') <> 'failed'""")
        allocs = [r[0] for r in con.execute("SELECT allocator_id FROM allocators").fetchall()]
        upcoming = dict(con.execute("""SELECT allocator_id, min(doc_date) FROM document_urls WHERE doc_date > current_date
                                       AND doc_type IN ('AGENDA','MINUTES','PACKET') GROUP BY 1""").fetchall())
        facts = dict(con.execute("""SELECT allocator_id, min(as_of) FROM plan_facts WHERE fact_kind='NEXT_MEETING'
                                    AND as_of >= current_date GROUP BY 1""").fetchall())
        last_new = dict(con.execute("SELECT allocator_id, max(first_seen) FROM document_urls GROUP BY 1").fetchall())
    by = defaultdict(list)
    for s in sigs:
        by[s["allocator_id"]].append(s)
    srows, arows = [], []
    for aid in allocs:
        mine = sorted(by.get(aid, []), key=lambda s: s["event_date"])
        # searches: manager-qualified search stages plus deployments, grouped by asset class with a 365-day gap rule
        groups: dict[str, list[list[dict]]] = defaultdict(list)
        for s in mine:
            st = _stage(s)
            if not st or (st in ("opened", "finalists", "interviews", "fee_contract", "awarded") and s["qualifier"] != "manager"):
                continue
            key = s["bucket"] or "OTHER"
            g = groups[key]
            if g and (s["event_date"] - g[-1][-1]["event_date"]).days <= GAP_DAYS and \
                    not (st in ("opened", "hint") and any(_stage(x) in ("awarded", "funded") for x in g[-1])):
                g[-1].append(s)
            else:
                g.append([s])
        completed_days, n_open, no = [], 0, 0
        for asset, glist in groups.items():
            for g in glist:
                stages = {}
                for s in g:
                    st = _stage(s)
                    stages.setdefault(st, s["event_date"])
                if set(stages) <= {"funded"} or set(stages) <= {"hint"}:
                    continue  # a lone commitment or a lone hint is not a search
                no += 1
                reached = max(stages, key=STAGES.index)
                status = "closed" if reached in ("awarded", "funded") else (
                    "open" if (today - g[-1]["event_date"]).days <= GAP_DAYS else "stalled")
                n_open += status == "open"
                start = stages.get("opened") or stages.get("finalists") or stages.get("interviews")
                end = stages.get("awarded") or stages.get("funded")
                if status == "closed" and start and end and end >= start:
                    completed_days.append((end - start).days)
                nxt_text = nxt_date = nxt_quote = nxt_sid = None
                future = [s for s in g if s["event_date"] > today]
                if future:
                    f = future[0]
                    nxt_text, nxt_date, nxt_quote, nxt_sid = f["summary"], f["event_date"], f["quote"], f["signal_id"]
                else:
                    t, d = _stated_next(g[-1])
                    if d and d >= today - timedelta(days=30):
                        nxt_text, nxt_date, nxt_quote, nxt_sid = "as stated in the latest document", d, t, g[-1]["signal_id"]
                srows.append([aid, no, asset, status, reached] + [stages.get(k) for k in STAGES] +
                             [g[0]["event_date"], g[-1]["event_date"], nxt_text, nxt_date, nxt_quote, nxt_sid,
                              ",".join(s["signal_id"] for s in g),
                              "grouped by asset class; stages from signal types; status computed (open = last step within 365 days)"])
        # allocator row
        r = compute(mine, last_new.get(aid), today)
        tier_sig = r.reasons[0] if r.reasons else None
        expires = note = None
        if tier_sig and r.tier in windows:
            expires = max(tier_sig["event_date"], today if tier_sig["event_date"] > today else tier_sig["event_date"]) + \
                timedelta(days=windows[r.tier])
            note = f"computed: {tier_sig['signal_type']} dated {tier_sig['event_date']} + {windows[r.tier]}-day {r.tier} window"
        dep = [s for s in mine if s["signal_type"] in ("MANDATE_FUNDED", "COMMITMENT_MADE")
               or (s["signal_type"] == "MANDATE_AWARDED" and s["qualifier"] == "manager")]
        d = dep[-1] if dep else None
        nm, nm_src = (upcoming.get(aid), "upcoming agenda on file") if upcoming.get(aid) else \
            ((facts.get(aid), "next-meeting date printed in a document") if facts.get(aid) else (None, None))
        typical = int(statistics.median(completed_days)) if len(completed_days) >= 2 else None
        arows.append([aid, r.tier, tier_sig["signal_id"] if tier_sig else None, expires, note, nm, nm_src,
                      d["event_date"] if d else None, d["bucket"] if d else None, d["manager_name"] if d else None,
                      d["amount_usd"] if d else None, d["summary"] if d else None, d["signal_id"] if d else None,
                      typical, f"computed: median of {len(completed_days)} past searches (opened to awarded/funded)" if typical else None,
                      n_open])
    with db.connect() as con:
        con.execute("BEGIN")
        for sql in SCHEMA:
            con.execute(sql)
        con.executemany("INSERT INTO timeline_searches VALUES (" + ",".join("?" * 20) + ")", srows)
        con.executemany("INSERT INTO timeline_allocators (allocator_id, tier, tier_signal_id, tier_expires, tier_expires_note, "
                        "next_meeting_date, next_meeting_source, last_deploy_date, last_deploy_asset_class, last_deploy_firm, "
                        "last_deploy_amount, last_deploy_summary, last_deploy_signal_id, typical_search_days, typical_search_note, "
                        "open_searches) VALUES (" + ",".join("?" * 16) + ")", arows)
        db.audit(con, "data-session", "timeline_build", "timeline", detail={"searches": len(srows), "allocators": len(arows)})
        con.execute("COMMIT")
    return {"searches": len(srows), "open": sum(1 for r in srows if r[3] == "open"), "allocators": len(arows),
            "with_last_deploy": sum(1 for r in arows if r[7]), "with_next_meeting": sum(1 for r in arows if r[5]),
            "with_typical": sum(1 for r in arows if r[13])}
