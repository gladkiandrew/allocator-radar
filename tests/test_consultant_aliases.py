"""D-B18: curated consultant aliases map printed variants to one canonical name."""

import yaml

from radar import db, resolve
from radar.settings import REPO_ROOT


def test_mapping_file_is_well_formed():
    cfg = yaml.safe_load((REPO_ROOT / "data" / resolve.CURATED_FILE).read_text())
    seen = {}
    for canon, aliases in cfg["canonical"].items():
        for a in aliases or []:
            assert a not in seen, f"{a} mapped twice"
            assert a != canon
            seen[a] = canon


def test_curated_aliases_loaded_and_used():
    with db.connect() as con:
        con.execute(resolve.DDL)
        assert resolve.load_curated(con) > 10
        cmap = resolve.canonical_map(con, "consultant")
    assert cmap["AndCo Consulting"] == "Mariner Institutional"
    assert cmap["The Bogdahn Group"] == "Mariner Institutional"
    assert cmap["Segal Marco"] == "Segal Marco Advisors"
    assert "Segal" not in cmap  # ambiguous: left unmapped


def test_resolver_never_creates_mutual_aliases():
    with db.connect() as con:
        con.execute("""INSERT INTO allocators (allocator_id, legal_name, display_name, allocator_type, jurisdiction_city,
                       jurisdiction_state, home_url, template, access, updated_at)
                       VALUES ('m', 'M', 'M', 'POLICE_FIRE', 'Miami', 'FL', 'https://m.invalid', 'T-WP', 'AUTO', now())""")
        for i, name in enumerate(["MetLife", "Metlife"]):
            con.execute("INSERT INTO consultant_engagements (engagement_id, allocator_id, consultant_name_as_disclosed, as_of, dedupe_key) VALUES (?, 'm', ?, DATE '2026-01-01', ?)", [f"e{i}", name, f"k{i}"])
    resolve.run()
    with db.connect() as con:
        rows = con.execute("SELECT alias, canonical FROM entity_aliases WHERE kind = 'consultant' AND alias IN ('MetLife', 'Metlife')").fetchall()
    assert len(rows) == 1, rows
