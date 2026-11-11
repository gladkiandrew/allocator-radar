"""export-static (D-B15) and the wall check on dist/."""

import re

import pytest

from radar import db, static_export
from radar.wall import static_site


def _store_with_signals():
    from radar.extract import extractor as X
    from tests.test_extractor import FakeProvider, _setup

    sha = _setup()
    sigs = [X.XSignal(signal_type="RFP_ISSUED", qualifier="manager", event_date="2026-01-15",
                      summary="The Board voted to issue an RFP for a hedge fund of funds manager.", page=1,
                      quote="The Board voted to issue a Request for Proposals for a hedge fund of funds manager.",
                      confidence=0.9)]
    X.extract_document(sha, "canary", provider=FakeProvider(X.XResult(signals=sigs, mandates=[], officers=[], consultants=[])))
    with db.connect() as con:
        con.execute("UPDATE signals SET review_status='auto_approved', review_note='quote matches cited page', bucket='FUND_OF_HEDGE_FUNDS', hf_relevant=true")
    return sha


def test_export_writes_public_pages_with_relative_links(tmp_path):
    _store_with_signals()
    out = tmp_path / "dist"
    st = static_export.export(out)
    for page in ("index.html", "how.html", "about.html", "gaps.html", "404.html", "a/canary.html", "static/style.css"):
        assert (out / page).is_file(), page
    assert st["pages"] == sum(1 for _ in out.rglob("*.html"))
    assert not (out / "static" / "htmx.min.js").exists()

    brief = (out / "a" / "canary.html").read_text()
    assert 'href="../index.html"' in brief and 'href="../static/style.css"' in brief
    assert "verified against source" in brief and "Public-record data. Updated " in brief  # auto-approved, crawled: no person yet
    assert ">auto-approved<" not in brief  # never an unqualified "auto-approved" label
    assert "https://canary.invalid/a.pdf#page=1" in brief  # citations go to the public source, not the lake
    assert "/doc/" not in brief and "hx-get" not in brief
    for f in out.rglob("*.html"):
        if f.name != "404.html":
            assert not re.search(r'(href|src)="/[^/]', f.read_text()), f"root-absolute link in {f}"
    assert "/review" not in (out / "index.html").read_text()
    assert static_site.check(out) == []


def test_manual_source_citation_is_text_not_a_link(tmp_path):
    sha = _store_with_signals()
    with db.connect() as con:
        con.execute("UPDATE evidence SET source_url = 'manual://canary/a.pdf'")
        con.execute("UPDATE document_urls SET url = 'manual://canary/a.pdf' WHERE sha256 = ?", [sha])
    out = tmp_path / "dist"
    static_export.export(out)
    brief = (out / "a" / "canary.html").read_text()
    assert "manual://" not in brief and "manual source" in brief
    assert static_site.check(out) == []


def test_unknown_internal_link_fails_the_export():
    with pytest.raises(static_export.ExportError):
        static_export.out_path("/review")
    with pytest.raises(static_export.ExportError):
        static_export.out_path("/doc/" + "0" * 64)
    assert static_export.out_path("/", "tier=DEPLOYING&strategy=hf") == "view-hf-deploying.html"
    assert static_export.out_path("/", "strategy=all") == "index.html"
    assert static_export.out_path("/ef/hedge-fund-buyers") == "ef-hf.html"
    assert static_export.out_path("/ef") == "ef.html" and static_export.out_path("/ef/590000001") == "ef/590000001.html"


def test_wall_static_catches_contact_admin_links_and_data_files(tmp_path):
    d = tmp_path / "dist"
    (d / "a").mkdir(parents=True)
    (d / "index.html").write_text('<a href="a/x.html">ok</a><a href="https://example.org/r.pdf">ok</a>')
    assert static_site.check(d) == []
    (d / "a" / "x.html").write_text(
        '<p>Call (954) 555-0142 or jane@example.org</p><a href="../review.html">r</a><a href="/export.csv">e</a>'
        '<a href="manual://x/y.pdf">m</a><a href="mailto:a@b.co">c</a><form method="post"></form>')
    (d / "radar.duckdb").write_bytes(b"x")
    v = "\n".join(static_site.check(d))
    for needle in ("shows phone", "shows email", "/review.html", "/export.csv", "non-public scheme", "mailto",
                   "form that posts", "radar.duckdb"):
        assert needle in v, needle


def test_wall_reads_table_cells_separately_but_still_catches_a_street_in_one_cell():
    from radar.wall.static_site import visible_text
    from radar.wall.scrubber import detect

    across = "<table><tr><td>$450,282</td></tr>\n    <tr><td>Park Place International LLC</td></tr></table>"
    assert not detect(visible_text(across))
    inside = "<table><tr><td>Office at 282 Park Place, Suite 4</td></tr></table>"
    assert detect(visible_text(inside))


def test_unverified_signals_never_render_in_the_public_build(tmp_path):
    from radar import db

    _store_with_signals()
    with db.connect() as con:  # auto-approved but with no record of a source check: unverified
        con.execute("UPDATE signals SET review_note = 'returned to queue'")
    out = tmp_path / "dist"
    static_export.export(out)
    brief = (out / "a" / "canary.html").read_text()
    assert "hedge fund of funds" not in brief.lower() and "verified against source" not in brief


def test_contacts_render_locally_but_never_in_the_ungated_public_build(tmp_path):
    from fastapi.testclient import TestClient

    from radar import db
    from radar.web.app import create_app

    _store_with_signals()
    with db.connect() as con:
        con.execute("""CREATE TABLE contacts (contact_id VARCHAR, allocator_id VARCHAR, name_as_printed VARCHAR,
                       title_as_printed VARCHAR, email VARCHAR, phone VARCHAR, as_of DATE, source_url VARCHAR, page INTEGER)""")
        con.execute("""INSERT INTO contacts VALUES ('c1', 'canary', 'Pat Example', 'Plan Administrator',
                       'admin@canary.invalid', '555-0100', DATE '2026-10-01', 'https://canary.invalid/a.pdf', 1)""")
    local = TestClient(create_app()).get("/a/canary").text
    assert "Pat Example" in local and 'data-audience="member"' in local
    out = tmp_path / "dist"
    static_export.export(out)
    pub = (out / "a" / "canary.html").read_text()
    assert "admin@canary.invalid" not in pub and 'data-audience="member"' not in pub
