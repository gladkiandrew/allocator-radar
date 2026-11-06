"""Wall layer 1: schema. No contact-shaped column or model field can exist."""
from radar import db
from radar.wall import live


def test_no_banned_columns():
    with db.connect() as con:
        assert live.check_schema(con) == []


def test_models_forbid_extra_and_have_no_contact_fields():
    assert live.check_models() == []


def test_officer_model_rejects_contact_fields():
    import pytest
    from pydantic import ValidationError

    from radar.models import Evidence, Officer

    ev = Evidence(document_sha256="0" * 64, source_url="https://x.test/a.pdf", page=1, quote="q",
                  extractor="t", extractor_version="0", confidence=1)
    with pytest.raises(ValidationError):
        Officer(allocator_id="a", name_as_printed="N", title_as_printed="Chair", role_type="CHAIR",
                as_of="2026-01-01", provenance=[ev], email="n@x.test")


def test_signal_requires_provenance():
    import pytest
    from pydantic import ValidationError

    from radar.models import Signal

    with pytest.raises(ValidationError):
        Signal(allocator_id="a", signal_type="RFP_ISSUED", event_date="2026-01-01", summary="s", provenance=[])
