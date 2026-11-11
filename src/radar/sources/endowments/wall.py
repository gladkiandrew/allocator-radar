"""THE WALL for the E&F store (data/ef.duckdb): the same schema and sweep checks as the main store.

  schema: no column name matches the banned pattern (email, phone, address ...)
  sweep:  no stored text value holds a contact-shaped span; URL columns are checked for email/mailto/social only
"""

from __future__ import annotations

import re

from radar.settings import wall_config
from radar.sources.endowments import store
from radar.wall.scrubber import detect

URL_COLUMNS = {("ef_signals", "source_url"), ("ef_documents", "url"), ("ef_orgs", "home_url")}


def check() -> list[str]:
    if not store.path().exists():
        return []
    pat = re.compile(wall_config()["banned_column_pattern"], re.I)
    out = []
    with store.connect(read_only=True) as con:
        cols = con.execute("SELECT table_name, column_name, data_type FROM information_schema.columns").fetchall()
        out += [f"ef schema: banned column {t}.{c}" for t, c, _ in cols if pat.search(c)]
        for table, col, dtype in cols:
            if dtype != "VARCHAR":
                continue
            for (val,) in con.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL').fetchall():
                hits = detect(val)
                from radar.wall.scrubber import CONTACT_KINDS, contacts_allowed
                if contacts_allowed():  # D-B35
                    hits = [h for h in hits if h.kind not in CONTACT_KINDS]
                if (table, col) in URL_COLUMNS:
                    hits = [h for h in hits if h.kind in ("email", "mailto", "social_url")]
                if hits:
                    out.append(f"ef sweep: {table}.{col} holds {hits[0].kind}: {hits[0].text[:40]!r}")
    return out
