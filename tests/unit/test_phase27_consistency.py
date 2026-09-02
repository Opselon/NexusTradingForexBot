"""
PHASE 27 QA REGRESSION (anti-overfitting matrix)
=================================================
Covers TEST A through TEST F as instructed by Nexus Main / Peer peer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.candidates import StrategyCandidate
from nexus_scalp.research.context_contract import (
    ContextContractError,
    contract_hash,
    extract_context_contract,
)
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.models import ResearchDataset, ResearchSample
from nexus_scalp.research.oos import MIN_OOS_EXPECTANCY_R, OOSGate
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.research.walkforward import WalkForwardEngine


def _make_phase27_samples() -> list[ResearchSample]:
    out: list[ResearchSample] = []
    # 40 London trending-expansion samples with +0.4R expectancy.
    # Timeline: London block FIRST (00:00-00:39), poison AFTER (01:00+) so the
    # GLOBAL temporal OOS tail lands on the -0.5R noise while the contract-
    # scoped population stays pure London.
    for i in range(40):
        out.append(
            ResearchSample(
                sample_id=f"lon_{i}",
                experience_id=f"e_lon_{i}",
                idempotency_key=f"k_lon_{i}",
                strategy_id="strat_london",
                symbol="XAUUSD",
                decision_timestamp=datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC) + timedelta(minutes=i),
                outcome_timestamp=datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
                + timedelta(minutes=i + 1),
                session="LONDON",
                regime="TRENDING",
                trend_state="UP",
                volatility_regime="EXPANSION",
                realized_r=0.4,
                realized_pnl_usd=40.0,
                risk_distance=1.0,
            )
        )
    # 20 Asian/NY samples with -0.5R expectancy (noise), temporally LAST.
    for i in range(20):
        out.append(
            ResearchSample(
                sample_id=f"mix_{i}",
                experience_id=f"e_mix_{i}",
                idempotency_key=f"k_mix_{i}",
                strategy_id="strat_london",
                symbol="XAUUSD",
                decision_timestamp=datetime(2026, 8, 1, 1, 0, 0, tzinfo=UTC) + timedelta(minutes=i),
                outcome_timestamp=datetime(2026, 8, 1, 1, 1, 0, tzinfo=UTC) + timedelta(minutes=i),
                session="ASIAN" if i % 2 == 0 else "NEW_YORK",
                regime="RANGING",
                trend_state="DOWN" if i % 3 == 0 else "NEUTRAL",
                volatility_regime="NORMAL",
                realized_r=-0.5,
                realized_pnl_usd=-50.0,
                risk_distance=1.0,
            )
        )
    return out


def _dataset(samples: list[ResearchSample], ds_id: str) -> ResearchDataset:
    return ResearchDataset(
        dataset_id=ds_id,
        created_at=datetime.now(UTC),
        samples=samples,
        source_range={"start": "2026-08-01T00:00:00Z", "end": "2026-08-01T23:59:59Z"},
        schema_ids=["scalp_v3"],
    )


LONDON_CONTRACT = {
    "sessions": ["LONDON"],
    "trend_states": ["UP"],
    "volatility_regimes": ["EXPANSION"],
}


@pytest.fixture
def temp_audit_repo(tmp_path):
    db_file = tmp_path / "test_phase27.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield repo
    repo.close()


def test_phase27_a_london_strategy_gates(temp_audit_repo):
    """TEST A: London-only strategy (+0.4R London x40, -0.5R Asian/NY x20):
    contract-scoped pipeline gates PASS; global eval FAIL.
    """
    samples = _make_phase27_samples()
    ds = _dataset(samples, "ds_a")
    registry = StrategyRegistry(audit_repo=temp_audit_repo)
    builder = ResearchDatasetBuilder(ledger=None)  # type: ignore
    pipeline = ResearchPipeline(dataset_builder=builder, registry=registry)

    candidate = StrategyCandidate(
        strategy_id="strat_london_only",
        strategy_version="1.0.0",
        context_definition={
            "symbol": "XAUUSD",
            "fingerprint": "XAUUSD|M1|LONDON|TRENDING|EXPANSION",
            "session": "LONDON",
            "regime": {"name": "TRENDING", "require": "UP"},
            "volatility_regime": "EXPANSION",
        },
        entry_logic={"direction": "directional"},
        exit_logic={"direction": "neutral"},
    )

    # 1. Contract-scoped pipeline execution (via declared context contract) should PASS / VALIDATED
    res_scoped = pipeline.validate_candidate(candidate, ds, n_folds=3)
    assert res_scoped["lifecycle"] == "VALIDATED"

    # 2. Global evaluation (no contract / contract=None) on the same mixed dataset should FAIL
    oos_global = OOSGate().evaluate(ds, strategy_id="strat_london_only", strategy_version="1.0.0")
    # Global mixed expectancy is negative or degraded -> OOS gate fails
    assert oos_global.status == "FAIL" and oos_global.oos_expectancy_r < 0.0, (
        f"global eval unexpectedly passed: {oos_global.status} {oos_global.oos_expectancy_r}"
    )


def test_phase27_b_random_strategy_rejected(temp_audit_repo):
    """TEST B: Random strategy (mixed random R): REJECTED under both global and contract-scoped paths."""
    out: list[ResearchSample] = []
    for i in range(50):
        out.append(
            ResearchSample(
                sample_id=f"rand_{i}",
                experience_id=f"e_rand_{i}",
                idempotency_key=f"k_rand_{i}",
                strategy_id="strat_rand",
                symbol="XAUUSD",
                decision_timestamp=datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
                + timedelta(minutes=7 * i),
                outcome_timestamp=datetime(2026, 8, 1, 0, 1, 0, tzinfo=UTC)
                + timedelta(minutes=7 * i),
                session="LONDON",
                regime="TRENDING",
                trend_state="UP",
                volatility_regime="EXPANSION",
                realized_r=-0.1 if i % 2 == 0 else -0.2,
                realized_pnl_usd=-10.0,
                risk_distance=1.0,
            )
        )
    ds = _dataset(out, "ds_b")
    registry = StrategyRegistry(audit_repo=temp_audit_repo)
    builder = ResearchDatasetBuilder(ledger=None)  # type: ignore
    pipeline = ResearchPipeline(dataset_builder=builder, registry=registry)

    candidate = StrategyCandidate(
        strategy_id="strat_rand",
        strategy_version="1.0.0",
        context_definition={
            "symbol": "XAUUSD",
            "fingerprint": "XAUUSD|M1|LONDON|TRENDING|EXPANSION",
            "session": "LONDON",
        },
        entry_logic={"direction": "directional"},
        exit_logic={"direction": "neutral"},
    )

    res = pipeline.validate_candidate(candidate, ds, n_folds=3)
    assert res["lifecycle"] == "REJECTED"

    # Global path (no contract scoping at all) must ALSO reject.
    oos_global = OOSGate().evaluate(ds, strategy_id="strat_rand", strategy_version="1.0.0")
    wf_global = WalkForwardEngine().validate(
        ds, strategy_id="strat_rand", strategy_version="1.0.0", n_splits=3
    )
    assert oos_global.status == "FAIL"
    assert oos_global.oos_expectancy_r < MIN_OOS_EXPECTANCY_R
    assert wf_global.passed is False


def test_phase27_c_context_overfit_insufficient_population():
    """TEST C: Context overfit - 6 matching samples only: pipeline must NOT validate
    (insufficient population -> candidate held; assert wf fold count == 0 or passed False).
    """
    out: list[ResearchSample] = []
    for i in range(6):
        out.append(
            ResearchSample(
                sample_id=f"small_{i}",
                experience_id=f"e_small_{i}",
                idempotency_key=f"k_small_{i}",
                strategy_id="strat_small",
                symbol="XAUUSD",
                decision_timestamp=datetime(2026, 8, 1, i, 0, 0, tzinfo=UTC),
                outcome_timestamp=datetime(2026, 8, 1, i, 1, 0, tzinfo=UTC),
                session="LONDON",
                regime="TRENDING",
                trend_state="UP",
                volatility_regime="EXPANSION",
                realized_r=0.5,
                realized_pnl_usd=50.0,
                risk_distance=1.0,
            )
        )
    ds = _dataset(out, "ds_c")
    wf = WalkForwardEngine()
    res = wf.validate(
        ds,
        strategy_id="strat_small",
        strategy_version="1.0.0",
        n_splits=3,
        context_contract=LONDON_CONTRACT,
    )
    # With only 6 samples, walk_forward_folds cannot form valid splits (block < 3 or n < segment) -> fold_count == 0 or passed is False
    assert len(res.folds) == 0 or res.passed is False


def test_phase27_d_contract_hash_determinism():
    """TEST D: contract_hash determinism: same DSL context twice -> identical hash;
    different session -> different hash (use nexus_scalp.research.context_contract.contract_hash + extract_context_contract).
    """
    dsl1 = {"session": "LONDON", "regime": {"require": "UP"}, "volatility_regime": "EXPANSION"}
    dsl2 = {"session": "LONDON", "regime": {"require": "UP"}, "volatility_regime": "EXPANSION"}
    dsl3 = {"session": "NEW_YORK", "regime": {"require": "UP"}, "volatility_regime": "EXPANSION"}

    c1 = extract_context_contract(dsl1, {})
    c2 = extract_context_contract(dsl2, {})
    c3 = extract_context_contract(dsl3, {})

    h1 = contract_hash(c1)
    h2 = contract_hash(c2)
    h3 = contract_hash(c3)

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 16


def test_phase27_e_temporal_split_oos_gate_thresholds():
    """TEST E: IS +0.5R then OOS -0.3R temporal split must FAIL the OOS gate thresholds unchanged (MIN_OOS_EXPECTANCY_R=0.0)."""
    assert MIN_OOS_EXPECTANCY_R == 0.0

    out: list[ResearchSample] = []
    # Create samples such that temporal split produces positive IS and negative OOS
    # Let's make 20 early samples with +0.5R and 10 later samples with -0.3R
    base = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
    for i in range(20):
        out.append(
            ResearchSample(
                sample_id=f"is_{i}",
                experience_id=f"e_is_{i}",
                idempotency_key=f"k_is_{i}",
                strategy_id="strat_e",
                symbol="XAUUSD",
                decision_timestamp=base + timedelta(minutes=i),
                outcome_timestamp=base + timedelta(minutes=i + 1),
                session="LONDON",
                regime="TRENDING",
                trend_state="UP",
                volatility_regime="EXPANSION",
                realized_r=0.5,
                realized_pnl_usd=50.0,
                risk_distance=1.0,
            )
        )
    for i in range(10):
        out.append(
            ResearchSample(
                sample_id=f"oos_{i}",
                experience_id=f"e_oos_{i}",
                idempotency_key=f"k_oos_{i}",
                strategy_id="strat_e",
                symbol="XAUUSD",
                decision_timestamp=base + timedelta(hours=5 + i),
                outcome_timestamp=base + timedelta(hours=5 + i + 1),
                session="LONDON",
                regime="TRENDING",
                trend_state="UP",
                volatility_regime="EXPANSION",
                realized_r=-0.3,
                realized_pnl_usd=-30.0,
                risk_distance=1.0,
            )
        )

    ds = _dataset(out, "ds_e")
    oos_gate = OOSGate()
    res = oos_gate.evaluate(
        ds, strategy_id="strat_e", strategy_version="1.0.0", val_frac=0.1, oos_frac=0.3
    )
    assert res.status == "FAIL"
    assert res.oos_expectancy_r < MIN_OOS_EXPECTANCY_R


def test_phase27_f_extract_context_contract_raises():
    """TEST F: _extract_context_contract raises ContextContractError on extraction failure
    instead of silent None (simulate by monkeypatching extract_context_contract to raise).
    """
    from nexus_scalp.research.pipeline import _extract_context_contract

    candidate = StrategyCandidate(
        strategy_id="strat_err",
        strategy_version="1.0.0",
        context_definition={"session": "LONDON"},
        entry_logic={"direction": "directional"},
        exit_logic={"direction": "neutral"},
    )

    with patch(
        "nexus_scalp.research.context_contract.extract_context_contract",
        side_effect=ValueError("simulated extraction failure"),
    ):
        with pytest.raises(ContextContractError):
            _extract_context_contract(candidate)
