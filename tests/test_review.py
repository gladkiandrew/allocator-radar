from fastapi.testclient import TestClient

from radar import db, review
from tests.test_extractor import FakeProvider, _setup
from radar.extract import extractor as X


def _one_signal():
    sha = _setup()
    res = X.XResult(signals=[X.XSignal(signal_type="RFP_ISSUED", qualifier="manager", event_date="2026-01-15",
        summary="The Board voted to issue an RFP for a hedge fund of funds manager.", page=1,
        quote="The Board voted to issue a Request for Proposals for a hedge fund of funds manager.", confidence=0.9)],
        mandates=[], officers=[], consultants=[])
    X.extract_document(sha, "canary", provider=FakeProvider(res))
    with db.connect() as con:
        return con.execute("SELECT signal_id FROM signals").fetchone()[0]


def test_approve_flow_and_lint_on_edit():
    import pytest
    sid = _one_signal()
    with pytest.raises(review.ReviewError):
        review.decide(sid, "approved", "owner", summary="A strong signal.")
    s = review.decide(sid, "approved", "owner")
    assert s["review_status"] == "approved" and s["reviewed_by"] == "owner"
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM audit_log WHERE action = 'signal_approved'").fetchone()[0] == 1


def test_review_page_and_post():
    from radar.web.app import create_app
    sid = _one_signal()
    c = TestClient(create_app())
    assert "pending review" in c.get("/review").text
    r = c.post(f"/review/{sid}", data={"decision": "approved"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "approved" in r.text
    gaps = c.get("/gaps").text  # one document only: below the main-table bar
    assert "Canary P&amp;F" in gaps and "documents fetched (minimum 3)" in gaps
    assert "Coverage" in c.get("/a/canary").text


def test_auto_approve_policy_stamps_policy_actor():
    from radar.settings import get_settings
    (get_settings().config_dir / "review_policy.yaml").write_text(
        "signals: {auto_approve: true, actor: 'auto-approve (owner policy test)'}\n")
    sid = _one_signal()
    with db.connect() as con:
        st, by = con.execute("SELECT review_status, reviewed_by FROM signals WHERE signal_id = ?", [sid]).fetchone()
    assert st == "approved" and by == "auto-approve (owner policy test)"


def test_integrity_removes_orphans_but_not_human_decisions():
    sid = _one_signal()
    review.decide(sid, "approved", "owner")
    with db.connect() as con:
        con.execute("DELETE FROM document_urls")
    assert review.integrity_cleanup() == []  # a person approved it: never touched by code
    with db.connect() as con:
        con.execute("UPDATE signals SET reviewed_by = NULL, review_status = 'new'")
    assert len(review.integrity_cleanup()) == 1
