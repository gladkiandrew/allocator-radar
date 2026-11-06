from datetime import date

import pytest

from radar import db, export, manual, review
from radar.models import ManualFact
from tests.test_extractor import _setup


def _fact(**kw):
    base = dict(allocator_id="canary", source_id="C5", source_url="https://www.dahab.com/rfp",
                event_date="2026-10-01", signal_type="RFP_ISSUED", qualifier="manager",
                fact="Dahab Associates posted an RFP for a hedge fund of funds manager on behalf of the plan.")
    base.update(kw)
    return ManualFact(**base)


def test_manual_fact_goes_to_queue_and_export_only_after_approval():
    _setup()
    sid = manual.add_fact(_fact(), "owner")
    text, n = export.build_csv("owner")
    assert n == 0 and "hedge fund of funds" not in text
    review.decide(sid, "approved", "owner")
    text, n = export.build_csv("owner")
    assert n == 1 and "https://www.dahab.com/rfp" in text and "206(4)-1" in text and "2-29" in text
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM export_log").fetchone()[0] == 2


def test_manual_fact_rejects_evaluative_contact_and_hard_reject():
    _setup()
    with pytest.raises(manual.ManualError):
        manual.add_fact(_fact(fact="A strong RFP."), "owner")
    with pytest.raises(manual.ManualError):
        manual.add_fact(_fact(fact="RFP issued; questions to 305-555-0100."), "owner")
    with pytest.raises(manual.ManualError):
        manual.add_fact(_fact(source_url="https://www.preqin.com/x"), "owner")


def test_records_request_tracks_and_ingests(tmp_path):
    _setup()
    with pytest.raises(manual.ManualError):
        manual.add_request("canary", "Send to me at someone@example.invalid", date(2026, 11, 1), "owner")
    rid = manual.add_request("canary", "Copies of the 2026 investment consultant RFP responses.", date(2026, 11, 1), "owner")
    from radar.wall.canary import canary_pdf
    (manual.records_dir(rid) / "response.pdf").write_bytes(canary_pdf())
    st = manual.ingest_request(rid, "owner")
    assert st["files"] == 1
    with db.connect() as con:
        assert con.execute("SELECT provenance FROM documents ORDER BY first_seen DESC LIMIT 1").fetchone()[0] in ("RECORDS_REQUEST", "CRAWL")
        from radar.wall import live
        assert live.check_sweep(con, live.CANARY_TOKENS) == []
