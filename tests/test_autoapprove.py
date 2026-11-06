from datetime import date

from radar import autoapprove, db, export
from radar.extract import extractor as X
from tests.test_extractor import FakeProvider, _setup


def _sig(stype="RFP_ISSUED", quote="The Board voted to issue a Request for Proposals for a hedge fund of funds manager.",
         summary="The Board voted to issue an RFP for a hedge fund of funds manager.", d="2026-01-15"):
    sha = _setup()
    res = X.XResult(signals=[X.XSignal(signal_type=stype, qualifier="manager", event_date=d, summary=summary, page=1,
                                       quote=quote, confidence=0.9)], mandates=[], officers=[], consultants=[])
    X.extract_document(sha, "canary", provider=FakeProvider(res))
    with db.connect() as con:
        return con.execute("SELECT signal_id FROM signals").fetchone()[0]


def _status(sid):
    with db.connect() as con:
        return con.execute("SELECT review_status, date_basis FROM signals WHERE signal_id = ?", [sid]).fetchone()


def test_eligible_signal_auto_approved_and_never_exported():
    sid = _sig()  # date 2026-01-15 is printed on the canary page ("January 15, 2026")
    st = autoapprove.run_pass()
    assert st["auto_approved"] == 1 and _status(sid)[0] == "auto_approved" and _status(sid)[1] in ("document_date", "printed_on_page")
    text, n = export.build_csv("owner")
    assert n == 0  # export needs a person's approval
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM audit_log WHERE action = 'signal_auto_approved' AND detail LIKE '%auto-approve-v1%'").fetchone()[0] == 1


def test_excluded_type_stays_pending():
    sid = _sig(stype="MANAGER_TERMINATED")
    autoapprove.run_pass()
    assert _status(sid)[0] == "new"


def test_date_not_printed_stays_pending():
    sid = _sig(d="2026-01-10")  # not printed anywhere on the page, not the document date
    autoapprove.run_pass()
    assert _status(sid)[0] == "new"


def test_readiness_counts_auto_approved_and_new_preparing_rules():
    from datetime import datetime, timedelta, timezone

    from radar.readiness import compute
    today = date(2026, 11, 6)
    now = datetime.now(timezone.utc)
    s = lambda t, days, q=None, st="auto_approved": {"signal_type": t, "qualifier": q, "event_date": today - timedelta(days=days), "review_status": st}
    assert compute([s("MANDATE_AWARDED", 30, "manager")], now, today).tier == "DEPLOYING"
    assert compute([s("MANDATE_AWARDED", 130, "manager")], now, today).tier == "PREPARING"
    assert compute([s("COMMITMENT_MADE", 170)], now, today).tier == "PREPARING"
    assert compute([s("COMMITMENT_MADE", 190)], now, today).tier == "WATCHING"
    assert compute([s("MANDATE_AWARDED", 30, "service_provider")], now, today).tier == "WATCHING"
