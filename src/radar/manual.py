"""Touchpoints 4 and 5: manual facts, public-records requests, and the manual inbox.

Manual facts go through the same lint, contact detector, and review queue as machine
signals. The system never sends a records request; it only tracks what a person filed
and ingests the response files a person drops into the request's folder.
"""

from __future__ import annotations

import mimetypes
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from radar import db, ingest
from radar.models import ManualFact, new_id
from radar.net.client import host_verdict
from radar.parse import pdf
from radar.settings import get_settings, sources_config
from radar.wall.lint import lint
from radar.wall.scrubber import detect

RECORD_STATUSES = ["filed", "acknowledged", "fulfilled", "partially fulfilled", "denied", "withdrawn"]


class ManualError(ValueError):
    pass


def manual_sources() -> list[dict]:
    return [s for s in sources_config()["verdicts"] if s["verdict"] == "MANUAL"]


def add_fact(f: ManualFact, actor: str) -> str:
    host = urlsplit(f.source_url).hostname or ""
    v = host_verdict(host)
    if v and v["verdict"] == "HARD_REJECT":
        raise ManualError(f"{host} is on the hard-reject list (licensed or login-walled); it cannot be cited")
    if detect(f.source_url) and any(h.kind == "social_url" for h in detect(f.source_url)):
        raise ManualError("person profile URLs cannot be cited")
    lr = lint(f.fact)
    if not lr.ok:
        raise ManualError(f"use factual wording; remove: {', '.join(lr.banned_found)}")
    if detect(f.fact):
        raise ManualError("the fact contains contact-shaped text (email, phone, address); remove it")
    if f.event_date > date.today():
        raise ManualError("event date is in the future")
    with db.connect() as con:
        if not con.execute("SELECT 1 FROM allocators WHERE allocator_id = ?", [f.allocator_id]).fetchone():
            raise ManualError("unknown allocator")
        eid, sid = new_id(), new_id()
        con.execute("BEGIN")
        con.execute("INSERT INTO evidence VALUES (?, NULL, ?, NULL, ?, 'manual-entry', '1', NULL, now(), 1.0, 'MANUAL')",
                    [eid, f.source_url, f.fact])
        from radar.buckets import hf_relevant
        con.execute("INSERT INTO signals (signal_id, allocator_id, signal_type, qualifier, event_date, observed_at, summary, "
                    "review_status, origin, review_note, hf_relevant) VALUES (?, ?, ?, ?, ?, now(), ?, 'new', 'MANUAL', ?, ?)",
                    [sid, f.allocator_id, f.signal_type.value, f.qualifier or None, f.event_date, f.fact,
                     f"source {f.source_id}; entered by {actor}", hf_relevant(None, f.fact)])
        con.execute("INSERT INTO row_evidence VALUES ('signal', ?, ?)", [sid, eid])
        db.audit(con, actor, "manual_fact", sid, detail={"source_id": f.source_id, "url": f.source_url})
        con.execute("COMMIT")
    from radar.review import decide, policy_actor

    if (auto := policy_actor("signals")):
        decide(sid, "approved", auto, note="auto-approved under owner policy")
    return sid


# ---------------- records requests ----------------
def records_dir(request_id: str) -> Path:
    return get_settings().manual_inbox / "records" / request_id


def add_request(allocator_id: str, request_text: str, date_filed: date, actor: str, note: str | None = None) -> str:
    if detect(request_text) or (note and detect(note)):
        raise ManualError("remove contact details (yours or anyone's) from the request text; the tracker stores none")
    if date_filed > date.today():
        raise ManualError("date filed is in the future")
    rid = new_id()[:8]
    folder = records_dir(rid)
    folder.mkdir(parents=True, exist_ok=True)
    with db.connect() as con:
        con.execute("INSERT INTO records_requests VALUES (?, ?, ?, ?, 'filed', ?, now(), ?)",
                    [rid, allocator_id, request_text[:4000], date_filed,
                     str(folder.relative_to(get_settings().manual_inbox.parent.parent)), note])
        db.audit(con, actor, "records_request_add", rid, detail={"allocator_id": allocator_id})
    return rid


def update_request(request_id: str, status: str, actor: str, note: str | None = None) -> None:
    if status not in RECORD_STATUSES:
        raise ManualError("unknown status")
    if note and detect(note):
        raise ManualError("remove contact details from the note")
    with db.connect() as con:
        con.execute("UPDATE records_requests SET status = ?, note = coalesce(?, note), updated_at = now() WHERE request_id = ?",
                    [status, note, request_id])
        db.audit(con, actor, "records_request_status", request_id, detail={"status": status})


# ---------------- inbox ingestion ----------------
def _ingest_folder(folder: Path, allocator_id: str, provenance: str, actor: str) -> dict:
    stats = {"files": 0, "new": 0}
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith(".") or path.name.lower() == "readme.md":
            continue
        data = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        url = f"manual://{folder.name}/{path.name}"
        sha, new = ingest.store_document(data, url=url, allocator_id=allocator_id, content_type=ctype,
                                         provenance=provenance, title=path.stem.replace("_", " ")[:200])
        pdf.parse_document(sha)
        stats["files"] += 1
        stats["new"] += int(new)
    with db.connect() as con:
        db.audit(con, actor, "inbox_ingest", allocator_id, detail={**stats, "folder": folder.name, "provenance": provenance})
    return stats


def ingest_request(request_id: str, actor: str) -> dict:
    with db.connect() as con:
        r = con.execute("SELECT allocator_id FROM records_requests WHERE request_id = ?", [request_id]).fetchone()
    if not r:
        raise ManualError("unknown request")
    return _ingest_folder(records_dir(request_id), r[0], "RECORDS_REQUEST", actor)


def ingest_inbox(actor: str) -> dict:
    """inbox/manual/<allocator_id>/... -> MANUAL provenance documents."""
    root = get_settings().manual_inbox
    out = {}
    with db.connect() as con:
        known = {a for (a,) in con.execute("SELECT allocator_id FROM allocators").fetchall()}
    for d in sorted(p for p in root.iterdir() if p.is_dir() and p.name != "records"):
        if d.name in known:
            out[d.name] = _ingest_folder(d, d.name, "MANUAL", actor)
    return out
