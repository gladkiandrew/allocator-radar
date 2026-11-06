"""Wall layer 2: the scrubber removes every contact shape and keeps names and facts."""
from radar.wall.canary import CANARY_LINES
from radar.wall.live import CANARY_TOKENS
from radar.wall.scrubber import REDACTED, detect, scrub


def test_canary_lines_fully_scrubbed():
    out = scrub("\n".join(CANARY_LINES), use_gliner=False).text
    for tok in CANARY_TOKENS:
        assert tok.lower() not in out.lower(), tok
    assert detect(out) == []
    assert REDACTED in out


def test_facts_and_names_survive():
    txt = ("Trustee Maria Lopez moved to issue an RFP for a $25,000,000 hedge fund mandate. "
           "Target allocation to alternatives raised from 10% to 15% on 2026-03-12. Item 4.b, page 3.")
    assert scrub(txt, use_gliner=False).text == txt


def test_phone_variants():
    for p in ["(305) 555-0100", "305-555-0100", "305.555.0100", "+1 305 555 0100", "Tel: 305 5550100",
              "954-555-0100 ext. 4"]:
        assert detect(f"call {p} now"), p


def test_money_and_dates_not_flagged():
    for s in ["$1,234,567.89", "2025-2026", "12/31/2025", "FY 2025", "3,456,789", "Account 12345678",
              "June 30, 2026", "returns of 7.25% vs 6.90%"]:
        assert detect(s) == [], s


def test_social_and_mailto():
    assert detect("see https://linkedin.com/in/someone")
    assert detect("mailto:someone@example.invalid")


import os

import pytest


@pytest.mark.skipif(os.environ.get("RADAR_TEST_GLINER") != "1", reason="slow; nightly runs it")
def test_gliner_pass_on_canary():
    out = scrub("\n".join(CANARY_LINES), use_gliner=True).text
    for tok in CANARY_TOKENS:
        assert tok.lower() not in out.lower(), tok
    assert "Request for Proposals" in out
