"""
Tests for AI decision attribution (Phase 5 explainability).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nexus_scalp.research.attribution import (
    AIAttributionEngine,
    ContributionKind,
    DecisionContribution,
    SourceType,
)
from nexus_scalp.research.models import CandidateLifecycle, StrategyRegistryEntry


def _entry(sid: str = "s1", lc: CandidateLifecycle = CandidateLifecycle.SHADOW, **kw):
    base = dict(
        strategy_id=sid,
        strategy_version="1.0.0",
        lifecycle=lc,
        discovery_source="family_discovery",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        validation_lineage=[
            "2026-08-21T09:41:00+00:00:VALIDATED",
            "2026-08-23T11:03:00+00:00:SHADOW:operator_promotion:actor=alice",
        ],
    )
    base.update(kw)
    return StrategyRegistryEntry(**base)


class TestDecisionContribution:
    def test_unmeasured_weight_is_none_not_zero(self):
        c = DecisionContribution(
            source_type=SourceType.AI_RESEARCH,
            kind=ContributionKind.AI_SUGGESTED,
            strategy_id="s1",
        )
        d = c.to_dict()
        assert d["weight"] is None
        assert d["weight_measured"] is False
        # Never fabricate a 0.73-style number.
        assert not isinstance(d["weight"], float)

    def test_measured_weight_round_trips(self):
        c = DecisionContribution(
            source_type=SourceType.AI_PARAMETER_OPTIMIZATION,
            kind=ContributionKind.AI_RANKED,
            strategy_id="s1",
            weight=0.42,
            confidence=0.9,
        )
        d = c.to_dict()
        assert d["weight"] == 0.42
        assert d["weight_measured"] is True


class TestAttributionEngine:
    def test_provenance_contribution_present(self):
        eng = AIAttributionEngine(None)
        rep = eng.attribution(_entry())
        kinds = [c["kind"] for c in rep["contributions"]]
        assert ContributionKind.AI_SUGGESTED.value in kinds
        srcs = [c["source_type"] for c in rep["contributions"]]
        assert any(s.startswith("AI_") for s in srcs)

    def test_operator_lineage_is_human(self):
        eng = AIAttributionEngine(None)
        rep = eng.attribution(_entry())
        human = [c for c in rep["contributions"] if c["source_type"] == SourceType.HUMAN.value]
        assert len(human) == 1
        assert human[0]["kind"] == ContributionKind.HUMAN_APPROVED.value
        assert human[0]["decision_id"] == "transition:SHADOW"

    def test_gate_verdict_is_statistical_test(self):
        eng = AIAttributionEngine(None)
        rep = eng.attribution(_entry())
        stats = [c for c in rep["contributions"] if c["source_type"] == SourceType.STATISTICAL_TEST.value]
        assert any(c["kind"] == ContributionKind.SYSTEM_VALIDATED.value for c in stats)

    def test_status_honest_when_no_weights(self):
        eng = AIAttributionEngine(None)
        rep = eng.attribution(_entry())
        # AI provenance exists but no numeric weights recorded.
        assert rep["status"] == "PARTIALLY_MEASURABLE"
        assert rep["measured"]["weights"] == 0
        assert "require instrumented" in rep["measured"]["note"]

    def test_empty_contributions_not_available(self):
        eng = AIAttributionEngine(None)
        bare = _entry(discovery_source="", validation_lineage=[])
        rep = eng.attribution(bare)
        assert rep["status"] == "NOT_AVAILABLE"
        assert rep["contributions"] == []

    def test_timeline_sorted_chronologically(self):
        eng = AIAttributionEngine(None)
        rep = eng.attribution(_entry())
        ts = [t["timestamp"] for t in rep["timeline"]]
        assert ts == sorted(ts)

    def test_distinct_kinds_never_conflated(self):
        # HUMAN_APPROVED and SYSTEM_VALIDATED are different concepts and must
        # appear as separate records with separate source types.
        eng = AIAttributionEngine(None)
        rep = eng.attribution(_entry())
        pairs = {(c["source_type"], c["kind"]) for c in rep["contributions"]}
        assert (SourceType.HUMAN.value, ContributionKind.HUMAN_APPROVED.value) in pairs
        assert (SourceType.STATISTICAL_TEST.value, ContributionKind.SYSTEM_VALIDATED.value) in pairs
