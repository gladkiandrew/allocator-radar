"""Local-only admin pages: /tasks (manual fetch tasks + upload) and /review/next (focus review)."""

import re

import pytest
from fastapi.testclient import TestClient

from radar import db
from radar.settings import get_settings
from radar.web import tasks as mt

YAML = """\
# Maintained by the data session. Keep comments.
tasks:
  # top priority first
  - id: fl-sba-frs-2026q3-perf
    priority: 1
    allocator_id: fl-sba-frs
    allocator: Florida SBA (FRS Pension Plan)
    what: Q3 2026 quarterly performance report
    url: https://example.org/sba/q3.pdf
    file_name: sba-2026q3-performance.pdf
    drop_folder: inbox/manual/fl-sba-frs/
    why_manual: site blocks automated GETs
    status: open   # open | received | ingested | dropped

  - id: canary-minutes-oct
    priority: 2
    allocator_id: canary
    allocator: "Canary P&F"
    what: October board minutes
    url: https://example.org/canary/oct.pdf
    file_name: canary-2026-10-minutes.pdf
    drop_folder: inbox/manual/canary/
    why_manual: behind a click-through
    status: 'open'

  - id: old-one
    priority: 1
    allocator_id: canary
    allocator: Canary P&F
    what: Already done
    url: https://example.org/canary/old.pdf
    file_name: old.pdf
    drop_folder: inbox/manual/canary/
    why_manual: manual only
    status: ingested

  - id: evil
    priority: 3
    allocator_id: ../../etc
    allocator: Nobody
    what: traversal attempt
    url: https://example.org/x.pdf
    file_name: ../passwd
    drop_folder: x
    why_manual: test
    status: open
"""


def _write_yaml(text=YAML):
    p = mt.tasks_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _client():
    from radar.web.app import create_app
    return TestClient(create_app())


def test_tasks_empty_state_when_file_missing():
    assert not mt.tasks_path().exists()
    r = _client().get("/tasks")
    assert r.status_code == 200 and "does not exist" in r.text


def test_tasks_page_renders_sorted_with_upload_boxes():
    _write_yaml()
    r = _client().get("/tasks")
    assert r.status_code == 200
    t = r.text
    for needle in ("Florida SBA (FRS Pension Plan)", "Q3 2026 quarterly performance report",
                   'href="https://example.org/sba/q3.pdf"', "sba-2026q3-performance.pdf", "inbox/manual/fl-sba-frs/",
                   "site blocks automated GETs", "Already done"):
        assert needle in t, needle
    # open tasks first (by priority), the ingested one after them
    order = [t.index(x) for x in ("task-fl-sba-frs-2026q3-perf", "task-canary-minutes-oct", "task-evil", "task-old-one")]
    assert order == sorted(order)
    assert 'action="/tasks/fl-sba-frs-2026q3-perf/upload"' in t
    assert 'action="/tasks/old-one/upload"' not in t  # not open
    assert 'action="/tasks/evil/upload"' not in t  # unsafe names: no upload box
    assert 'href="/tasks"' in _client().get("/").text  # Tools menu entry, local only


def test_upload_saves_file_and_marks_only_that_task():
    p = _write_yaml()
    c = _client()
    r = c.post("/tasks/canary-minutes-oct/upload", files={"file": ("whatever-client-name.pdf", b"%PDF-1.4 minutes")},
               follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    dest = get_settings().manual_inbox / "canary" / "canary-2026-10-minutes.pdf"
    assert dest.read_bytes() == b"%PDF-1.4 minutes"
    assert not (get_settings().manual_inbox / "canary" / "whatever-client-name.pdf").exists()
    new = p.read_text()
    old_lines, new_lines = YAML.splitlines(keepends=True), new.splitlines(keepends=True)
    assert len(old_lines) == len(new_lines)
    diff = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
    assert diff == [("    status: 'open'\n", "    status: 'received'\n")]
    assert {t["id"]: t["status"] for t in mt.load()}["canary-minutes-oct"] == "received"
    # no longer open: a second upload is refused
    r = c.post("/tasks/canary-minutes-oct/upload", files={"file": ("x.pdf", b"other")}, follow_redirects=False)
    assert "err=" in r.headers["location"]

    # status line with a trailing comment keeps the comment
    c.post("/tasks/fl-sba-frs-2026q3-perf/upload", files={"file": ("a.pdf", b"%PDF q3")})
    assert "    status: received   # open | received | ingested | dropped\n" in p.read_text()
    assert p.read_text().count("\n") == YAML.count("\n")


def test_upload_rejects_traversal_and_refuses_overwrite():
    p = _write_yaml()
    c = _client()
    r = c.post("/tasks/evil/upload", files={"file": ("x", b"data")}, follow_redirects=False)
    assert "err=" in r.headers["location"] and p.read_text() == YAML
    assert not any(get_settings().manual_inbox.parent.rglob("passwd"))
    r = c.post("/tasks/..%2F..%2Fetc/upload", files={"file": ("x", b"data")}, follow_redirects=False)
    assert r.status_code in (303, 404) and p.read_text() == YAML
    with pytest.raises(mt.TaskError):
        mt.destination({"allocator_id": "a/b", "file_name": "x.pdf"})
    with pytest.raises(mt.TaskError):
        mt.destination({"allocator_id": "ok", "file_name": ".."})
    with pytest.raises(mt.TaskError):
        mt.destination({"allocator_id": "..", "file_name": "x.pdf"})

    dest = get_settings().manual_inbox / "fl-sba-frs" / "sba-2026q3-performance.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"existing")
    r = c.post("/tasks/fl-sba-frs-2026q3-perf/upload", files={"file": ("a.pdf", b"different")}, follow_redirects=False)
    assert "err=" in r.headers["location"] and dest.read_bytes() == b"existing" and p.read_text() == YAML
    r = c.post("/tasks/fl-sba-frs-2026q3-perf/upload", files={"file": ("a.pdf", b"existing")}, follow_redirects=False)
    assert "msg=" in r.headers["location"]  # byte-identical: accepted, task marked received
    assert {t["id"]: t["status"] for t in mt.load()}["fl-sba-frs-2026q3-perf"] == "received"


def test_status_edit_refuses_when_unsure():
    dup = YAML + "\n  - id: old-one\n    status: open\n"
    with pytest.raises(mt.TaskError):
        mt.set_status_text(dup, "old-one", "received")
    no_status = YAML.replace("    status: ingested\n", "")
    with pytest.raises(mt.TaskError):
        mt.set_status_text(no_status, "old-one", "received")
    flow = "tasks: [{id: a, status: open}]\n"
    with pytest.raises(mt.TaskError):
        mt.set_status_text(flow, "a", "received")
    # id not on the dash line still works
    other = "tasks:\n  - priority: 1\n    id: b  # note\n    status: open\n"
    assert mt.set_status_text(other, "b", "received") == other.replace("status: open", "status: received")


# ---------------- focus review ----------------
def _signals(n=3):
    from radar.extract import extractor as X
    from tests.test_extractor import FakeProvider, _setup

    sha = _setup()
    sigs = [X.XSignal(signal_type="RFP_ISSUED", qualifier="manager", event_date=f"2026-01-{10 + i}",
                      summary=f"The Board voted to issue an RFP number {i} for a hedge fund of funds manager.", page=1,
                      quote="The Board voted to issue a Request for Proposals for a hedge fund of funds manager.",
                      confidence=0.9) for i in range(n)]
    X.extract_document(sha, "canary", provider=FakeProvider(X.XResult(signals=sigs, mandates=[], officers=[], consultants=[])))
    with db.connect() as con:
        return [r[0] for r in con.execute("SELECT signal_id FROM signals WHERE review_status = 'new'").fetchall()]


def _shown(html):
    return re.search(r'data-signal="([^"]+)"', html).group(1)


def test_focus_review_one_at_a_time_with_keys():
    ids = _signals(3)
    assert len(ids) >= 2
    n = len(ids)
    c = _client()
    assert 'href="/review/next"' in c.get("/review").text
    r = c.get("/review/next")
    assert r.status_code == 200 and f"{n} remaining" in r.text
    assert r.text.count('class="card review-card"') == 1
    assert "<kbd>a</kbd> Approve" in r.text and "<kbd>r</kbd> Reject" in r.text and "<kbd>s</kbd> Skip" in r.text
    assert "/doc/" in r.text and "#page=1" in r.text  # local citation to the cached page
    first = _shown(r.text)

    # skip: advances without deciding, and the skip list travels in the query string
    r = c.post(f"/review/next/{first}", data={"decision": "skip", "skip": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/review/next?skip={first}"
    r = c.get(r.headers["location"])
    second = _shown(r.text)
    assert second != first and f"{n - 1} remaining" in r.text
    with db.connect() as con:
        assert con.execute("SELECT review_status FROM signals WHERE signal_id = ?", [first]).fetchone()[0] == "new"

    # approve the second, keeping the first skipped
    r = c.post(f"/review/next/{second}", data={"decision": "approved", "skip": first})
    assert r.status_code == 200 and f"{n - 2} remaining" in r.text and "Approved" in r.text
    with db.connect() as con:
        st, by = con.execute("SELECT review_status, reviewed_by FROM signals WHERE signal_id = ?", [second]).fetchone()
    assert st == "approved" and by == "owner"
    if n > 2:
        third = _shown(r.text)
        assert third not in (first, second)
        r = c.post(f"/review/next/{third}", data={"decision": "rejected", "skip": first})
        with db.connect() as con:
            assert con.execute("SELECT review_status FROM signals WHERE signal_id = ?", [third]).fetchone()[0] == "rejected"
    assert "Only skipped signals are left" in r.text
    r = c.get("/review/next")  # start over: the skipped one comes back
    assert _shown(r.text) == first and "1 remaining" in r.text
    r = c.post(f"/review/next/{first}", data={"decision": "bogus"}, follow_redirects=False)
    assert "err=" in r.headers["location"]


# ---------------- public safety ----------------
def test_public_mode_hides_admin_pages(monkeypatch):
    from radar import settings as S

    _write_yaml()
    monkeypatch.setenv("RADAR_PUBLIC_MODE", "true")
    S.get_settings.cache_clear()
    try:
        c = _client()
        for path in ("/tasks", "/review", "/review/next", "/review?mode=focus"):
            assert c.get(path).status_code == 404, path
        assert c.post("/tasks/canary-minutes-oct/upload", files={"file": ("x", b"y")}).status_code == 403
        assert c.post("/review/next/x", data={"decision": "approved"}).status_code == 403
        home = c.get("/").text
        assert 'href="/tasks"' not in home and 'href="/review' not in home
    finally:
        monkeypatch.delenv("RADAR_PUBLIC_MODE")
        S.get_settings.cache_clear()


def test_static_export_has_no_admin_pages(tmp_path):
    from radar import static_export
    from radar.wall import static_site

    _write_yaml()
    _signals(1)
    with db.connect() as con:
        con.execute("UPDATE signals SET review_status = 'auto_approved'")
    out = tmp_path / "dist"
    static_export.export(out)
    names = [str(f.relative_to(out)) for f in out.rglob("*")]
    for bad in ("task", "review", "export", "admin", "manual", "records", "registry"):
        assert not any(bad in n.lower() for n in names), (bad, [n for n in names if bad in n.lower()])
    for f in out.rglob("*.html"):
        text = f.read_text()
        assert not re.search(r'(href|action)="[^"]*(tasks|review|export\.csv|registry|records)', text), f
    assert static_site.check(out) == []
    with pytest.raises(static_export.ExportError):
        static_export.out_path("/tasks")
    with pytest.raises(static_export.ExportError):
        static_export.out_path("/review/next")
