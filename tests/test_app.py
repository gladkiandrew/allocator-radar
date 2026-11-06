from fastapi.testclient import TestClient

from radar.web.app import create_app


def test_empty_table_renders():
    c = TestClient(create_app())
    r = c.get("/")
    assert r.status_code == 200 and "Florida allocators" in r.text
    assert c.get("/how").status_code == 200


def test_basic_auth_required_when_enabled(monkeypatch):
    import hashlib

    from radar import settings as S
    monkeypatch.setenv("RADAR_AUTH_MODE", "basic")
    monkeypatch.setenv("RADAR_AUTH_PASSWORD_HASH", hashlib.sha256(b"s3cret").hexdigest())
    S.get_settings.cache_clear()
    c = TestClient(create_app())
    assert c.get("/").status_code == 401
    assert c.get("/", auth=("owner", "wrong")).status_code == 401
    assert c.get("/", auth=("owner", "s3cret")).status_code == 200
    assert c.get("/doc/" + "0" * 64).status_code == 401


def test_strategy_filter_and_row_fields():
    from radar import db
    from tests.test_extractor import FakeProvider, _setup
    from radar.extract import extractor as X
    sha = _setup()
    res = X.XResult(signals=[X.XSignal(signal_type="RFP_ISSUED", qualifier="manager", event_date="2026-01-15",
        summary="The Board voted to issue an RFP for a hedge fund of funds manager.", page=1,
        quote="The Board voted to issue a Request for Proposals for a hedge fund of funds manager.", confidence=0.9)],
        mandates=[], officers=[], consultants=[])
    X.extract_document(sha, "canary", provider=FakeProvider(res))
    with db.connect() as con:
        con.execute("UPDATE signals SET review_status='auto_approved', bucket='FUND_OF_HEDGE_FUNDS', hf_relevant=true, date_basis='document_date'")
    c = TestClient(create_app())
    assert c.get("/?strategy=hf").status_code == 200 and c.get("/?strategy=REAL_ESTATE").status_code == 200
