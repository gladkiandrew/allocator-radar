"""Read-side queries for the UI and export. All text returned here is already scrubbed."""

from __future__ import annotations

from collections import defaultdict

from radar import db
from radar.readiness import TIER_ORDER, compute


def _evidence_for(con, kind: str, ids: list[str]) -> dict[str, list[dict]]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rs = db.rows(
        con,
        f"""SELECT re.row_id, e.*, du.title AS doc_title, du.doc_type, du.doc_date
            FROM row_evidence re JOIN evidence e USING (evidence_id)
            LEFT JOIN (SELECT sha256, url, any_value(title) AS title, any_value(doc_type) AS doc_type,
                              any_value(doc_date) AS doc_date FROM document_urls GROUP BY sha256, url) du
              ON du.sha256 = e.document_sha256 AND du.url = e.source_url
            WHERE re.row_kind = ? AND re.row_id IN ({marks})
            ORDER BY e.page""",
        [kind, *ids],
    )
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rs:
        out[r["row_id"]].append(r)
    return out


def signals_for(con, allocator_ids: list[str] | None = None,
                statuses: tuple[str, ...] = ("new", "approved", "auto_approved")) -> list[dict]:
    where, params = [f"review_status IN ({','.join('?' * len(statuses))})"], list(statuses)
    if allocator_ids is not None:
        if not allocator_ids:
            return []
        where.append(f"allocator_id IN ({','.join('?' * len(allocator_ids))})")
        params += allocator_ids
    sigs = db.rows(con, f"SELECT * FROM signals WHERE {' AND '.join(where)} ORDER BY event_date DESC", params)
    ev = _evidence_for(con, "signal", [s["signal_id"] for s in sigs])
    for s in sigs:
        s["evidence"] = ev.get(s["signal_id"], [])
    return sigs


def allocator_table(con, scoped: bool = True) -> list[dict]:
    from radar import scope

    allocs = db.rows(con, "SELECT * FROM allocators ORDER BY display_name")
    if scoped:
        allocs = [a for a in allocs if scope.contains(a)]
    docs = {
        r["allocator_id"]: r
        for r in db.rows(
            con,
            """SELECT allocator_id, count(DISTINCT sha256) AS n_docs, max(first_seen) AS last_new_document,
                      max(last_fetched) AS last_fetched,
                      -- a document dated after today (an agenda for an upcoming meeting) is never the newest document
                      max(doc_date) FILTER (WHERE doc_date <= current_date) AS newest_doc_date,
                      min(doc_date) FILTER (WHERE doc_date > current_date) AS upcoming_doc_date
               FROM document_urls GROUP BY allocator_id""",
        )
    }
    # Latest-dated consultant engagements per plan, minus rows that name a manager rather than a consultant
    # (see shown_engagements). Several consultants on the same date (e.g. per asset class) are all shown.
    from radar.resolve import canonical_map

    cmap = canonical_map(con, "consultant")
    eng_rows = db.rows(con, """SELECT c.* FROM consultant_engagements c
                               JOIN (SELECT allocator_id, max(as_of) AS as_of FROM consultant_engagements GROUP BY 1) m
                                 ON m.allocator_id = c.allocator_id AND m.as_of = c.as_of""")
    eng_ev = _evidence_for(con, "engagement", [e["engagement_id"] for e in eng_rows])
    by_plan: dict[str, list[dict]] = defaultdict(list)
    for e in eng_rows:
        e["evidence"] = eng_ev.get(e["engagement_id"], [])
        by_plan[e["allocator_id"]].append(e)
    consultants = {}
    for aid, es in by_plan.items():
        names = sorted({cmap.get(x.strip(), x.strip()) for e in shown_engagements(es)
                        for x in e["consultant_name_as_disclosed"].split(" | ")})
        if names:
            consultants[aid] = {"allocator_id": aid, "consultant": ", ".join(names), "as_of": es[0]["as_of"],
                                "n": len(names)}
    sigs_by: dict[str, list[dict]] = defaultdict(list)
    for s in signals_for(con):
        sigs_by[s["allocator_id"]].append(s)
    out = []
    for a in allocs:
        d = docs.get(a["allocator_id"], {})
        sigs = sigs_by.get(a["allocator_id"], [])
        r = compute(sigs, d.get("last_new_document"))
        from radar import coverage

        fut = con.execute("SELECT min(as_of) FROM plan_facts WHERE allocator_id = ? AND fact_kind = 'NEXT_MEETING' "
                          "AND as_of >= current_date", [a["allocator_id"]]).fetchone()[0]
        a.update(
            county=scope.county(a),
            next_meeting=fut,
            coverage_pct=coverage.checklist(con, a["allocator_id"])["pct"],
            bar_reasons=coverage.bar(con, a),
            readiness=r,
            n_docs=d.get("n_docs", 0),
            last_fetched=d.get("last_fetched"),
            newest_doc_date=d.get("newest_doc_date"),
            upcoming_doc_date=d.get("upcoming_doc_date"),
            consultant=consultants.get(a["allocator_id"]),
            pending=[s for s in sigs if s["review_status"] == "new"],
        )
        out.append(a)
    out.sort(key=lambda a: (TIER_ORDER.index(a["readiness"].tier),
                            -(a["readiness"].reasons[0]["event_date"].toordinal() if a["readiness"].reasons else 0),
                            a["display_name"]))
    return out


def _not_consultants() -> set[str]:
    """Firms DATA has marked as not consultants: `not_consultant:` list in data/consultant_aliases.yaml (optional)."""
    import yaml

    from radar.settings import get_settings

    p = get_settings().data_dir / "consultant_aliases.yaml"
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return set()
    return {_norm_label(x) for x in (data.get("not_consultant") or []) if isinstance(x, str)}


def _names_own_manager(e: dict) -> bool:
    """A per-deal table row (Asset class | Manager | Account | Commitment | Consultant | ...) whose consultant cell
    names the row's own manager, e.g. "Real Estate | Invesco Advisers Inc | ... | Invesco | Joint Venture"."""
    name = _norm_label(e.get("consultant_name_as_disclosed"))
    first = name.split()[0] if name else ""
    for ev in e.get("evidence") or []:
        cells = [c.strip() for c in (ev.get("quote") or "").split("|")]
        if len(cells) >= 5 and first and first in _norm_label(cells[1]).split():
            return True
    return False


def shown_engagements(engagements: list[dict]) -> list[dict]:
    """Consultant engagements fit to display as consultants: drop rows that name the deal's own manager, and firms
    listed under not_consultant. Stored rows are untouched."""
    nc = _not_consultants()
    return [e for e in engagements
            if e.get("role") != "per_deal_manager"  # DATA's role column (D-B31)
            and not _names_own_manager(e) and _norm_label(e.get("consultant_name_as_disclosed")) not in nc]


def pending_count(con) -> int:
    from radar import scope

    ids = scope.ids(con)
    if ids is None:
        return con.execute("SELECT count(*) FROM signals WHERE review_status = 'new'").fetchone()[0]
    return sum(1 for (i,) in con.execute("SELECT allocator_id FROM signals WHERE review_status = 'new'").fetchall() if i in ids)


def allocator_brief(con, allocator_id: str) -> dict | None:
    rs = db.rows(con, "SELECT * FROM allocators WHERE allocator_id = ?", [allocator_id])
    if not rs:
        return None
    a = rs[0]
    from radar import scope

    a["county"] = scope.county(a)
    a["pages"] = db.rows(con, "SELECT * FROM allocator_pages WHERE allocator_id = ? ORDER BY url", [allocator_id])
    a["documents"] = db.rows(
        con,
        """SELECT du.*, d.page_count, d.parse_error, d.provenance,
                  (SELECT count(*) FROM pages p WHERE p.sha256 = du.sha256 AND p.prefilter_terms IS NOT NULL) AS hit_pages
           FROM document_urls du JOIN documents d USING (sha256)
           WHERE du.allocator_id = ? ORDER BY du.doc_date DESC NULLS LAST, du.first_seen DESC""",
        [allocator_id],
    )
    sigs = signals_for(con, [allocator_id], statuses=("new", "approved", "auto_approved", "rejected"))
    a["signals"] = sigs
    last_new = max((d["first_seen"] for d in a["documents"]), default=None)
    a["readiness"] = compute(sigs, last_new)
    for kind, table, key, order in [
        ("mandate", "mandates", "mandate_id", "as_of DESC, bucket_label_as_disclosed"),
        ("officer", "officers", "officer_id", "as_of DESC, role_type, name_as_printed"),
        ("engagement", "consultant_engagements", "engagement_id", "as_of DESC"),
    ]:
        items = db.rows(con, f"SELECT * FROM {table} WHERE allocator_id = ? ORDER BY {order}", [allocator_id])
        ev = _evidence_for(con, kind, [i[key] for i in items])
        for i in items:
            i["evidence"] = ev.get(i[key], [])
        a[table] = items
    a["consultants_shown"] = shown_engagements([e for e in a["consultant_engagements"]
                                                if a["consultant_engagements"] and e["as_of"] == a["consultant_engagements"][0]["as_of"]])
    from radar import coverage

    a["facts"] = plan_facts(con, allocator_id)
    a["managers_as_of"], a["managers"] = managers_by_class(a["mandates"])
    a["coverage"] = coverage.checklist(con, allocator_id)
    a["bar_reasons"] = coverage.bar(con, a)
    return a


def _norm_label(s: str | None) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower().replace("&", " and ")).strip()


def plan_facts(con, allocator_id: str) -> dict:
    """Latest cited plan facts, de-duplicated for display. Every value keeps its evidence."""
    from datetime import date

    rows = db.rows(con, "SELECT * FROM plan_facts WHERE allocator_id = ? ORDER BY as_of DESC", [allocator_id])
    ev = _evidence_for(con, "fact", [r["fact_id"] for r in rows])
    for r in rows:
        r["evidence"] = ev.get(r["fact_id"], [])
    by = {}
    for r in rows:
        by.setdefault(r["fact_kind"], []).append(r)

    def latest(kind):
        xs = by.get(kind, [])
        return xs[0] if xs else None

    def table(kind):
        xs = by.get(kind, [])
        if not xs:
            return None, {}
        as_of = max(x["as_of"] for x in xs)
        cur = {}
        for x in xs:
            if x["as_of"] != as_of or x["value_num"] is None:
                continue
            k = _norm_label(x["label"])
            prev = cur.get(k)
            # same label printed twice (summary vs detail page): keep the more precise figure
            if prev is None or len(f"{x['value_num']:g}") > len(f"{prev['value_num']:g}"):
                cur[k] = x
        return as_of, cur

    t_asof, targets = table("TARGET_ALLOCATION")
    a_asof, actuals = table("ACTUAL_ALLOCATION")
    alloc = []
    for k in list(targets) + [k for k in actuals if k not in targets]:
        alloc.append({"label": (targets.get(k) or actuals.get(k))["label"], "target": targets.get(k), "actual": actuals.get(k)})
    today = date.today()
    future = sorted([x for x in by.get("NEXT_MEETING", []) if x["as_of"] >= today], key=lambda x: x["as_of"])
    return {
        "total_assets": latest("TOTAL_ASSETS"),
        "alloc": alloc, "target_as_of": t_asof, "actual_as_of": a_asof,
        "providers": {k: latest(k) for k in ("INVESTMENT_CONSULTANT", "ACTUARY", "CUSTODIAN", "AUDITOR")},
        "assumed_return": latest("ASSUMED_RETURN"),
        "next_meeting": future[0] if future else None,
    }


def managers_by_class(mandates: list[dict]) -> tuple:
    """Managers from the most recent holdings date, grouped by the plan's own asset-class label."""
    if not mandates:
        return None, []
    from collections import Counter
    from datetime import timedelta

    # The latest full holdings table: among dates within ~9 months of the newest, the one with the most rows.
    newest = max(m["as_of"] for m in mandates)
    counts = Counter(m["as_of"] for m in mandates if m["as_of"] >= newest - timedelta(days=270))
    as_of = max(counts, key=lambda d: (counts[d], d))
    groups: dict[str, list[dict]] = {}
    seen = set()
    for m in mandates:
        if m["as_of"] != as_of:
            continue
        k = (m["bucket_label_as_disclosed"], m["manager_name_as_disclosed"].lower())
        if k in seen:
            continue
        seen.add(k)
        groups.setdefault(m["bucket_label_as_disclosed"], []).append(m)
    return as_of, sorted(groups.items())
