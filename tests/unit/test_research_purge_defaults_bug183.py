"""BUG-183: research leakage guards must be ON by default on the production path.

splitting.py declares DEFAULT_PURGE_SECONDS=300 / DEFAULT_EMBARGO_SECONDS=60
(BUG-140 Phase 7: "the default no longer leaks") and
tests/unit/test_evidence_semantics_bug140.py asserts those constants are > 0.
The production research path nonetheless defaulted every gate to 0.0:
ResearchPipeline.validate_candidate, BacktestEngine.run(use_split=True),
OOSGate.evaluate and WalkForwardEngine.validate all took purge/embargo=0.0
unless the caller remembered to pass them, and ResearchPipeline._record_run
hardcoded purge_seconds/embargo_seconds=0.0 into the persisted run snapshot.

This regression pins the AFTER state:
 1. the four production consumers default to the splitting constants,
 2. the run record stores the effective purge/embargo values (not literals),
 3. split_temporal semantics actually purge a boundary-crossing horizon when
    the defaults are supplied.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

from nexus_scalp.research.backtest import BacktestEngine
from nexus_scalp.research.candidates import StrategyCandidate
from nexus_scalp.research.models import ResearchDataset
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.splitting import (
    DEFAULT_EMBARGO_SECONDS,
    DEFAULT_PURGE_SECONDS,
    split_temporal,
)
from nexus_scalp.research.walkforward import WalkForwardEngine


def _sig_default(fn, name: str) -> float:
    return float(inspect.signature(fn).parameters[name].default)


def test_production_gates_default_to_splitting_leakage_constants() -> None:
    assert DEFAULT_PURGE_SECONDS > 0.0
    assert DEFAULT_EMBARGO_SECONDS > 0.0

    assert (
        _sig_default(ResearchPipeline.validate_candidate, "purge_seconds") == DEFAULT_PURGE_SECONDS
    ), "ResearchPipeline.validate_candidate must default purge to the splitting constant"
    assert (
        _sig_default(ResearchPipeline.validate_candidate, "embargo_seconds")
        == DEFAULT_EMBARGO_SECONDS
    ), "ResearchPipeline.validate_candidate must default embargo to the splitting constant"

    assert _sig_default(OOSGate.evaluate, "purge_seconds") == DEFAULT_PURGE_SECONDS
    assert _sig_default(OOSGate.evaluate, "embargo_seconds") == DEFAULT_EMBARGO_SECONDS

    assert _sig_default(WalkForwardEngine.validate, "purge_seconds") == DEFAULT_PURGE_SECONDS
    assert _sig_default(WalkForwardEngine.validate, "embargo_seconds") == DEFAULT_EMBARGO_SECONDS

    assert _sig_default(BacktestEngine.run, "purge_seconds") == DEFAULT_PURGE_SECONDS
    assert _sig_default(BacktestEngine.run, "embargo_seconds") == DEFAULT_EMBARGO_SECONDS


def _minimal_pipeline() -> ResearchPipeline:
    class _FakeAuditRepo:
        _is_sqlite = False

    class _FakeRegistry:
        audit_repo = _FakeAuditRepo()

    class _FakeBuilder:
        pass

    return ResearchPipeline(dataset_builder=_FakeBuilder(), registry=_FakeRegistry())


def test_run_record_stores_effective_purge_and_embargo() -> None:
    pipeline = _minimal_pipeline()
    candidate = StrategyCandidate(
        strategy_id="bug183",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="test",
        context_definition={"symbol": "XAUUSD"},
        entry_logic={"dir": "long"},
        exit_logic={"dir": "short"},
        feature_dimension=4,
    )
    dataset = ResearchDataset(dataset_id="ds_bug183")

    pipeline._record_run(
        run_id="run_bug183",
        candidate=candidate,
        dataset=dataset,
        summary={},
        status="COMPLETED",
        run_outcome="VALIDATED",
        purge_seconds=DEFAULT_PURGE_SECONDS,
        embargo_seconds=DEFAULT_EMBARGO_SECONDS,
    )

    assert pipeline.last_run is not None
    config = pipeline.last_run.config
    assert config["purge_seconds"] == DEFAULT_PURGE_SECONDS, (
        "run snapshot must record the effective purge, not a hardcoded 0.0"
    )
    assert config["embargo_seconds"] == DEFAULT_EMBARGO_SECONDS, (
        "run snapshot must record the effective embargo, not a hardcoded 0.0"
    )


def test_split_temporal_default_constants_purge_boundary_crossing_horizon() -> None:
    # A train-tail sample whose label horizon crosses the train/val boundary
    # must leave the train partition when the (now default) purge is applied.
    t0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    base = dict(
        symbol="XAUUSD",
        strategy_id="s",
        strategy_version="1.0.0",
        feature_schema_id="scalp_v3",
        feature_dimension=4,
        realized_r=1.0,
    )

    def _sample(i: int, horizon_min: int):
        from nexus_scalp.research.models import ResearchSample

        decision = t0 + timedelta(minutes=i)
        return ResearchSample(
            sample_id=f"s{i}",
            experience_id=f"e{i}",
            idempotency_key=f"k{i}",
            decision_timestamp=decision,
            outcome_timestamp=decision + timedelta(minutes=horizon_min),
            **base,
        )

    samples = [_sample(i, 1) for i in range(9)] + [_sample(9, 600)]
    dataset = ResearchDataset(dataset_id="ds_purge", samples=samples)

    split = split_temporal(
        dataset,
        purge_seconds=DEFAULT_PURGE_SECONDS,
        embargo_seconds=DEFAULT_EMBARGO_SECONDS,
    )

    train_keys = {s.sample_id for s in split.train}
    assert "s9" not in train_keys, (
        "sample whose 600s horizon crosses the train/val boundary must be purged"
    )
    # Sanity: unpurged early samples remain in train.
    assert "s0" in train_keys
