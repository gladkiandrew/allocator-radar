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


def signals_for(con, allocator_ids: list[str] | None = None, statuses: tuple[str, ...] = ("new", "approved")) -> list[dict]:
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


def allocator_table(con) -> list[dict]:
    allocs = db.rows(con, "SELECT * FROM allocators ORDER BY display_name")
    docs = {
        r["allocator_id"]: r
        for r in db.rows(
            con,
            """SELECT allocator_id, count(DISTINCT sha256) AS n_docs, max(first_seen) AS last_new_document,
                      max(last_fetched) AS last_fetched, max(doc_date) AS newest_doc_date
               FROM document_urls GROUP BY allocator_id""",
        )
    }
    consultants = {
        r["allocator_id"]: r
        for r in db.rows(
            con,
            """SELECT allocator_id, arg_max(consultant_name_as_disclosed, as_of) AS consultant, max(as_of) AS as_of
               FROM consultant_engagements GROUP BY allocator_id""",
        )
    }
    from radar.resolve import canonical_map

    cmap = canonical_map(con, "consultant")
    for c in consultants.values():
        c["consultant"] = cmap.get(c["consultant"], c["consultant"])
    sigs_by: dict[str, list[dict]] = defaultdict(list)
    for s in signals_for(con):
        sigs_by[s["allocator_id"]].append(s)
    out = []
    for a in allocs:
        d = docs.get(a["allocator_id"], {})
        sigs = sigs_by.get(a["allocator_id"], [])
        r = compute(sigs, d.get("last_new_document"))
        a.update(
            readiness=r,
            n_docs=d.get("n_docs", 0),
            last_fetched=d.get("last_fetched"),
            newest_doc_date=d.get("newest_doc_date"),
            consultant=consultants.get(a["allocator_id"]),
            pending=[s for s in sigs if s["review_status"] == "new"],
        )
        out.append(a)
    out.sort(key=lambda a: (TIER_ORDER.index(a["readiness"].tier),
                            -(a["readiness"].reasons[0]["event_date"].toordinal() if a["readiness"].reasons else 0),
                            a["display_name"]))
    return out


def pending_count(con) -> int:
    return con.execute("SELECT count(*) FROM signals WHERE review_status = 'new'").fetchone()[0]


def allocator_brief(con, allocator_id: str) -> dict | None:
    rs = db.rows(con, "SELECT * FROM allocators WHERE allocator_id = ?", [allocator_id])
    if not rs:
        return None
    a = rs[0]
    a["pages"] = db.rows(con, "SELECT * FROM allocator_pages WHERE allocator_id = ? ORDER BY url", [allocator_id])
    a["documents"] = db.rows(
        con,
        """SELECT du.*, d.page_count, d.parse_error, d.provenance,
                  (SELECT count(*) FROM pages p WHERE p.sha256 = du.sha256 AND p.prefilter_terms IS NOT NULL) AS hit_pages
           FROM document_urls du JOIN documents d USING (sha256)
           WHERE du.allocator_id = ? ORDER BY du.doc_date DESC NULLS LAST, du.first_seen DESC""",
        [allocator_id],
    )
    sigs = signals_for(con, [allocator_id], statuses=("new", "approved", "rejected"))
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
    return a
