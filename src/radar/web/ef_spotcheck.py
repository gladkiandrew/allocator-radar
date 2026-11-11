"""E&F spot-check (local admin only): the owner checks a sample of E&F signals against their sources.

Input: data/ef_spotcheck_25.md, one line per signal, `- [ ] <ef_signal_id> | <org> | <summary>` (written by the
DATA session). Decisions never touch the database: each one is appended as a JSON line to
data/ef_spotcheck_25.decisions.jsonl under an exclusive file lock. The latest line per id wins. The E&F store is
only ever opened read-only.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

from radar.settings import get_settings

DECISIONS = ("approved", "rejected")
MAX_NOTE = 1000
_ITEM = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*(?P<rest>.*)$")
_TOKEN = re.compile(r"[A-Za-z0-9_-]+")


class SpotcheckError(Exception):
    pass


def md_path() -> Path:
    return get_settings().data_dir / "ef_spotcheck_25.md"


def decisions_path() -> Path:
    return get_settings().data_dir / "ef_spotcheck_25.decisions.jsonl"


# ---------------- input ----------------
def parse_md(text: str, known_ids: set[str]) -> list[str]:
    """Signal ids in file order: on each `- [ ]` / `- [x]` line, the first token that is a known ef_signals id.

    Other lines, lines without a known id, and repeats are ignored."""
    out: list[str] = []
    for line in text.splitlines():
        m = _ITEM.match(line)
        if not m:
            continue
        for tok in _TOKEN.findall(m.group("rest")):
            if tok in known_ids:
                if tok not in out:
                    out.append(tok)
                break
    return out


def load_signals() -> dict[str, dict]:
    """Every E&F signal with its organization, from a read-only connection to the E&F store."""
    from radar.sources.endowments import store

    with store.connect(read_only=True) as con:
        rows = store.rows(con, """SELECT s.*, o.name AS org_name, o.kind AS org_kind
                                  FROM ef_signals s LEFT JOIN ef_orgs o USING (ein)""")
    return {r["signal_id"]: r for r in rows}


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9$]+", " ", (t or "").lower()).split())


def parse_table(text: str, signals: dict[str, dict]) -> list[str]:
    """DATA's table form: | # | Organization | Type | Date | Summary | Cited form / line | Source URL | Correct? |.
    Each row is matched to the one signal with the same type and date whose summary starts the same way."""
    by_key: dict[tuple, list[dict]] = {}
    for sid, sig in signals.items():
        by_key.setdefault((str(sig.get("signal_type")), str(sig.get("event_date"))), []).append(sig)
    out: list[str] = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        cands = by_key.get((cells[2], cells[3]), [])
        head = _norm(cells[4])[:60]
        hit = [c for c in cands if _norm(c.get("summary"))[:60] == head] or \
              [c for c in cands if _norm(c.get("summary")).startswith(head[:30])]
        if len(hit) > 1:  # the same firm and summary at two organizations: match the organization name
            org = _norm(cells[1].split("(")[0])
            hit = [c for c in hit if _norm(c.get("org_name")).startswith(org[:20]) or org.startswith(_norm(c.get("org_name"))[:20])]
        if len(hit) == 1 and hit[0]["signal_id"] not in out:
            out.append(hit[0]["signal_id"])
    return out


def sample(signals: dict[str, dict]) -> tuple[list[str], bool]:
    """(ids listed in the md that exist in the store, whether the md file exists). Accepts the agreed checklist
    form (`- [ ] <id> | ...`) or DATA's table form."""
    p = md_path()
    if not p.exists():
        return [], False
    text = p.read_text(encoding="utf-8", errors="replace")
    return (parse_md(text, set(signals)) or parse_table(text, signals)), True


# ---------------- decisions ----------------
def read_decisions(path: Path | None = None) -> dict[str, dict]:
    """Latest decision per id (later lines win). Unreadable lines are ignored."""
    path = path or decisions_path()
    latest: dict[str, dict] = {}
    if not path.exists():
        return latest
    with open(path, encoding="utf-8", errors="replace") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
        try:
            lines = fh.read().splitlines()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    for ln in lines:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get("id"), str) and d.get("decision") in DECISIONS:
            latest[d["id"]] = d
    return latest


def append_decision(signal_id: str, decision: str, by: str, note: str = "", path: Path | None = None) -> dict:
    if decision not in DECISIONS:
        raise SpotcheckError("decision must be approve or reject")
    path = path or decisions_path()
    rec = {"id": signal_id, "decision": decision, "by": by,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "note": (note or "").strip()[:MAX_NOTE]}
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            size = os.fstat(fd).st_size
            if size:  # a torn last line from someone else must not swallow ours
                with open(path, "rb") as rf:
                    rf.seek(size - 1)
                    if rf.read(1) != b"\n":
                        line = b"\n" + line
            os.write(fd, line)
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    return rec


# ---------------- summary ----------------
def fmt_pct(k: int, n: int) -> str:
    if not n:
        return "0"
    return f"{100 * k / n:.1f}".rstrip("0").rstrip(".")


def handoff_line(n: int, k: int, today: date | None = None) -> str:
    today = today or date.today()
    return (f"SPOT-CHECK {today.isoformat()}: {n} signals checked, {k} correct, accuracy {fmt_pct(k, n)}%, "
            f"method owner review of data/ef_spotcheck_25.md in /review/ef")


def state(ids: list[str], decisions: dict[str, dict]) -> dict:
    decided = {i: decisions[i] for i in ids if i in decisions}
    approved = sum(1 for d in decided.values() if d["decision"] == "approved")
    n = len(decided)
    return {"total": len(ids), "decided": n, "approved": approved, "rejected": n - approved,
            "undecided": [i for i in ids if i not in decided], "accuracy": fmt_pct(approved, n),
            "handoff": handoff_line(n, approved)}
