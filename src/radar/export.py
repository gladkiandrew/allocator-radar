"""CSV export. APPROVED signals only; every row carries its evidence URL and page.

The export log is append-only (sha256 of the file, row count, actor, time).
"""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import date

from radar import db, queries
from radar.models import new_id
from radar.settings import firm_posture

DISCLAIMERS = {
    "base": ("Facts compiled from public records (Florida Chapter 119 public records and plan websites). "
             "Each row cites its source document and page. No contact information is included."),
    "ria": ("Internal research only. Not an advertisement under SEC Rule 206(4)-1. Do not distribute externally "
            "without compliance review."),
    "cpo_cta": ("Internal research only. Not promotional material under NFA Compliance Rule 2-29. Do not "
                "distribute externally without compliance review."),
}


class ExportRefused(RuntimeError):
    pass


def disclaimer_lines(posture: str | None = None) -> list[str]:
    posture = posture or firm_posture()
    lines = [DISCLAIMERS["base"]]
    if posture in ("ria", "both"):
        lines.append(DISCLAIMERS["ria"])
    if posture in ("cpo_cta", "both"):
        lines.append(DISCLAIMERS["cpo_cta"])
    return lines


COLUMNS = ["allocator", "allocator_type", "city", "state", "readiness_tier", "signal_type", "qualifier",
           "event_date", "summary", "source_url", "page", "document_sha256", "origin", "reviewed_by", "reviewed_at"]


def build_csv(actor: str) -> tuple[str, int]:
    with db.connect() as con:
        table = {a["allocator_id"]: a for a in queries.allocator_table(con)}
        sigs = queries.signals_for(con, statuses=("approved",))
        if any(s["review_status"] != "approved" for s in sigs):  # belt and braces
            raise ExportRefused("unapproved row reached the exporter")
        buf = io.StringIO()
        for line in disclaimer_lines():
            buf.write(f"# {line}\n")
        buf.write(f"# Exported {date.today().isoformat()} by {actor}. Approved signals only.\n")
        w = csv.writer(buf)
        w.writerow(COLUMNS)
        n = 0
        for s in sigs:
            a = table.get(s["allocator_id"], {})
            evs = s["evidence"] or [{}]
            for e in evs:
                w.writerow([a.get("display_name"), a.get("allocator_type"), a.get("jurisdiction_city"),
                            a.get("jurisdiction_state"), a["readiness"].tier if a else "", s["signal_type"],
                            s["qualifier"] or "", s["event_date"], s["summary"], e.get("source_url", ""),
                            e.get("page") or "", e.get("document_sha256") or "", s["origin"], s["reviewed_by"],
                            s["reviewed_at"]])
                n += 1
        text = buf.getvalue()
        con.execute("INSERT INTO export_log VALUES (?, now(), ?, ?, ?, NULL)",
                    [new_id(), actor, n, hashlib.sha256(text.encode()).hexdigest()])
        db.audit(con, actor, "export_csv", None, detail={"rows": n})
    return text, n
