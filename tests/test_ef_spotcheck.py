"""E&F spot-check review (/review/ef): md input, JSONL decisions, never the database."""

import hashlib
import json
import re
from datetime import date

from fastapi.testclient import TestClient

from radar.sources.endowments import store
from radar.web import ef_spotcheck as efs

SIGS = [
    ("ef-aaa111", "590000001", "UNIVERSITY OF NOWHERE FOUNDATION INC", "UNIVERSITY_FOUNDATION",
     "Form 990 TY2023 Schedule D Part VII line 3", 4, "a quoted line"),
    ("ef-bbb222", "590000002", "SUNSHINE FAMILY FOUNDATION", "PRIVATE_FOUNDATION",
     "Form 990-PF TY2024 Part VII line 3", None, None),
    ("ef-ccc333", "590000003", "GULF COMMUNITY FOUNDATION", "COMMUNITY_FOUNDATION",
     "Form 990 TY2023 Part X line 12", None, None),
]


def _seed():
    store.init()
    with store.connect() as con:
        for sid, ein, name, kind, cite, page, quote in SIGS:
            con.execute("INSERT INTO ef_orgs (ein, name, kind) VALUES (?, ?, ?)", [ein, name, kind])
            con.execute("""INSERT INTO ef_signals (signal_id, ein, signal_type, event_date, summary, source_type,
                           source_url, doc_title, cite, page, quote) VALUES (?, ?, 'EF_ALT_HOLDING', '2024-06-30', ?,
                           '990', ?, 'Form 990', ?, ?, ?)""",
                        [sid, ein, f"Summary for {sid}", f"https://example.org/{ein}.pdf", cite, page, quote])


def _md(ids=None):
    ids = ids or [s[0] for s in SIGS]
    lines = ["# E&F spot-check, 25 signals", "", "some junk line ef-bbb222 not a checkbox",
             "- [ ] not-an-id | nothing here"]
    for i, sid in enumerate(ids):
        box = "[x]" if i == 1 else "[ ]"
        lines.append(f"- {box} {sid} | Org | summary")
    efs.md_path().write_text("\n".join(lines) + "\n")


def _client():
    from radar.web.app import create_app
    return TestClient(create_app())


def _shown(html):
    return re.search(r'data-signal="([^"]+)"', html).group(1)


def _lines():
    p = efs.decisions_path()
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _sha():
    return hashlib.sha256(store.path().read_bytes()).hexdigest()


def test_parse_md_tolerates_junk_and_checked_boxes():
    known = {"ef-aaa111", "ef-bbb222", "ef-ccc333"}
    text = "\n".join([
        "# heading ef-aaa111",
        "ef-ccc333 plain line",
        "- [ ] ef-aaa111 | Org A | x",
        "- [x] `ef-bbb222` | Org B | y",
        "  * [X] see: ef-ccc333 | Org C",
        "- [ ] unknown-id | Org D | ef-aaa111 (first KNOWN token counts; a repeat is dropped)",
        "- [ ] ef-aaa111 | duplicate",
        "- [ ] nothing known here",
        "- [?] ef-ccc333 bad box",
    ])
    assert efs.parse_md(text, known) == ["ef-aaa111", "ef-bbb222", "ef-ccc333"]


def test_page_shows_one_signal_with_context():
    _seed()
    _md()
    c = _client()
    assert 'href="/review/ef"' in c.get("/review").text
    r = c.get("/review/ef")
    assert r.status_code == 200
    assert r.text.count('class="card review-card"') == 1
    assert _shown(r.text) == "ef-aaa111"
    assert "University of Nowhere Foundation Inc" in r.text and "University foundation" in r.text
    assert "Summary for ef-aaa111" in r.text and "EF_ALT_HOLDING" in r.text and "2024-06-30" in r.text
    assert "Form 990 TY2023 Schedule D Part VII line 3" in r.text and "page 4" in r.text
    assert "a quoted line" in r.text
    assert 'href="https://example.org/590000001.pdf#page=4"' in r.text
    assert "3 remaining" in r.text and "0 decided" in r.text
    assert "<kbd>a</kbd> Approve" in r.text and "<kbd>r</kbd> Reject" in r.text and "<kbd>s</kbd> Skip" in r.text


def test_missing_md_says_so():
    _seed()
    r = _client().get("/review/ef")
    assert r.status_code == 200 and "does not exist" in r.text


def test_approve_writes_one_line_and_skip_writes_none():
    _seed()
    _md()
    c = _client()
    r = c.post("/review/ef/ef-aaa111", data={"decision": "skip", "skip": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/review/ef?skip=ef-aaa111"
    assert not efs.decisions_path().exists()
    r = c.get(r.headers["location"])
    assert _shown(r.text) == "ef-bbb222" and "3 remaining" in r.text and "1 skipped" in r.text

    r = c.post("/review/ef/ef-bbb222", data={"decision": "approved", "skip": "ef-aaa111", "note": " looks right "})
    assert r.status_code == 200 and "Approved" in r.text
    lines = _lines()
    assert len(lines) == 1
    d = lines[0]
    assert set(d) == {"id", "decision", "by", "at", "note"}
    assert d["id"] == "ef-bbb222" and d["decision"] == "approved" and d["by"] == "owner" and d["note"] == "looks right"
    assert date.fromisoformat(d["at"][:10])
    assert _shown(r.text) == "ef-ccc333" and "2 remaining" in r.text and "1 decided" in r.text

    # an id outside the sample, or a bad decision, writes nothing
    r = c.post("/review/ef/ef-zzz999", data={"decision": "approved"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    r = c.post("/review/ef/ef-ccc333", data={"decision": "bogus"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert len(_lines()) == 1


def test_latest_decision_per_id_wins():
    _seed()
    _md()
    efs.append_decision("ef-aaa111", "rejected", "owner")
    efs.append_decision("ef-aaa111", "approved", "owner", "changed my mind")
    with open(efs.decisions_path(), "a") as fh:
        fh.write("not json\n")
    latest = efs.read_decisions()
    assert latest["ef-aaa111"]["decision"] == "approved"
    st = efs.state([s[0] for s in SIGS], latest)
    assert st["decided"] == 1 and st["approved"] == 1 and st["undecided"] == ["ef-bbb222", "ef-ccc333"]
    efs.append_decision("ef-aaa111", "rejected", "owner")
    assert efs.state([s[0] for s in SIGS], efs.read_decisions())["rejected"] == 1


def test_done_screen_shows_accuracy_and_handoff_line():
    _seed()
    _md()
    c = _client()
    c.post("/review/ef/ef-aaa111", data={"decision": "approved"})
    c.post("/review/ef/ef-bbb222", data={"decision": "rejected"})
    r = c.post("/review/ef/ef-ccc333", data={"decision": "approved"})
    assert 'id="ef-done"' in r.text and 'class="card review-card"' not in r.text
    assert "accuracy <strong>66.7%</strong>" in r.text
    line = (f"SPOT-CHECK {date.today().isoformat()}: 3 signals checked, 2 correct, accuracy 66.7%, "
            "method owner review of data/ef_spotcheck_25.md in /review/ef")
    assert line in r.text
    assert efs.handoff_line(25, 23, date(2026, 11, 11)) == (
        "SPOT-CHECK 2026-11-11: 25 signals checked, 23 correct, accuracy 92%, "
        "method owner review of data/ef_spotcheck_25.md in /review/ef")


def test_public_mode_returns_404(monkeypatch):
    from radar import settings as S

    _seed()
    _md()
    monkeypatch.setenv("RADAR_PUBLIC_MODE", "true")
    S.get_settings.cache_clear()
    try:
        c = _client()
        assert c.get("/review/ef").status_code == 404
        assert c.post("/review/ef/ef-aaa111", data={"decision": "approved"}).status_code == 403
        assert not efs.decisions_path().exists()
    finally:
        monkeypatch.delenv("RADAR_PUBLIC_MODE")
        S.get_settings.cache_clear()


def test_store_file_unchanged_by_decisions():
    _seed()
    _md()
    before, mtime = _sha(), store.path().stat().st_mtime_ns
    c = _client()
    c.get("/review/ef")
    c.post("/review/ef/ef-aaa111", data={"decision": "skip"})
    c.post("/review/ef/ef-aaa111", data={"decision": "approved"})
    c.post("/review/ef/ef-bbb222", data={"decision": "rejected"})
    c.get("/review/ef")
    assert _sha() == before and store.path().stat().st_mtime_ns == mtime
    with store.connect(read_only=True) as con:
        assert {r[0] for r in con.execute("SELECT review_status FROM ef_signals").fetchall()} == {"new"}


def test_source_url_with_its_own_fragment_is_not_doubled():
    _seed()
    with store.connect() as con:
        con.execute("UPDATE ef_signals SET source_url = source_url || '#page=4', cite = cite || ', page 4' "
                    "WHERE signal_id = 'ef-aaa111'")
    _md()
    r = _client().get("/review/ef")
    assert 'href="https://example.org/590000001.pdf#page=4"' in r.text and "#page=4#page=4" not in r.text
    assert "page 4 · page 4" not in r.text
