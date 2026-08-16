"""
Strategy Candidate Contract & Deterministic Versioning
======================================================
PHASE 09B (spec 9 / 27 / 28).

A `StrategyCandidate` has a DETERMINISTIC identity derived from its definition,
not from a random id or from the existence of any model artifact. The identity
is content-addressed: any change to entry/exit/context/risk/schema produces a
NEW strategy_version, and the old version's historical validation records stay
attached to the old version (immutable versioning).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexus_scalp.experience.models import CANONICAL_FEATURE_DIMENSION, CANONICAL_FEATURE_SCHEMA_ID
from nexus_scalp.research.models import CandidateLifecycle


class StrategyCandidate(BaseModel):
    """
    A candidate strategy: a testable hypothesis with deterministic identity.

    Fields (spec 9):
      strategy_id, strategy_version, feature_schema_id, feature_dimension,
      creation_timestamp, source_dataset_id, discovery_window, context_definition,
      entry_logic, exit_logic, risk_assumptions, parent_strategy_ids,
      discovery_method, lifecycle status.

    The candidate does NOT depend on a model artifact existing.
    """

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(...)
    strategy_version: str = Field(..., description="Content-derived immutable version")
    feature_schema_id: str = Field(default=CANONICAL_FEATURE_SCHEMA_ID)
    feature_dimension: int = Field(default=CANONICAL_FEATURE_DIMENSION, gt=0)
    creation_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    source_dataset_id: str = Field(default="")
    discovery_window: str = Field(default="")
    context_definition: dict[str, Any] = Field(default_factory=dict)
    entry_logic: dict[str, Any] = Field(default_factory=dict)
    exit_logic: dict[str, Any] = Field(default_factory=dict)
    risk_assumptions: dict[str, Any] = Field(default_factory=dict)
    parent_strategy_ids: list[str] = Field(default_factory=list)
    discovery_method: str = Field(default="")
    lifecycle: CandidateLifecycle = Field(default=CandidateLifecycle.DISCOVERED)
    discovery_evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("creation_timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    # ------------------------------------------------------------------
    # Deterministic identity
    # ------------------------------------------------------------------

    def content_digest(self) -> str:
        """Deterministic hash of the strategy DEFINITION (not timestamps)."""
        payload = {
            "strategy_id": self.strategy_id,
            "feature_schema_id": self.feature_schema_id,
            "feature_dimension": self.feature_dimension,
            "context_definition": _sort(self.context_definition),
            "entry_logic": _sort(self.entry_logic),
            "exit_logic": _sort(self.exit_logic),
            "risk_assumptions": _sort(self.risk_assumptions),
            "parent_strategy_ids": sorted(self.parent_strategy_ids),
            "discovery_method": self.discovery_method,
        }
        raw = json.dumps(payload, sort_keys=True, default=str).encode()
        return hashlib.sha256(raw).hexdigest()

    def canonical_version(self) -> str:
        """Version derived from content: `v<first-12-of-digest>`."""
        return "v" + self.content_digest()[:12]

    def is_version_consistent(self) -> bool:
        """True when strategy_version matches the content-derived version."""
        return self.strategy_version == self.canonical_version()

    def with_definition_change(self, **changes: Any) -> StrategyCandidate:
        """
        Returns a NEW candidate (new version) after a definition change.

        Mutating logic is forbidden on a frozen candidate; instead derive a new
        version. Historical validation results remain attached to the OLD
        version (immutability, spec 28).
        """
        current = self.model_dump()
        for k in ("context_definition", "entry_logic", "exit_logic", "risk_assumptions"):
            if k in changes and isinstance(changes[k], dict):
                changes[k] = _merge_dicts(current.get(k, {}), changes[k])
        candidate = StrategyCandidate(**{**current, **changes})
        candidate = candidate.model_copy(
            update={
                "strategy_version": candidate.canonical_version(),
                "lifecycle": CandidateLifecycle.DISCOVERED,
                "creation_timestamp": datetime.now(UTC),
                "parent_strategy_ids": [
                    *list(current.get("parent_strategy_ids", [])),
                    self.strategy_version,
                ],
            }
        )
        return candidate

    # ------------------------------------------------------------------
    # Schema compatibility (spec 25)
    # ------------------------------------------------------------------

    def is_schema_compatible(self, other_schema_id: str, other_dimension: int) -> bool:
        """A 50D candidate must never be silently compared to a 350D context."""
        return (
            self.feature_schema_id == other_schema_id and self.feature_dimension == other_dimension
        )


def _sort(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sort(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_sort(x) for x in obj]
    return obj


def _merge_dicts(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in update.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dicts(out[k], v)
        else:
            out[k] = v
    return out
