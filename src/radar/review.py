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
