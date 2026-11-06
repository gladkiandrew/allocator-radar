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
