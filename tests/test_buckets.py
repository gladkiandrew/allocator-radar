from radar.buckets import hf_relevant, to_bucket


def test_asset_class_decides_first():
    assert to_bucket("Private Equity", "Growth Equity", "QEP Quantitative Partners") == "PRIVATE_EQUITY"
    assert to_bucket("Real Estate", "Opportunistic", "Starwood Distressed Opportunity Fund XIII") == "REAL_ESTATE"
    assert to_bucket("Active Credit", "Direct Lending", "Sunrise Credit Partners LP") == "PRIVATE_CREDIT"
    assert to_bucket("Active Credit", "High Yield", "Apalachee Partners US Dynamic HY") == "LONG_ONLY_FIXED_INCOME"


def test_strategic_investments_refined_by_style_and_name():
    assert to_bucket("Strategic Investments", "Hedge Fund", "Brevan Howard Master Fund") == "MULTI_STRATEGY"
    assert to_bucket("Strategic Investments", "Insurance", "Miravast ILS Credit Opportunities III LP") == "INSURANCE_LINKED"
    assert to_bucket("Hedge Funds 2.0", None, "AQR Managed Futures Fund II LP") == "MANAGED_FUTURES_CTA"


def test_hf_flag():
    assert hf_relevant("MULTI_STRATEGY", "Strategic Investments", "Hedge Fund")
    assert not hf_relevant("INSURANCE_LINKED", "Strategic Investments", "Insurance")
    assert not hf_relevant("REAL_ESTATE", "Real Estate", "Opportunistic")


def test_signal_buckets_d_b30():
    from radar.buckets import signal_bucket, to_bucket
    assert to_bucket("Equity", "large cap growth equity", None) == "LONG_ONLY_EQUITY"
    assert signal_bucket("SEARCH_OPENED", "manager", "x", "Large-Cap Growth Equity manager search")[0] == "LONG_ONLY_EQUITY"
    assert signal_bucket("MANDATE_AWARDED", "service_provider", "auditor hired", "audit RFP") == ("SERVICE_PROVIDER", None)
    assert signal_bucket("REBALANCE_APPROVED", None, "rebalance to targets", "rebalancing") == ("TOTAL_PLAN", None)
    assert signal_bucket("COMMITMENT_MADE", None, "x", "$5 million to ABC Real Estate Fund, from fixed income")[0] == "REAL_ESTATE"
    assert signal_bucket("SEARCH_SCHEDULED", "manager", "Memo re: Bumble", "Memo re: Bumble")[0] == "OTHER"
    assert signal_bucket("MANDATE_FUNDED", None, "x", "iShares Bitcoin Trust (IBIT)")[0] == "DIGITAL_ASSETS"
    assert signal_bucket("SEARCH_OPENED", "manager", "x", "Core Plus fixed income search")[0] == "LONG_ONLY_FIXED_INCOME"
    assert signal_bucket("SEARCH_OPENED", "manager", "board insurance renewals", "Board Insurance Renewals")[0] != "INSURANCE_LINKED"
