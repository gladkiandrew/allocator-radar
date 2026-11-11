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


def test_staging_ingest_rechecks_files(tmp_path, monkeypatch):
    import yaml
    from radar import staging
    from radar.settings import get_settings
    monkeypatch.setattr(staging, "_robots_ok", lambda url: "blocked" not in url)
    with db.connect() as con:
        con.execute("INSERT INTO allocators (allocator_id, legal_name, display_name, allocator_type, jurisdiction_city, "
                    "jurisdiction_state, jurisdiction_country, home_url, template, access, source_id, registry_version, updated_at) "
                    "VALUES ('p1','P','Test Plan','GENERAL','X','FL','US','https://x.test','T','AUTO','S','v',now())")
    root = get_settings().data_dir / "staging" / "g"
    (root / "p1").mkdir(parents=True)
    (root / "p1" / "a.pdf").write_bytes(b"%PDF-1.4 not really")
    (root / "p1" / "b.pdf").write_bytes(b"<html>error</html>")
    (root / "p1" / "c.pdf").write_bytes(b"%PDF-1.4 x")
    (root / "manifest.yaml").write_text(yaml.safe_dump({"files": [
        {"id": "p1", "file": "p1/a.pdf", "url": "https://x.test/a.pdf", "title": "Agenda", "doc_type": "AGENDA", "doc_date": "2026-10-01"},
        {"id": "p1", "file": "p1/b.pdf", "url": "https://x.test/b.pdf"},
        {"id": "p1", "file": "p1/c.pdf", "url": "https://blocked.test/c.pdf"},
        {"id": "nope", "file": "p1/a.pdf", "url": "https://x.test/a.pdf"}]}))
    monkeypatch.setattr("radar.parse.pdf.parse_document", lambda sha, **k: 0)
    st = staging.ingest_pensions("g")
    assert st["files"] == 1 and len(st["skipped"]) == 3
    with db.connect() as con:
        assert con.execute("SELECT retrieved_by, doc_date FROM document_urls WHERE allocator_id='p1'").fetchone() == \
            ("browser-agent", date(2026, 10, 1))


def _plan(con, aid="p1", name="Test Plan"):
    con.execute("INSERT INTO allocators (allocator_id, legal_name, display_name, allocator_type, jurisdiction_city, "
                "jurisdiction_state, jurisdiction_country, home_url, template, access, source_id, registry_version, updated_at) "
                "VALUES (?, ?, ?, 'GENERAL','X','FL','US','https://x.test','T','AUTO','S','v',now()) ON CONFLICT DO NOTHING",
                [aid, name, name])


def _mandate(con, aid, name):
    con.execute("INSERT INTO mandates (mandate_id, allocator_id, manager_name_as_disclosed, bucket_label_as_disclosed, bucket, "
                "amount_usd_as_disclosed, amount_kind, as_of, status, fee_as_disclosed, dedupe_key) "
                "VALUES (?, ?, ?, 'Private Credit', 'PRIVATE_CREDIT', 1, 'market_value', DATE '2026-06-30', 'active', NULL, ?)",
                [name + aid, aid, name, name + aid])


def test_definitions_hollywood_brightwood_is_incumbent_presentation():
    from radar.extract.definitions import apply
    with db.connect() as con:
        _plan(con, "hw"); _mandate(con, "hw", "Brightwood Capital Fund V")
        st, q, note = apply(con, "hw", "SEARCH_SCHEDULED", "manager",
                            "The agenda lists a Private Credit: Brightwood Presentation.", "C. Private Credit: Brightwood Presentation")
    assert st == "MANAGER_PRESENTATION" and note.startswith("definition 1")
    with db.connect() as con:  # even if it said "candidate", a held firm is an incumbent update
        st, q, note = apply(con, "hw", "FINALISTS_NAMED", "manager", "Brightwood named a candidate.", "Brightwood candidate interview",
                            event_date=date(2026, 9, 22))
    assert st == "INCUMBENT_UPDATE" and "Brightwood" in note


def test_definitions_vero_cohen_presentation_not_a_search_but_search_words_qualify():
    from radar.extract.definitions import apply
    with db.connect() as con:
        _plan(con, "vb"); _mandate(con, "vb", "Dana Large Cap Value")
        st, *_ = apply(con, "vb", "SEARCH_SCHEDULED", "manager", "lists a Cohen & Steers presentation",
                       "c. Cohen & Steers presentation by Brian Casey")
        assert st == "MANAGER_PRESENTATION"
        st, q, _ = apply(con, "vb", "SEARCH_OPENED", "manager", "packet includes a US Large Cap Value manager search report",
                         "US Large Cap Value Manager Search as of 6/30/2026")
        assert (st, q) == ("SEARCH_OPENED", "manager")


def test_definitions_service_providers_never_manager():
    from radar.extract.definitions import apply
    with db.connect() as con:
        _plan(con, "ucf")
        assert apply(con, "ucf", "RFP_ISSUED", "manager", "The board approved an RFP for an independent auditor.",
                     "approve an RFP for an independent auditor")[1] == "service_provider"
        assert apply(con, "ucf", "SEARCH_OPENED", "manager", "The board opened an OCIO search.", "OCIO search")[1] == "consultant"
        # a manager search that merely mentions the consultant stays a manager search
        assert apply(con, "ucf", "SEARCH_SCHEDULED", "manager", "The board held international manager interviews led by the consultant.",
                     "international manager interviews; consultant Mariner")[1] == "manager"


def test_ocio_calendar_caps_at_preparing():
    from datetime import date, timedelta
    from radar.sources.endowments.signals import ef_readiness
    s = {"source_type": "meeting_calendar", "signal_type": "OCIO_SEARCH", "qualifier": "manager", "review_status": "auto_approved",
         "event_date": date.today() + timedelta(days=8)}
    assert ef_readiness([s], True).tier == "PREPARING"


def test_d_b35_contacts_kept_by_default(monkeypatch):
    from radar import settings as S
    monkeypatch.setenv("RADAR_SCRUB_CONTACTS", "false"); S.get_settings.cache_clear()
    from radar.wall.scrubber import scrub
    t = "Plan Administrator Jane Doe, jdoe@example.org, (954) 555-1212, 100 Main Street, Suite 2, Hollywood, FL 33020"
    assert scrub(t).text == t
    assert "[REDACTED" in scrub("see https://www.linkedin.com/in/someone").text  # person social profiles still removed
    from radar.llm.provider import _guard
    _guard(t)  # no refusal
    S.get_settings.cache_clear()
