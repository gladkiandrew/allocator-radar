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
        assert con.execute("SELECT count(*) FROM audit_log WHERE action = 'signal_auto_approved' AND detail LIKE '%auto-approve-v2%'").fetchone()[0] == 1


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


def test_non_matching_quote_refused():
    sid = _sig()
    with db.connect() as con:  # corrupt the stored quote so it no longer matches the cited page
        con.execute("UPDATE evidence SET quote = 'The Board voted to hire a new manager for the hedge fund portfolio.'")
    autoapprove.run_pass()
    assert _status(sid)[0] == "new"


def _table_like(extractor, basis):
    sid = _sig(d="2026-01-10")  # date not printed on the page
    with db.connect() as con:
        con.execute("UPDATE evidence SET extractor = ?", [extractor])
        con.execute("UPDATE signals SET date_basis = ?", [basis])
    autoapprove.run_pass()
    return _status(sid)[0]


def test_quarter_end_only_for_table_rows():
    assert _table_like("sba-table-parser", "quarter_end") == "auto_approved"


def test_quarter_end_refused_for_llm_rows():
    assert _table_like("page-extractor", "quarter_end") == "new"


def test_table_row_high_impact_still_refused():
    sid = _sig(stype="REDEMPTION_APPROVED", d="2026-01-10")
    with db.connect() as con:
        con.execute("UPDATE evidence SET extractor = 'sba-table-parser'")
        con.execute("UPDATE signals SET date_basis = 'quarter_end'")
    autoapprove.run_pass()
    assert _status(sid)[0] == "new"


def test_export_refuses_auto_approved_even_when_many():
    _sig()
    autoapprove.run_pass()
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM signals WHERE review_status = 'auto_approved'").fetchone()[0] == 1
    text, n = export.build_csv("owner")
    assert n == 0 and "auto_approved" not in text


def test_table_row_fund_name_not_linted_but_our_words_are():
    sid = _sig(d="2026-01-10")
    with db.connect() as con:
        con.execute("UPDATE signals SET summary = 'The SBA reported a commitment to Credit Opportunities III LP.'")
        con.execute("UPDATE evidence SET extractor = 'sba-table-parser', quote = 'Strategic Investments | Credit Opportunities III LP | $1'")
        con.execute("UPDATE signals SET date_basis = 'quarter_end'")
    autoapprove.run_pass()
    assert _status(sid)[0] == "auto_approved"

    with db.connect() as con:  # our own evaluative wording is still refused
        con.execute("UPDATE signals SET review_status = 'new', summary = 'The SBA made an attractive commitment to Credit Opportunities III LP.'")
    autoapprove.run_pass()
    assert _status(sid)[0] == "new"
