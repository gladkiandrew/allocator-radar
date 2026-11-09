"""Pydantic v2 models. AGENDA §4.2, trimmed to what Phase A uses.

THE WALL, schema layer: no model has an email, phone, street address, person URL,
social handle, home location, DOB, or personal-ID field. Every model forbids extra
fields, so nothing can be smuggled in. tests/wall/test_schema.py enforces this.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AllocatorType(StrEnum):
    PUBLIC_PENSION_STATE = "PUBLIC_PENSION_STATE"
    PUBLIC_PENSION_LOCAL = "PUBLIC_PENSION_LOCAL"
    POLICE_FIRE = "POLICE_FIRE"
    TEACHERS = "TEACHERS"
    CORPORATE_DB = "CORPORATE_DB"
    TAFT_HARTLEY = "TAFT_HARTLEY"
    ENDOWMENT = "ENDOWMENT"
    PRIVATE_FOUNDATION = "PRIVATE_FOUNDATION"
    COMMUNITY_FOUNDATION = "COMMUNITY_FOUNDATION"
    HOSPITAL = "HOSPITAL"
    OTHER = "OTHER"


class SignalType(StrEnum):
    SEARCH_OPENED = "SEARCH_OPENED"
    SEARCH_SCHEDULED = "SEARCH_SCHEDULED"  # an agenda schedules manager/consultant interviews, presentations or an RFP review
    RFP_ISSUED = "RFP_ISSUED"
    FINALISTS_NAMED = "FINALISTS_NAMED"
    MANDATE_AWARDED = "MANDATE_AWARDED"
    MANDATE_FUNDED = "MANDATE_FUNDED"
    MANAGER_TERMINATED = "MANAGER_TERMINATED"
    MANAGER_ON_WATCH = "MANAGER_ON_WATCH"
    WATCH_REMOVED = "WATCH_REMOVED"
    REDEMPTION_APPROVED = "REDEMPTION_APPROVED"
    COMMITMENT_MADE = "COMMITMENT_MADE"
    TARGET_ALLOCATION_CHANGED = "TARGET_ALLOCATION_CHANGED"
    IPS_AMENDED = "IPS_AMENDED"
    ASSET_LIABILITY_STUDY = "ASSET_LIABILITY_STUDY"
    PACING_PLAN_ADOPTED = "PACING_PLAN_ADOPTED"
    CONSULTANT_CHANGED = "CONSULTANT_CHANGED"
    CIO_CHANGED = "CIO_CHANGED"
    OFFICER_CHANGED = "OFFICER_CHANGED"
    REBALANCE_APPROVED = "REBALANCE_APPROVED"
    FEE_RENEGOTIATED = "FEE_RENEGOTIATED"
    PERFORMANCE_DISCUSSED = "PERFORMANCE_DISCUSSED"
    OTHER = "OTHER"


class StrategyBucket(StrEnum):
    EQUITY_LONG_SHORT = "EQUITY_LONG_SHORT"
    EQUITY_MARKET_NEUTRAL = "EQUITY_MARKET_NEUTRAL"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    MERGER_ARB = "MERGER_ARB"
    DISTRESSED = "DISTRESSED"
    CREDIT_LONG_SHORT = "CREDIT_LONG_SHORT"
    STRUCTURED_CREDIT = "STRUCTURED_CREDIT"
    CONVERTIBLE_ARB = "CONVERTIBLE_ARB"
    FIXED_INCOME_RV = "FIXED_INCOME_RV"
    GLOBAL_MACRO = "GLOBAL_MACRO"
    MANAGED_FUTURES_CTA = "MANAGED_FUTURES_CTA"
    MULTI_STRATEGY = "MULTI_STRATEGY"
    FUND_OF_HEDGE_FUNDS = "FUND_OF_HEDGE_FUNDS"
    QUANT_SYSTEMATIC = "QUANT_SYSTEMATIC"
    VOLATILITY = "VOLATILITY"
    INSURANCE_LINKED = "INSURANCE_LINKED"
    COMMODITIES = "COMMODITIES"
    DIGITAL_ASSETS = "DIGITAL_ASSETS"
    PRIVATE_CREDIT = "PRIVATE_CREDIT"
    PRIVATE_EQUITY = "PRIVATE_EQUITY"
    VENTURE = "VENTURE"
    REAL_ESTATE = "REAL_ESTATE"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    NATURAL_RESOURCES = "NATURAL_RESOURCES"
    LONG_ONLY_EQUITY = "LONG_ONLY_EQUITY"
    LONG_ONLY_FIXED_INCOME = "LONG_ONLY_FIXED_INCOME"
    CASH_OVERLAY = "CASH_OVERLAY"
    OTHER = "OTHER"


class MandateStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ON_WATCH = "ON_WATCH"
    TERMINATED = "TERMINATED"
    PENDING_FUNDING = "PENDING_FUNDING"
    REDEEMING = "REDEEMING"
    UNKNOWN = "UNKNOWN"


class RoleType(StrEnum):
    TRUSTEE = "TRUSTEE"
    CHAIR = "CHAIR"
    CIO = "CIO"
    EXEC_DIRECTOR = "EXEC_DIRECTOR"
    STAFF = "STAFF"
    CONSULTANT_REP = "CONSULTANT_REP"
    ACTUARY = "ACTUARY"
    AUDITOR = "AUDITOR"
    COUNSEL = "COUNSEL"
    OTHER = "OTHER"


Provenance = Literal["CRAWL", "MANUAL", "RECORDS_REQUEST"]
ReviewStatus = Literal["new", "approved", "auto_approved", "rejected"]  # approved = a person; auto_approved = rules


class Evidence(Strict):
    evidence_id: str = Field(default_factory=new_id)
    document_sha256: str | None  # None only for MANUAL facts that cite a URL we never fetch
    source_url: str
    page: int | None = None  # 1-indexed PDF page
    quote: str = Field(max_length=500)  # scrubbed text, verbatim from the page
    extractor: str
    extractor_version: str
    prompt_hash: str | None = None
    extracted_at: datetime = Field(default_factory=utcnow)
    confidence: float = Field(ge=0, le=1)
    provenance: Provenance = "CRAWL"


class Allocator(Strict):
    allocator_id: str
    legal_name: str
    display_name: str
    allocator_type: AllocatorType
    jurisdiction_city: str | None = None
    jurisdiction_state: str | None = "FL"
    jurisdiction_country: str = "US"
    fiscal_year_end_month: int | None = Field(default=None, ge=1, le=12)


class Signal(Strict):
    signal_id: str = Field(default_factory=new_id)
    allocator_id: str
    signal_type: SignalType
    qualifier: str | None = None  # e.g. "increase" / "decrease" for TARGET_ALLOCATION_CHANGED
    event_date: date  # source-native date, never the fetch date
    observed_at: datetime = Field(default_factory=utcnow)
    summary: str = Field(max_length=300)  # factual, lint-checked
    review_status: ReviewStatus = "new"
    origin: Literal["MACHINE", "MANUAL"] = "MACHINE"
    provenance: list[Evidence] = Field(min_length=1)


class Officer(Strict):  # person-as-role ONLY
    officer_id: str = Field(default_factory=new_id)
    allocator_id: str
    name_as_printed: str
    title_as_printed: str
    role_type: RoleType
    as_of: date
    provenance: list[Evidence] = Field(min_length=1)


class Mandate(Strict):
    mandate_id: str = Field(default_factory=new_id)
    allocator_id: str
    manager_name_as_disclosed: str
    bucket_label_as_disclosed: str
    bucket: StrategyBucket = StrategyBucket.OTHER
    amount_usd_as_disclosed: float | None = None
    amount_kind: Literal["market_value", "commitment", "target_pct", "actual_pct"] | None = None
    as_of: date
    status: MandateStatus = MandateStatus.UNKNOWN
    fee_as_disclosed: str | None = None
    provenance: list[Evidence] = Field(min_length=1)


class ConsultantEngagement(Strict):
    allocator_id: str
    consultant_name_as_disclosed: str
    as_of: date
    provenance: list[Evidence] = Field(min_length=1)


class ManualFact(Strict):
    """Touchpoint 4: a human types in a fact from a MANUAL-ONLY source."""

    allocator_id: str
    source_url: str
    source_id: str  # e.g. P1, C5, FL1, R1
    event_date: date
    fact: str = Field(max_length=300)
    signal_type: SignalType
    qualifier: str | None = None

    @field_validator("source_url")
    @classmethod
    def http_only(cls, v: str) -> str:
        if not v.startswith(("https://", "http://")):
            raise ValueError("source URL must be http(s)")
        if v.lower().startswith("mailto:"):
            raise ValueError("mailto links are never stored")
        return v
