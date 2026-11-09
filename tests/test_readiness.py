from datetime import date, datetime, timedelta, timezone

from radar.readiness import compute

TODAY = date(2026, 11, 6)
NOW = datetime.now(timezone.utc)


def sig(t, days_ago, status="approved", q="manager"):
    return {"signal_type": t, "event_date": TODAY - timedelta(days=days_ago), "review_status": status, "qualifier": q}


def test_deploying_within_90():
    assert compute([sig("RFP_ISSUED", 30)], NOW, TODAY).tier == "DEPLOYING"


def test_deploying_expires_after_90():
    assert compute([sig("RFP_ISSUED", 91)], NOW, TODAY).tier == "WATCHING"


def test_unapproved_never_counts():
    assert compute([sig("RFP_ISSUED", 3, status="new")], NOW, TODAY).tier == "WATCHING"


def test_preparing_and_qualifier():
    assert compute([sig("TARGET_ALLOCATION_CHANGED", 10, q="increase")], NOW, TODAY).tier == "PREPARING"
    assert compute([sig("TARGET_ALLOCATION_CHANGED", 10, q="decrease")], NOW, TODAY).tier == "WATCHING"
    assert compute([sig("MANAGER_ON_WATCH", 170)], NOW, TODAY).tier == "PREPARING"


def test_deploying_beats_preparing():
    r = compute([sig("IPS_AMENDED", 5), sig("FINALISTS_NAMED", 40)], NOW, TODAY)
    assert r.tier == "DEPLOYING" and r.reasons[0]["signal_type"] == "FINALISTS_NAMED"


def test_stale_when_no_new_documents():
    assert compute([], NOW - timedelta(days=121), TODAY).tier == "STALE"
    assert compute([], None, TODAY).tier == "STALE"


def test_future_dated_signal_ignored():
    assert compute([sig("RFP_ISSUED", -5)], NOW, TODAY).tier == "WATCHING"


def test_service_provider_rfp_does_not_deploy():
    assert compute([sig("RFP_ISSUED", 5, q="service_provider")], NOW, TODAY).tier == "WATCHING"
    assert compute([sig("RFP_ISSUED", 5, q="consultant")], NOW, TODAY).tier == "WATCHING"


def test_search_scheduled_deploys_for_manager_only():
    assert compute([sig("SEARCH_SCHEDULED", 20)], NOW, TODAY).tier == "DEPLOYING"
    assert compute([sig("SEARCH_SCHEDULED", 20, q="service_provider")], NOW, TODAY).tier == "WATCHING"
    assert compute([sig("SEARCH_SCHEDULED", 95)], NOW, TODAY).tier == "WATCHING"


def test_search_scheduled_upcoming_meeting_counts_now():
    assert compute([sig("SEARCH_SCHEDULED", -10)], NOW, TODAY).tier == "DEPLOYING"   # meeting in 10 days
    assert compute([sig("SEARCH_SCHEDULED", -120)], NOW, TODAY).tier == "WATCHING"   # too far ahead
    assert compute([sig("RFP_ISSUED", -5)], NOW, TODAY).tier == "WATCHING"           # other types: no future dates


def test_config_deploying_qualifiers_intact():
    from radar.settings import readiness_config
    dep = readiness_config()["tiers"][0]
    for t in ("SEARCH_OPENED", "SEARCH_SCHEDULED", "RFP_ISSUED", "FINALISTS_NAMED", "MANDATE_AWARDED"):
        assert dep["qualifiers"][t] == ["manager"], t
    assert all(isinstance(v, int) for v in dep.get("future_days", {}).values())
