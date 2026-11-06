"""Touchpoint 1: registry approval. Loads an approved registry into the store.

The approval is bound to the registry file's sha256. If the file changes, the crawler
refuses to run until the new version is approved.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from radar import db
from radar.registry.discovery import version
from radar.settings import get_settings


def registry_path(region: str = "florida") -> Path:
    return get_settings().registry_dir / f"{region}.yaml"


def load(region: str = "florida") -> tuple[dict | None, str | None]:
    p = registry_path(region)
    if not p.exists():
        return None, None
    return yaml.safe_load(p.read_text()), version(p)


def approval(con, region: str = "florida") -> dict | None:
    rs = db.rows(con, "SELECT * FROM registry_approvals WHERE region = ? ORDER BY approved_at DESC LIMIT 1", [region])
    return rs[0] if rs else None


def is_current_approved(con, region: str = "florida") -> bool:
    _, v = load(region)
    a = approval(con, region)
    return bool(v and a and a["registry_version"] == v)


def approve(actor: str, expected_version: str, selected_pages: set[tuple[str, str]] | None,
            region: str = "florida", note: str | None = None) -> int:
    reg, v = load(region)
    if reg is None:
        raise ValueError("no registry file")
    if v != expected_version:
        raise ValueError("registry file changed since it was displayed; reload and review again")
    n = 0
    with db.connect() as con:
        con.execute("BEGIN")
        for a in reg["allocators"]:
            con.execute(
                """INSERT INTO allocators (allocator_id, legal_name, display_name, allocator_type, jurisdiction_city,
                       jurisdiction_state, jurisdiction_country, home_url, template, access, access_reason, source_id,
                       registry_version, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'US', ?, ?, ?, ?, ?, ?, now())
                   ON CONFLICT (allocator_id) DO UPDATE SET legal_name = excluded.legal_name,
                       display_name = excluded.display_name, allocator_type = excluded.allocator_type,
                       jurisdiction_city = excluded.jurisdiction_city, home_url = excluded.home_url,
                       template = excluded.template, access = excluded.access, access_reason = excluded.access_reason,
                       source_id = excluded.source_id, registry_version = excluded.registry_version, updated_at = now()""",
                [a["allocator_id"], a["display_name"], a["display_name"], a["allocator_type"], a["jurisdiction_city"],
                 a.get("jurisdiction_state", "FL"), a["home_url"], a["template"], a["access"], a.get("access_reason"),
                 a["source_id"], v],
            )
            con.execute("DELETE FROM allocator_pages WHERE allocator_id = ?", [a["allocator_id"]])
            if a["access"] == "AUTO":
                for p in a.get("document_pages", []):
                    if selected_pages is not None and (a["allocator_id"], p["url"]) not in selected_pages:
                        continue
                    con.execute("INSERT INTO allocator_pages VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                                [a["allocator_id"], p["url"], p.get("label")])
            n += 1
        # Allocators no longer in the registry leave the store (their fetched documents stay in the lake).
        keep = [x["allocator_id"] for x in reg["allocators"]]
        marks = ",".join("?" * len(keep))
        gone = [r[0] for r in con.execute(f"SELECT allocator_id FROM allocators WHERE allocator_id NOT IN ({marks})", keep).fetchall()]
        for g in gone:
            for table in ("allocator_pages", "document_urls", "mandates", "officers", "consultant_engagements"):
                con.execute(f"DELETE FROM {table} WHERE allocator_id = ?", [g])
            con.execute("DELETE FROM row_evidence WHERE row_kind = 'signal' AND row_id IN (SELECT signal_id FROM signals WHERE allocator_id = ?)", [g])
            con.execute("DELETE FROM signals WHERE allocator_id = ?", [g])
            con.execute("DELETE FROM allocators WHERE allocator_id = ?", [g])
        con.execute("INSERT INTO registry_approvals VALUES (?, ?, ?, now(), ?)", [region, v, actor, note])
        db.audit(con, actor, "registry_approve", region, detail={"version": v, "allocators": n, "removed": gone,
                                                                 "pages": len(selected_pages or [])})
        con.execute("COMMIT")
    return n


def auto_approve_if_policy(region: str = "florida") -> bool:
    """Approve the current registry version under the owner's policy (all suggested pages)."""
    from radar.review import policy_actor

    actor = policy_actor("registry")
    reg, v = load(region)
    if not actor or reg is None:
        return False
    with db.connect() as con:
        if is_current_approved(con, region):
            return True
    selected = {(a["allocator_id"], p["url"]) for a in reg["allocators"] for p in a.get("document_pages", [])
                if p.get("suggested")}
    approve(actor, v, selected, region, note="auto-approved under owner policy (suggested pages)")
    return True
