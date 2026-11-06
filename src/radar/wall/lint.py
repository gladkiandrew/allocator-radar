"""Language lint (AGENDA §4.7). No evaluative language anywhere in stored or shown text.

Applied to every signal summary (machine or manual), every LLM-written brief line, and
every manual fact. Failing text is rejected (never stored); callers may rewrite and retry.
Strictness depends on config/firm_posture.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from radar.settings import firm_posture

BASE_BANNED = [
    "outperform", "outperformed", "outperforming", "outperformance",
    "underperform", "underperformed", "underperforming", "underperformance",
    "strong", "stronger", "strongest", "weak", "weaker", "weakest", "poor", "poorly",
    "impressive", "disappoint", "disappointing", "disappointed",
    "top", "best", "worst", "better", "worse",
    "laggard", "laggards", "leader", "leaders", "attractive", "concern", "concerning", "concerns",
]

# Added under ria / cpo_cta / both: predictive and promotional words.
STRICT_BANNED = [
    "likely", "unlikely", "expected to", "expect", "poised", "ready to", "eager",
    "opportunity", "opportunities", "hot", "warm", "cold", "promising", "excellent",
    "great", "superior", "inferior", "guarantee", "guaranteed", "should", "must-see",
    "lead", "prospect", "prospects", "win", "winning", "pitch",
]


@dataclass(frozen=True)
class LintResult:
    ok: bool
    banned_found: list[str]


def banned_terms(posture: str | None = None) -> list[str]:
    posture = posture or firm_posture()
    return BASE_BANNED + (STRICT_BANNED if posture != "none" else [])


def lint(text: str, posture: str | None = None) -> LintResult:
    found = []
    low = text.lower()
    for term in banned_terms(posture):
        if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", low):
            found.append(term)
    return LintResult(ok=not found, banned_found=found)


class LintError(ValueError):
    pass


def require_clean(text: str, posture: str | None = None) -> str:
    res = lint(text, posture)
    if not res.ok:
        raise LintError(f"evaluative language not allowed: {', '.join(res.banned_found)}")
    return text
