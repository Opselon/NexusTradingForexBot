"""
Strategy Marketplace — Domain Models (CHG-0056)
================================================

Marketplace items are SEED PACKAGES: bundles of deterministic DSL seed
strategies (strategies/factory/models.py StrategyDsl — strategies are DATA,
never code). A seed is installed into the marketplace lifecycle and may be
enabled for a mode only when its research evidence supports it.

SAFETY CONTRACT (mirrors strategies/factory/models.py):
  * A seed never places, modifies or closes an order.
  * There is NO skip path to live: LIVE_ELIGIBLE requires RESEARCH_VALIDATED
    plus an explicit operator approval flag (recorded, never implied).
  * Lifecycle transitions are validated by TRANSITIONS (state machine) and
    every change is recorded as an append-only event row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexus_scalp.strategies.factory.models import StrategyDsl


def utc_now() -> datetime:
    return datetime.now(UTC)


def _coerce_utc(v: datetime) -> datetime:
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


# ---------------------------------------------------------------------------
# Lifecycle (spec §2)
# ---------------------------------------------------------------------------


class MarketplaceLifecycle(StrEnum):
    """Marketplace item lifecycle. No skip path to live."""

    INSTALLED = "INSTALLED"
    RESEARCH_PENDING = "RESEARCH_PENDING"
    RESEARCH_RUNNING = "RESEARCH_RUNNING"
    RESEARCH_VALIDATED = "RESEARCH_VALIDATED"
    RESEARCH_REJECTED = "RESEARCH_REJECTED"
    PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
    SHADOW_ELIGIBLE = "SHADOW_ELIGIBLE"
    LIVE_CANDIDATE = "LIVE_CANDIDATE"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
    DISABLED = "DISABLED"
    QUARANTINED = "QUARANTINED"
    RETIRED = "RETIRED"


def _t(*targets: MarketplaceLifecycle) -> set[MarketplaceLifecycle]:
    return set(targets)


#: Legal lifecycle transitions (append-only state machine). RESEARCH_RUNNING ->
#: RESEARCH_PENDING is the "run crashed / cancelled" recovery edge; it never
#: bypasses evidence: PAPER_ELIGIBLE / SHADOW_ELIGIBLE / LIVE_CANDIDATE can be
#: entered ONLY from RESEARCH_VALIDATED.
TRANSITIONS: dict[MarketplaceLifecycle, set[MarketplaceLifecycle]] = {
    MarketplaceLifecycle.INSTALLED: _t(
        MarketplaceLifecycle.RESEARCH_PENDING,
        MarketplaceLifecycle.DISABLED,
        MarketplaceLifecycle.QUARANTINED,
        MarketplaceLifecycle.RETIRED,
    ),
    MarketplaceLifecycle.RESEARCH_PENDING: _t(
        MarketplaceLifecycle.RESEARCH_RUNNING,
        MarketplaceLifecycle.DISABLED,
        MarketplaceLifecycle.QUARANTINED,
        MarketplaceLifecycle.RETIRED,
    ),
    MarketplaceLifecycle.RESEARCH_RUNNING: _t(
        MarketplaceLifecycle.RESEARCH_PENDING,  # crashed / cancelled run
        MarketplaceLifecycle.RESEARCH_VALIDATED,
        MarketplaceLifecycle.RESEARCH_REJECTED,
    ),
    MarketplaceLifecycle.RESEARCH_VALIDATED: _t(
        MarketplaceLifecycle.PAPER_ELIGIBLE,
        MarketplaceLifecycle.SHADOW_ELIGIBLE,
        MarketplaceLifecycle.LIVE_CANDIDATE,
        MarketplaceLifecycle.DISABLED,
        MarketplaceLifecycle.QUARANTINED,
        MarketplaceLifecycle.RETIRED,
    ),
    MarketplaceLifecycle.RESEARCH_REJECTED: _t(
        MarketplaceLifecycle.RESEARCH_PENDING,  # repair -> re-run research
        MarketplaceLifecycle.QUARANTINED,
        MarketplaceLifecycle.RETIRED,
    ),
    MarketplaceLifecycle.PAPER_ELIGIBLE: _t(
        MarketplaceLifecycle.SHADOW_ELIGIBLE,
        MarketplaceLifecycle.DISABLED,
        MarketplaceLifecycle.QUARANTINED,
        MarketplaceLifecycle.RETIRED,
    ),
    MarketplaceLifecycle.SHADOW_ELIGIBLE: _t(
        MarketplaceLifecycle.LIVE_CANDIDATE,
        MarketplaceLifecycle.DISABLED,
        MarketplaceLifecycle.QUARANTINED,
        MarketplaceLifecycle.RETIRED,
    ),
    MarketplaceLifecycle.LIVE_CANDIDATE: _t(
        MarketplaceLifecycle.LIVE_ELIGIBLE,  # ONLY with operator approval flag
        MarketplaceLifecycle.DISABLED,
        MarketplaceLifecycle.QUARANTINED,
        MarketplaceLifecycle.RETIRED,
    ),
    MarketplaceLifecycle.LIVE_ELIGIBLE: _t(
        MarketplaceLifecycle.DISABLED,
        MarketplaceLifecycle.QUARANTINED,
        MarketplaceLifecycle.RETIRED,
    ),
    MarketplaceLifecycle.DISABLED: _t(
        MarketplaceLifecycle.RESEARCH_PENDING,
        MarketplaceLifecycle.PAPER_ELIGIBLE,
        MarketplaceLifecycle.SHADOW_ELIGIBLE,
        MarketplaceLifecycle.RETIRED,
    ),
    MarketplaceLifecycle.QUARANTINED: _t(MarketplaceLifecycle.RETIRED),
    MarketplaceLifecycle.RETIRED: set(),
}


def can_transition(current: MarketplaceLifecycle, target: MarketplaceLifecycle) -> bool:
    return target in TRANSITIONS.get(current, set())


class LifecycleTransitionError(ValueError):
    """Raised on an illegal marketplace lifecycle transition."""


# ---------------------------------------------------------------------------
# Enablement modes (spec §2)
# ---------------------------------------------------------------------------


class EnablementMode(StrEnum):
    RESEARCH = "RESEARCH"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE_REQUEST = "LIVE_REQUEST"


#: Operator approval flag keys (settings layer; default OFF => PENDING).
LIVE_APPROVAL_FLAG = "marketplace.live_approval_enabled"

#: Enablement status values.
ENABLEMENT_PENDING = "PENDING"
ENABLEMENT_GRANTED = "GRANTED"
ENABLEMENT_DENIED = "DENIED"


# ---------------------------------------------------------------------------
# Seed specification (spec §2 — all epic fields)
# ---------------------------------------------------------------------------


class SeedSpec(BaseModel):
    """One marketplace seed: a validated DSL strategy + marketplace metadata.

    The DSL is the ONLY strategy representation; every field here is
    marketplace packaging (provenance, scoping, compatibility contract).
    """

    model_config = ConfigDict(frozen=True)

    seed_id: str = Field(..., min_length=3, max_length=128)
    name: str = Field(..., min_length=1, max_length=128)
    family: str = Field(..., min_length=1, max_length=64)
    version: str = Field(default="1.0.0", min_length=1, max_length=32)
    author: str = Field(default="", max_length=128)
    description: str = Field(default="", max_length=2048)
    source: str = Field(default="marketplace_pack", max_length=64)
    license: str = Field(default="proprietary", max_length=64)
    instrument_scope: list[str] = Field(default_factory=lambda: ["XAUUSD"])
    timeframe_scope: list[str] = Field(default_factory=list)
    required_features: list[str] = Field(default_factory=list)
    parameter_schema: dict[str, Any] = Field(default_factory=dict)
    default_parameters: dict[str, Any] = Field(default_factory=dict)
    risk_profile: str = Field(default="MODERATE", max_length=32)
    expected_market_regimes: list[str] = Field(default_factory=list)
    unsupported_market_regimes: list[str] = Field(default_factory=list)
    compatibility_contract: dict[str, Any] = Field(default_factory=dict)
    dsl: StrategyDsl = Field(...)


class SeedPackage(BaseModel):
    """A named pack of seeds produced by one deterministic generator."""

    model_config = ConfigDict(frozen=True)

    pack_id: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    family: str = Field(..., min_length=1, max_length=64)
    description: str = Field(default="", max_length=2048)
    version: str = Field(default="1.0.0", max_length=32)
    seeds: tuple[SeedSpec, ...] = Field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Stored row models (mirror store.py tables)
# ---------------------------------------------------------------------------


class LifecycleEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    seed_id: str
    from_lifecycle: str
    to_lifecycle: str
    reason: str = ""
    actor: str = "system"
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _coerce_utc(v)


class EnablementRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    seed_id: str
    mode: EnablementMode
    status: str = ENABLEMENT_PENDING  # PENDING | GRANTED | DENIED
    reason: str = ""
    actor: str = "operator"
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("updated_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _coerce_utc(v)


class ScoreSnapshot(BaseModel):
    """One immutable 14-factor evaluation snapshot (mk_score_snapshots row)."""

    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    seed_id: str
    profile_id: str
    profile_version: int
    total: float = Field(ge=0.0, le=1.0)
    verdict: str = "INCONCLUSIVE"
    factors: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _coerce_utc(v)


class RepairRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    repair_id: str
    seed_id: str
    parent_seed_id: str = ""
    trigger: str = ""
    status: str = "PENDING"  # PENDING | COMPARING | PROMOTED | REJECTED | FAILED
    outcome: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _coerce_utc(v)


__all__ = [
    "ENABLEMENT_DENIED",
    "ENABLEMENT_GRANTED",
    "ENABLEMENT_PENDING",
    "LIVE_APPROVAL_FLAG",
    "TRANSITIONS",
    "EnablementMode",
    "EnablementRecord",
    "LifecycleEvent",
    "LifecycleTransitionError",
    "MarketplaceLifecycle",
    "RepairRecord",
    "ScoreSnapshot",
    "SeedPackage",
    "SeedSpec",
    "can_transition",
    "utc_now",
]
