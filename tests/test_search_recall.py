"""Recall of the page prefilter and the search-scan vocabulary on real passages (D-B16). No LLM, no network."""

from pathlib import Path

import pytest
import yaml

from radar.extract.extractor import SEARCH_VOCAB
from radar.extract.prefilter import is_hit, match_terms, score

CASES = yaml.safe_load((Path(__file__).parent / "fixtures" / "search_passages.yaml").read_text())


@pytest.mark.parametrize("case", CASES["positive"], ids=lambda c: c["id"])
def test_positive_passes_prefilter(case):
    terms = match_terms(case["text"])
    assert is_hit(terms), f"score {score(terms)} for {terms}"


@pytest.mark.parametrize("case", CASES["positive"], ids=lambda c: c["id"])
def test_positive_selected_by_search_scan(case):
    assert SEARCH_VOCAB.search(case["text"])


@pytest.mark.parametrize("case", CASES["negative"], ids=lambda c: c["id"])
def test_negative_does_not_pass_prefilter(case):
    terms = match_terms(case["text"])
    assert not is_hit(terms), f"score {score(terms)} for {terms}"
