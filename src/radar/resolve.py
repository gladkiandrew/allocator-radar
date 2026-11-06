"""Entity resolution for firm names as printed (consultants and managers). AGENDA §4.6, light v0.

Ladder: normalized exact -> fuzzy score >= 0.95 auto-alias -> 0.80-0.95 merge queue (Touchpoint 2) -> new.
Merges are alias edges, never destructive: the printed name is always kept on the row; only the
display/grouping uses the canonical name. Hard negatives (e.g. "Millennium Management" vs
"Millennium Partners") are protected by requiring the distinguishing tokens to match.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from radar import db
from radar.models import new_id

SUFFIXES = r"\b(llc|l\.l\.c|lp|l\.p|ltd|inc|incorporated|corp|corporation|co|company|group|holdings|the)\b"
GENERIC = {"capital", "management", "investment", "investments", "advisors", "advisers", "partners", "asset",
           "associates", "fund", "funds", "global", "institutional", "consulting", "llc", "lp"}

DDL = """CREATE TABLE IF NOT EXISTS entity_aliases (
    kind VARCHAR NOT NULL, alias VARCHAR NOT NULL, canonical VARCHAR NOT NULL, method VARCHAR NOT NULL,
    score DOUBLE, decided_by VARCHAR, decided_at TIMESTAMPTZ, PRIMARY KEY (kind, alias))"""


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKC", name).casefold().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(SUFFIXES, " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _distinct(n: str) -> set[str]:
    return {t for t in n.split() if t not in GENERIC and len(t) > 1}


def score(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return 1.0
    da, db_ = _distinct(na), _distinct(nb)
    if not da or not db_ or not (da & db_):
        return 0.0  # no shared distinguishing token: never the same firm
    ga, gb = set(na.split()) & GENERIC, set(nb.split()) & GENERIC
    if ga and gb and not (ga <= gb or gb <= ga):
        return 0.7  # same name, different firm-type word ("Management" vs "Partners"): never auto, never queued high
    seq = SequenceMatcher(None, na, nb).ratio()
    overlap = len(da & db_) / len(da | db_)
    contained = 1.0 if (da <= db_ or db_ <= da) else 0.0
    return round(max(seq, 0.5 * overlap + 0.5 * contained), 3)


def _names(con, kind: str) -> list[str]:
    if kind == "consultant":
        q = "SELECT DISTINCT consultant_name_as_disclosed FROM consultant_engagements"
    else:
        q = "SELECT DISTINCT manager_name_as_disclosed FROM mandates"
    return sorted({r[0].strip() for r in con.execute(q).fetchall() if r[0] and r[0].strip()})


def run() -> dict:
    stats = {"auto": 0, "queued": 0}
    with db.connect() as con:
        con.execute(DDL)
        for kind in ("consultant", "manager"):
            names = _names(con, kind)
            known = dict(con.execute("SELECT alias, canonical FROM entity_aliases WHERE kind = ?", [kind]).fetchall())
            queued = {(l, r) for l, r in con.execute(
                "SELECT left_name, right_name FROM merge_queue WHERE entity_kind = ?", [kind]).fetchall()}
            canon: list[str] = sorted({known.get(n, n) for n in names}, key=len)
            for n in names:
                if n in known:
                    continue
                best, best_s = None, 0.0
                for c in canon:
                    if c == n:
                        continue
                    s = score(n, c)
                    if s > best_s:
                        best, best_s = c, s
                if best and best_s >= 0.95:
                    con.execute("INSERT INTO entity_aliases VALUES (?, ?, ?, 'auto', ?, 'resolver', now()) ON CONFLICT DO NOTHING",
                                [kind, n, known.get(best, best), best_s])
                    stats["auto"] += 1
                elif best and best_s >= 0.80 and (n, best) not in queued and (best, n) not in queued:
                    con.execute("INSERT INTO merge_queue VALUES (?, ?, ?, ?, ?, NULL, 'open', NULL, NULL)",
                                [new_id(), kind, n, best, best_s])
                    stats["queued"] += 1
    return stats


def decide(merge_id: str, same: bool, actor: str) -> None:
    with db.connect() as con:
        con.execute(DDL)
        r = con.execute("SELECT entity_kind, left_name, right_name, score FROM merge_queue WHERE merge_id = ?", [merge_id]).fetchone()
        if not r:
            raise ValueError("unknown merge")
        kind, left, right, s = r
        if same:
            canonical = min((left, right), key=len)
            alias = right if canonical == left else left
            con.execute("INSERT INTO entity_aliases VALUES (?, ?, ?, 'reviewed', ?, ?, now()) ON CONFLICT DO NOTHING",
                        [kind, alias, canonical, s, actor])
        con.execute("UPDATE merge_queue SET status = ?, decided_by = ?, decided_at = now() WHERE merge_id = ?",
                    ["merged" if same else "distinct", actor, merge_id])
        db.audit(con, actor, "merge_decision", merge_id, detail={"same": same, "left": left, "right": right})


def canonical_map(con, kind: str) -> dict[str, str]:
    con.execute(DDL)
    return dict(con.execute("SELECT alias, canonical FROM entity_aliases WHERE kind = ?", [kind]).fetchall())
