"""Entity resolution for firm names as printed (consultants and managers). AGENDA §4.6, light v0.

Ladder: normalized exact -> fuzzy score >= 0.95 auto-alias -> otherwise kept as a separate name.
There is no manual merge queue: anything below the auto-alias bar simply stays distinct (D-B11).
Merges are alias edges, never destructive: the printed name is always kept on the row; only the
display/grouping uses the canonical name. Hard negatives (e.g. "Millennium Management" vs
"Millennium Partners") are protected by requiring the distinguishing tokens to match.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from radar import db

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


CURATED_FILE = "consultant_aliases.yaml"


def load_curated(con) -> int:
    """Curated consultant aliases (data/consultant_aliases.yaml, D-B18). They override auto aliases."""
    import yaml

    from radar.settings import REPO_ROOT

    path = REPO_ROOT / "data" / CURATED_FILE
    if not path.exists():
        return 0
    cfg = yaml.safe_load(path.read_text()) or {}
    n = 0
    for canon, aliases in (cfg.get("canonical") or {}).items():
        for alias in aliases or []:
            con.execute("INSERT OR REPLACE INTO entity_aliases VALUES ('consultant', ?, ?, 'curated', 1.0, 'owner-file', now())",
                        [alias.strip(), canon])
            n += 1
    return n


def run() -> dict:
    stats = {"auto": 0}
    with db.connect() as con:
        con.execute(DDL)
        stats["curated"] = load_curated(con)
        for kind in ("consultant", "manager"):
            names = _names(con, kind)
            known = dict(con.execute("SELECT alias, canonical FROM entity_aliases WHERE kind = ?", [kind]).fetchall())
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
                target = known.get(best, best) if best else None
                if best and best_s >= 0.95 and target != n:  # never alias two names to each other (D-B18)
                    con.execute("INSERT INTO entity_aliases VALUES (?, ?, ?, 'auto', ?, 'resolver', now()) ON CONFLICT DO NOTHING",
                                [kind, n, target, best_s])
                    known[n] = target
                    stats["auto"] += 1
        # clean up cycles left by earlier runs: drop an auto alias whose canonical is itself aliased back to it
        con.execute("""DELETE FROM entity_aliases a WHERE a.method = 'auto' AND EXISTS (
                           SELECT 1 FROM entity_aliases b WHERE b.kind = a.kind AND b.alias = a.canonical
                             AND b.canonical = a.alias AND b.alias < a.alias)""")
    return stats


def canonical_map(con, kind: str) -> dict[str, str]:
    con.execute(DDL)
    return dict(con.execute("SELECT alias, canonical FROM entity_aliases WHERE kind = ?", [kind]).fetchall())


def not_consultant(name: str) -> bool:
    """A name on the curated not_consultant list (data/consultant_aliases.yaml): a deal's own manager or servicer
    printed in a per-deal consultant column (D-B31)."""
    import functools

    import yaml

    from radar.settings import REPO_ROOT

    @functools.lru_cache(maxsize=1)
    def _names() -> frozenset[str]:
        path = REPO_ROOT / "data" / CURATED_FILE
        cfg = yaml.safe_load(path.read_text()) if path.exists() else {}
        return frozenset(n.strip().lower() for n in (cfg or {}).get("not_consultant") or [])

    return (name or "").strip().lower() in _names()
