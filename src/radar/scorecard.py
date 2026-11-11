"""Data quality scorecard for the How it works page, computed when the page is built (read-only).

Four figures, real numbers only:
  1. coverage:  pension plans in scope (and how many meet the main-table bar), plus E&F organizations
  2. freshness: median age in days of each pension plan's newest document dated today or earlier
                (a future-dated agenda never counts), and the same for E&F's latest 990 period end
  3. review:    share of public signals (approved + auto-approved) that a person approved
  4. accuracy:  the latest spot check DATA recorded in HANDOFF.md ("SPOT-CHECK <date>: <n> signals checked,
                <k> correct, ..."); None when none is recorded. Nothing is estimated.
"""

from __future__ import annotations

import re
import statistics
from datetime import date
from pathlib import Path

from radar.settings import REPO_ROOT

SPOT = re.compile(r"SPOT-CHECK\s+(\d{4}-\d{2}-\d{2})\s*:\s*(\d+)\s+signals?\s+checked\s*,\s*(\d+)\s+correct", re.I)


def handoff_path() -> Path:
    """HANDOFF.md in the main checkout. From a git worktree, follow .git ("gitdir: <main>/.git/worktrees/<name>")."""
    import os

    if os.environ.get("RADAR_HANDOFF_PATH"):
        return Path(os.environ["RADAR_HANDOFF_PATH"])
    git = REPO_ROOT / ".git"
    if git.is_file():
        m = re.match(r"gitdir:\s*(.+)", git.read_text().strip())
        if m and "/.git/worktrees/" in m.group(1):
            return Path(m.group(1).split("/.git/worktrees/")[0]) / "HANDOFF.md"
    return REPO_ROOT / "HANDOFF.md"


def latest_spot_check(text: str) -> dict | None:
    """The last SPOT-CHECK line by date (ties: the later line). None if there is none."""
    best = None
    for m in SPOT.finditer(text or ""):
        d, n, k = m.group(1), int(m.group(2)), int(m.group(3))
        if n <= 0 or k > n:
            continue
        if best is None or d >= best["date"]:
            best = {"date": d, "checked": n, "correct": k, "pct": round(100 * k / n, 1)}
    return best


def _median_age(dates: list[date], today: date) -> int | None:
    ages = [(today - d).days for d in dates if d and d <= today]
    return int(statistics.median(ages)) if ages else None


def compute(today: date | None = None) -> dict:
    from radar import db, queries
    from radar.sources.endowments import store

    today = today or date.today()
    with db.connect() as con:
        plans = queries.allocator_table(con)  # scoped; newest_doc_date already excludes future-dated documents
        ids = [p["allocator_id"] for p in plans]
        ph = ",".join("?" * len(ids)) or "''"
        reviewed, public = con.execute(
            f"""SELECT count(*) FILTER (WHERE review_status = 'approved'), count(*) FROM signals
                WHERE review_status IN ('approved', 'auto_approved') AND allocator_id IN ({ph})""", ids).fetchone()
    with store.connect(read_only=True) as con:
        n_orgs = con.execute("SELECT count(*) FROM ef_orgs").fetchone()[0]
        ef_rev, ef_pub = con.execute("""SELECT count(*) FILTER (WHERE review_status = 'approved'), count(*)
                                        FROM ef_signals WHERE review_status IN ('approved', 'auto_approved')""").fetchone()
        ef_latest = [r[0] for r in con.execute("SELECT max(tax_period_end) FROM ef_filings GROUP BY ein").fetchall()]
    ef_latest = [d if isinstance(d, date) else date.fromisoformat(str(d)) for d in ef_latest if d]
    p = handoff_path()
    spot = latest_spot_check(p.read_text()) if p.exists() else None
    total_rev, total_pub = reviewed + ef_rev, public + ef_pub
    return {
        "as_of": today.isoformat(),
        "plans": len(plans),
        "plans_main": sum(1 for x in plans if not x["bar_reasons"]),
        "orgs": n_orgs,
        "median_age_days": _median_age([x["newest_doc_date"] for x in plans], today),
        "plans_dated": sum(1 for x in plans if x["newest_doc_date"]),
        "ef_median_age_days": _median_age(ef_latest, today),
        "reviewed": total_rev,
        "public_signals": total_pub,
        "reviewed_pct": round(100 * total_rev / total_pub, 1) if total_pub else None,
        "spot": spot,
    }
