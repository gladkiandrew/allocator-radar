"""Data-session fixes (D-B24, D-B25 and later)."""

from datetime import date, timedelta

from radar import db


def _doc(con, sha, aid, doc_date, first_seen, doc_type="AGENDA", text="Agenda"):
    con.execute("""INSERT INTO documents (sha256, content_type, n_bytes, page_count, provenance, first_seen, parsed_at)
                   VALUES (?, 'application/pdf', 1, 1, 'CRAWL', ?, ?) ON CONFLICT DO NOTHING""", [sha, first_seen, first_seen])
    con.execute("""INSERT INTO document_urls (sha256, url, allocator_id, doc_type, doc_date, first_seen, last_fetched)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""", [sha, f"https://x.test/{sha}.pdf", aid, doc_type, doc_date, first_seen, first_seen])
    con.execute("INSERT INTO pages VALUES (?, 1, ?, 0, NULL) ON CONFLICT DO NOTHING", [sha, text])


def test_upcoming_agenda_keeps_meeting_and_retrieval_dates_apart():
    today = date.today()
    with db.connect() as con:
        _doc(con, "a" * 64, "p1", today + timedelta(days=20), today - timedelta(days=2))
        row = con.execute("SELECT meeting_date, retrieved_on, as_of_date, upcoming_when_retrieved FROM document_dates "
                          "WHERE sha256 = ?", ["a" * 64]).fetchone()
        assert row == (today + timedelta(days=20), today - timedelta(days=2), today - timedelta(days=2), True)


def test_as_of_clamps_upcoming_meeting_rows_to_today():
    from radar.extract.extractor import _as_of
    today = date(2026, 11, 11)
    assert _as_of("2026-11-16", today) == today
    assert _as_of("2026-10-01", today) == date(2026, 10, 1)
    assert _as_of("2027-06-01", today) is None


def test_add_page_keeps_layout(tmp_path):
    from radar.registry.pages import _insert_yaml
    text = ("allocators:\n- allocator_id: a\n  document_pages:\n  - url: https://x.test/1\n    label: one\n"
            "- allocator_id: b\n  document_pages:\n  - url: https://y.test/1\n    label: y\n")
    new, added = _insert_yaml(text, "a", "https://x.test/2", "Upcoming Agenda")
    assert added and new.index("https://x.test/2") < new.index("allocator_id: b")
    again, added2 = _insert_yaml(new, "a", "https://x.test/2", "Upcoming Agenda")
    assert not added2 and again == new


def test_curated_signal_held_to_extractor_validators():
    import pytest
    from radar.extract.curated import CuratedError, add_signal
    today = date.today()
    with db.connect() as con:
        con.execute("INSERT INTO allocators (allocator_id, legal_name, display_name, allocator_type, jurisdiction_city, "
                    "jurisdiction_state, jurisdiction_country, home_url, template, access, source_id, registry_version, updated_at) "
                    "VALUES ('p1','P','Test Plan','GENERAL','X','FL','US','https://x.test','T','AUTO','S','v',now())")
        _doc(con, "c" * 64, "p1", today, today, text="5. Update Re Fee Discussions with Private Credit Managers Alpha and Beta Capital")
    with pytest.raises(CuratedError):  # quote not on the page
        add_signal("ccc", "p1", 1, "Interviews of Gamma", "SEARCH_SCHEDULED", "manager", today, "Test Plan lists Gamma.")
    with pytest.raises(CuratedError):  # summary's names do not appear in the quote
        add_signal("ccc", "p1", 1, "Update Re Fee Discussions", "SEARCH_SCHEDULED", "manager",
                   today, "Test Plan lists fee discussions with Gamma Partners.")
    with pytest.raises(CuratedError):  # future date outside the window for a non-search type
        add_signal("ccc", "p1", 1, "Alpha and Beta Capital", "MANDATE_AWARDED", "manager",
                   today + timedelta(days=3), "Test Plan awarded Alpha.")
    sid = add_signal("ccc", "p1", 1, "Update Re Fee Discussions with Private Credit Managers Alpha and Beta Capital",
                     "SEARCH_SCHEDULED", "manager", today,
                     "Test Plan's agenda lists an update on fee discussions with private credit managers Alpha and Beta Capital.")
    with db.connect() as con:
        st, = con.execute("SELECT review_status FROM signals WHERE signal_id = ?", [sid]).fetchone()
        ex, = con.execute("SELECT e.extractor FROM row_evidence r JOIN evidence e USING (evidence_id) WHERE r.row_id = ?",
                          [sid]).fetchone()
    assert st == "new" and ex == "curated-extraction"
