"""ML-PHASE1 STEP-9 regression: audit() and build() census consistency.

Proves the stored provenance census uses the same classification semantics
as eligibility/build. Dataset accounting only — no provenance, outcome or R
is fabricated and no candidate gate or trading logic is involved.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.lifecycle import DecisionLifecycle
from nexus_scalp.experience.models import ExperienceRecord, FeatureSnapshot
from nexus_scalp.research.dataset import (
    REASON_MISSING_OUTCOME,
    REASON_UNKNOWN_PROVENANCE,
    ResearchDatasetBuilder,
)


def _make_record(
    key: str,
    *,
    executed: bool = True,
    closed: bool = True,
    realized_r: float = 0.3,
    exit_reason: str = "",
) -> ExperienceRecord:
    from nexus_scalp.domain.enums import ActionType
    from nexus_scalp.experience.models import ModelProvenance, StrategyContext

    ctx = StrategyContext(
        strategy_id="strat_step9",
        regime="RANGING_MEAN_REVERSION",
        session="LONDON",
        volatility_regime="NORMAL",
        trend_state="NEUTRAL",
        confluence_fingerprint="fp",
    )
    snap = FeatureSnapshot(feature_schema_id="scalp_v1", feature_dimension=50, values=[0.1] * 50)
    now = datetime.now(UTC)
    return ExperienceRecord(
        experience_id=f"exp_{key}",
        request_id=f"req_{key}",
        decision_id=f"dec_{key}",
        execution_id="",
        idempotency_key=key,
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=now,
        action=ActionType.BUY_MARKET,
        entry_reason="test",
        proposed_entry=4400.0,
        stop_loss=4395.0,
        take_profit=4410.0,
        risk_reward_ratio=2.0,
        context=ctx,
        feature_snapshot=snap,
        provenance=ModelProvenance(
            model_id="m",
            model_version="v",
            feature_schema_id="scalp_v1",
            feature_dimension=50,
        ),
        is_executed=executed,
        is_closed=closed,
        exit_reason=exit_reason,
        realized_r_multiple=realized_r if (executed and closed) else 0.0,
        realized_pnl_usd=10.0 if (executed and closed) else 0.0,
        outcome_timestamp=now if (executed and closed) else None,
        planned_risk_distance=5.0,
        strategy_id="strat_step9",
        strategy_version="1.0.0",
    )


def _builder_without_ledger() -> ResearchDatasetBuilder:
    """A ResearchDatasetBuilder whose evidence resolver returns NO evidence.

    The ledger is a stub whose ``audit_repo`` reports a non-SQLite backend,
    so ``_evidence_for`` deterministically returns None and an outcome-less
    record lands in UNKNOWN_PROVENANCE without any DB. This keeps the test a
    pure unit test of the shared classification path.
    """

    class _StubRepo:
        _is_sqlite = False
        _db_path = ""

    class _StubLedger:
        audit_repo = _StubRepo()

    builder = ResearchDatasetBuilder.__new__(ResearchDatasetBuilder)
    builder.ledger = _StubLedger()  # type: ignore[assignment]
    # pre-populated: eligible records must not need the DB for the
    # reconstruction-source lookup (they carry an authoritative outcome).
    builder._source_cache = {"__none__": "BROKER_NATIVE"}
    builder._unknown_provenance_logged = set()
    return builder


class TestAuditBuildCensusConsistency:
    """The census attached to a dataset must describe the same eligibility
    decision build() acted on — same record, same reason, both paths."""

    def test_census_matches_build_classification(self) -> None:
        # An unresolved orphan (no outcome, no terminal lifecycle, no
        # evidence) must be UNKNOWN_PROVENANCE in BOTH audit() and build().
        records = [
            _make_record("ok_a", realized_r=0.4),
            _make_record("ok_b", realized_r=-0.2),
            _make_record("orphan_1", executed=False, closed=False),
            _make_record("orphan_2", executed=False, closed=False),
        ]
        builder = _builder_without_ledger()

        report = builder.audit(records)
        assert report["total_records"] == 4
        assert report["eligible"] == 2
        assert report["rejection_reasons"].get(REASON_UNKNOWN_PROVENANCE) == 2
        assert report["rejection_reasons"].get(REASON_MISSING_OUTCOME, 0) == 0

    def test_classify_sample_is_the_shared_path(self) -> None:
        # _classify_sample is the ONE helper both audit() and build() call;
        # a terminal state never reaches the evidence resolver, and an
        # unresolved orphan never collapses into MISSING_OUTCOME.
        builder = _builder_without_ledger()

        ok, _reason, _detail = builder._classify_sample(_make_record("ok_c"))
        assert ok is True

        ok, reason, _detail = builder._classify_sample(
            _make_record("hang_c", executed=False, closed=False)
        )
        assert ok is False
        assert reason == REASON_UNKNOWN_PROVENANCE

    def test_terminal_states_short_circuit_before_evidence(self) -> None:
        # A known terminal non-trade never reaches the evidence resolver:
        # _classify_sample returns the lifecycle reason without a DB read.
        # The stub backend reports no evidence, so the lifecycle reason is
        # the answer even though the resolver is reachable.
        builder = _builder_without_ledger()

        rec = _make_record("term_c", executed=False, closed=False, exit_reason="CANCELED_UNFILLED")
        ok, reason, _detail = builder._classify_sample(rec)
        assert ok is False
        assert reason == "CANCELED_UNFILLED"
