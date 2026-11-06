"""Readiness tier: derived from APPROVED signals by config/readiness_rules.yaml.

Never a score. Every tier carries the signals (and so the documents) that produced it,
or, for WATCHING/STALE, the coverage fact that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from radar.settings import readiness_config

TIER_ORDER = ["DEPLOYING", "PREPARING", "WATCHING", "STALE"]


@dataclass
class Readiness:
    tier: str
    reasons: list[dict] = field(default_factory=list)  # qualifying approved signals
    rule_meaning: str = ""
    last_new_document: datetime | None = None
    coverage_stale: bool = False


def _matches(rule: dict, sig: dict) -> bool:
    if sig["signal_type"] not in rule["signal_types"]:
        return False
    quals = (rule.get("qualifiers") or {}).get(sig["signal_type"])
    if quals is not None and (sig.get("qualifier") or "").lower() not in quals:
        return False
    return True


def compute(signals: list[dict], last_new_document: datetime | None, today: date | None = None,
            cfg: dict | None = None) -> Readiness:
    cfg = cfg or readiness_config()
    today = today or date.today()
    stale_days = cfg["coverage"]["stale_after_days"]
    now = datetime.now(timezone.utc)
    coverage_stale = last_new_document is None or (now - last_new_document) > timedelta(days=stale_days)

    approved = [s for s in signals if s["review_status"] == "approved"]
    for rule in cfg["tiers"]:
        window_start = today - timedelta(days=rule["window_days"])
        hits = [s for s in approved if _matches(rule, s) and window_start <= s["event_date"] <= today]
        if hits:
            hits.sort(key=lambda s: s["event_date"], reverse=True)
            return Readiness(rule["tier"], hits, rule["meaning"].strip(), last_new_document, coverage_stale)

    if coverage_stale:
        return Readiness(cfg["stale_tier"], [], cfg["coverage"]["meaning"], last_new_document, True)
    return Readiness(cfg["fallback_tier"], [], "Documents current; no qualifying approved signal in window.",
                     last_new_document, False)
