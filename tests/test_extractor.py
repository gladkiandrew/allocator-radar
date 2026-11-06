"""Extractor validators with a fake provider: citations must be faithful, lint must pass."""
from radar import db, ingest
from radar.extract import extractor as X
from radar.llm.provider import CallMeta
from radar.parse import pdf
from radar.wall.canary import canary_pdf


class FakeProvider:
    backend, model = "fake", "fake-1"

    def __init__(self, result):
        self.result = result
        self.prompts = []

    def complete(self, *, system, user, schema, purpose):
        self.prompts.append(user)
        return self.result, CallMeta("fake", "fake-1", "p" * 64, "r" * 64, 1)


def _setup():
    with db.connect() as con:
        con.execute("""INSERT INTO allocators (allocator_id, legal_name, display_name, allocator_type, jurisdiction_city,
                       jurisdiction_state, home_url, template, access, updated_at)
                       VALUES ('canary', 'Canary P&F', 'Canary P&F', 'POLICE_FIRE', 'Miami', 'FL', 'https://c.invalid', 'T-WP', 'AUTO', now())""")
    sha, _ = ingest.store_document(canary_pdf(), url="https://canary.invalid/a.pdf", allocator_id="canary",
                                   content_type="application/pdf", title="Agenda January 15, 2026", doc_type="AGENDA")
    pdf.parse_document(sha)
    return sha


def test_faithful_signal_stored_and_bad_ones_dropped():
    sha = _setup()
    res = X.XResult(signals=[
        X.XSignal(signal_type="RFP_ISSUED", qualifier="manager", event_date="2026-01-15",
                  summary="The Board voted to issue an RFP for a hedge fund of funds manager.", page=1,
                  quote="The Board voted to issue a Request for Proposals for a hedge fund of funds manager.", confidence=0.9),
        X.XSignal(signal_type="MANAGER_TERMINATED", qualifier="none", event_date="2026-01-15",
                  summary="The Board terminated Acme.", page=1, quote="The Board terminated Acme Capital effective today.",
                  confidence=0.9),  # quote not on page -> dropped
        X.XSignal(signal_type="IPS_AMENDED", qualifier="none", event_date="2026-01-15",
                  summary="The Board approved a strong new IPS.", page=1,
                  quote="motion carried unanimously", confidence=0.9),  # lint -> dropped
        X.XSignal(signal_type="RFP_ISSUED", qualifier="manager", event_date="2099-01-01",
                  summary="Future.", page=1, quote="The Board voted to issue a Request for Proposals", confidence=0.9),
    ], mandates=[], officers=[], consultants=[])
    fake = FakeProvider(res)
    st = X.extract_document(sha, "canary", provider=fake)
    assert st["signals"] == 1 and st["dropped"] == 3
    assert "canary.trustee@example.invalid" not in fake.prompts[0]
    with db.connect() as con:
        sigs = db.rows(con, "SELECT * FROM signals")
    assert sigs[0]["review_status"] == "new" and sigs[0]["qualifier"] == "manager"
    # second run: nothing left to extract
    assert X.extract_document(sha, "canary", provider=fake)["calls"] == 0


def test_quote_matching():
    assert X.quote_on_page("the board voted to  issue", "The Board\nvoted to issue an RFP")
    assert not X.quote_on_page("the board voted to fire", "The Board voted to issue an RFP")


def test_support_check():
    assert X.supports("The Board approved the engagement letter with CBIZ CPAs for audit services.",
                      "approved the engagement letter with CBIZ CPAs", "Canary P&F")
    assert not X.supports("The Board approved the engagement letter with CBIZ CPAs for audit services.",
                          "The Board agreed to extend the contract for legal services with Rossman", "Canary P&F")
    assert X.supports("The Board voted to issue an RFP for a hedge fund of funds manager.",
                      "The Board voted to issue a Request for Proposals", "Canary P&F")
    assert X.supports("transferring $3 million to the R&D cash account", "transfer $3 million to R&D", "x")
