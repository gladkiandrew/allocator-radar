"""Touchpoint 3: signal review. The only path by which a signal becomes 'approved'."""

from __future__ import annotations

from datetime import date

from radar import db
from radar.models import SignalType
from radar.wall.lint import lint
from radar.wall.scrubber import detect


class ReviewError(ValueError):
    pass


def decide(signal_id: str, decision: str, actor: str, *, note: str | None = None, signal_type: str | None = None,
           qualifier: str | None = None, event_date: str | None = None, summary: str | None = None) -> dict:
    if decision not in ("approved", "rejected", "new"):
        raise ReviewError("decision must be approved, rejected, or new")
    with db.connect() as con:
        rs = db.rows(con, "SELECT * FROM signals WHERE signal_id = ?", [signal_id])
        if not rs:
            raise ReviewError("unknown signal")
        before = rs[0]
        upd = {}
        if signal_type and signal_type != before["signal_type"]:
            if signal_type not in SignalType.__members__:
                raise ReviewError("unknown signal type")
            upd["signal_type"] = signal_type
        if qualifier is not None and (qualifier or None) != before["qualifier"]:
            upd["qualifier"] = qualifier or None
        if event_date and event_date != str(before["event_date"]):
            d = date.fromisoformat(event_date)
            if d > date.today():
                raise ReviewError("event date is in the future")
            upd["event_date"] = d
        if summary is not None and summary.strip() != before["summary"]:
            s = summary.strip()[:300]
            lr = lint(s)
            if not lr.ok:
                raise ReviewError(f"summary uses evaluative language: {', '.join(lr.banned_found)}")
            if detect(s):
                raise ReviewError("summary contains contact-shaped text")
            upd["summary"] = s
        if decision == "approved" and not con.execute(
                "SELECT count(*) FROM row_evidence WHERE row_kind = 'signal' AND row_id = ?", [signal_id]).fetchone()[0]:
            raise ReviewError("cannot approve a signal without evidence")
        sets = ", ".join(f"{k} = ?" for k in upd)
        con.execute(
            f"UPDATE signals SET {sets + ', ' if sets else ''}review_status = ?, reviewed_by = ?, reviewed_at = now(), review_note = ? "
            "WHERE signal_id = ?",
            [*upd.values(), decision, actor, note or None, signal_id],
        )
        db.audit(con, actor, f"signal_{decision}", signal_id,
                 detail={"changed": {k: str(v) for k, v in upd.items()}, "note": note,
                         "before": {k: str(before[k]) for k in ("signal_type", "qualifier", "event_date", "review_status")}})
        return db.rows(con, "SELECT * FROM signals WHERE signal_id = ?", [signal_id])[0]


def policy_actor(kind: str = "signals") -> str | None:
    """The auto-approve actor string if the owner's policy auto-approves this kind, else None."""
    from radar.settings import review_policy

    pol = (review_policy().get(kind) or {})
    return pol.get("actor") if pol.get("auto_approve") else None


def approve_pending() -> int:
    """Apply the owner's auto-approve policy to every pending signal that has evidence."""
    actor = policy_actor("signals")
    if not actor:
        return 0
    with db.connect() as con:
        ids = [r[0] for r in con.execute(
            """SELECT signal_id FROM signals s WHERE review_status = 'new'
               AND EXISTS (SELECT 1 FROM row_evidence re WHERE re.row_kind = 'signal' AND re.row_id = s.signal_id)""").fetchall()]
    for sid in ids:
        decide(sid, "approved", actor, note="auto-approved under owner policy")
    return len(ids)


def integrity_cleanup() -> list[str]:
    """Remove machine/policy-approved signals whose evidence no longer belongs to their plan.

    A signal is orphaned when none of its crawled evidence documents is filed under the signal's allocator
    (for example after a document was unfiled from the wrong plan). Human decisions are never touched:
    only rows that are unreviewed or were approved by the owner's policy actor are removed.
    """
    actor = policy_actor("signals")
    removed = []
    with db.connect() as con:
        rows = db.rows(con, """
            SELECT s.signal_id, s.allocator_id, s.summary, s.review_status, s.reviewed_by
            FROM signals s
            WHERE s.origin = 'MACHINE'
              AND (s.review_status = 'new' OR (s.review_status = 'approved' AND s.reviewed_by = ?))
              AND NOT EXISTS (
                SELECT 1 FROM row_evidence re JOIN evidence e USING (evidence_id)
                JOIN document_urls du ON du.sha256 = e.document_sha256 AND du.allocator_id = s.allocator_id
                WHERE re.row_kind = 'signal' AND re.row_id = s.signal_id)""", [actor or "\x00"])
        for r in rows:
            con.execute("DELETE FROM row_evidence WHERE row_kind = 'signal' AND row_id = ?", [r["signal_id"]])
            con.execute("DELETE FROM signals WHERE signal_id = ?", [r["signal_id"]])
            db.audit(con, "integrity", "signal_removed_orphan", r["signal_id"],
                     detail={"allocator_id": r["allocator_id"], "summary": r["summary"][:200], "was": r["review_status"]})
            removed.append(r["summary"][:90])
        # same rule for holdings / officers / consultant rows (no review state; machine-only)
        for kind, table, key in (("mandate", "mandates", "mandate_id"), ("officer", "officers", "officer_id"),
                                 ("engagement", "consultant_engagements", "engagement_id")):
            con.execute(f"""DELETE FROM {table} t WHERE NOT EXISTS (
                SELECT 1 FROM row_evidence re JOIN evidence e USING (evidence_id)
                JOIN document_urls du ON du.sha256 = e.document_sha256 AND du.allocator_id = t.allocator_id
                WHERE re.row_kind = '{kind}' AND re.row_id = t.{key})""")
    return removed
