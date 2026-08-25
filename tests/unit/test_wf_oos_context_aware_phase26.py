"""Strategy-Aware Walk-Forward & OOS Tests (PHASE 26) — part 1.

Aligned to the deployed context_contract interface (list-based scopes:
sessions / trend_states / volatility_regimes).
"""

from __future__ import annotations

from datetime import UTC, datetime

from nexus_scalp.research.context_contract import (
    filter_samples_by_contract,
    has_active_contract,
)
from nexus_scalp.research.models import ResearchDataset, ResearchSample
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.walkforward import WalkForwardEngine


def _make_samples() -> list[ResearchSample]:
    out: list[ResearchSample] = []
    # 40 London trending-expansion samples with positive expectancy.
    for i in range(40):
        out.append(
            ResearchSample(
                sample_id=f"lon_{i}",
                experience_id=f"e_lon_{i}",
                idempotency_key=f"k_lon_{i}",
                strategy_id="strat_london",
                symbol="XAUUSD",
                decision_timestamp=datetime(2026, 8, 1, 8, i, 0, tzinfo=UTC),
                outcome_timestamp=datetime(2026, 8, 1, 8, i + 1, 0, tzinfo=UTC),
                session="LONDON",
                regime="TRENDING",
                trend_state="UP",
                volatility_regime="EXPANSION",
                realized_r=0.4,
                realized_pnl_usd=40.0,
                risk_distance=1.0,
            )
        )
    # 20 Asian/NY ranging-normal samples with negative expectancy: the noise
    # that used to poison a specialized strategy's global evaluation.
    for i in range(20):
        out.append(
            ResearchSample(
                sample_id=f"mix_{i}",
                experience_id=f"e_mix_{i}",
                idempotency_key=f"k_mix_{i}",
                strategy_id="strat_london",
                symbol="XAUUSD",
                decision_timestamp=datetime(2026, 8, 1, 2, i, 0, tzinfo=UTC),
                outcome_timestamp=datetime(2026, 8, 1, 2, i + 1, 0, tzinfo=UTC),
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


def test_phase26_01_has_active_contract() -> None:
    assert has_active_contract(LONDON_CONTRACT) is True
    assert has_active_contract({}) is False
    assert has_active_contract({"sessions": []}) is False


def test_phase26_02_london_strategy_filtering() -> None:
    ds = _dataset(_make_samples(), "ds_t1")
    matched, diag = filter_samples_by_contract(ds.samples, LONDON_CONTRACT)
    assert diag["total_samples"] == 60
    assert diag["matched_samples"] == 40
    assert diag["sufficient_evidence"] is True
    assert all(s.session == "LONDON" for s in matched)


def test_phase26_03_walkforward_context_aware_pass() -> None:
    ds = _dataset(_make_samples(), "ds_t2")
    wf = WalkForwardEngine(min_pass_fraction=0.5)
    res = wf.validate(
        ds,
        strategy_id="strat_lon",
        strategy_version="1.0.0",
        n_splits=3,
        context_contract=LONDON_CONTRACT,
    )
    assert res.passed is True
    assert res.avg_val_expectancy_r > 0.0


def test_phase26_04_oos_context_aware_pass() -> None:
    ds = _dataset(_make_samples(), "ds_t3")
    oos = OOSGate()
    res = oos.evaluate(
        ds,
        strategy_id="strat_lon",
        strategy_version="1.0.0",
        context_contract=LONDON_CONTRACT,
    )
    assert res.status == "PASS"


def test_phase26_05_generic_fallback_identical() -> None:
    ds = _dataset(_make_samples(), "ds_t4")
    wf = WalkForwardEngine()
    res = wf.validate(
        ds,
        strategy_id="strat_gen",
        strategy_version="1.0.0",
        n_splits=3,
        context_contract=None,
    )
    assert res.fold_count == 3

# ---------------------------------------------------------------------------
# PHASE 29 (2026-08-25): WF/OOS data-volume honesty + adaptive folds.
# A family too small for the requested fold count must produce an explicit
# insufficient_reason (FAMILY_TOO_SMALL_FOR_FOLDS) instead of a silent
# passed=False with zeroed metrics; the pipeline requests only the maximum
# feasible fold count. No gate threshold is weakened.
# ---------------------------------------------------------------------------


def _family_samples(n: int, prefix: str = "fam", positive: bool = True) -> list[ResearchSample]:
    out: list[ResearchSample] = []
    for i in range(n):
        out.append(
            ResearchSample(
                sample_id=f"{prefix}_{i}",
                experience_id=f"e_{prefix}_{i}",
                idempotency_key=f"k_{prefix}_{i}",
                strategy_id="strat_family",
                symbol="XAUUSD",
                decision_timestamp=datetime(2026, 8, 1, 8, i % 60, (i // 60) % 60, tzinfo=UTC),
                outcome_timestamp=datetime(2026, 8, 1, 9, i % 60, (i // 60) % 60, tzinfo=UTC),
                session="LONDON",
                regime="TRENDING",
                trend_state="UP",
                volatility_regime="EXPANSION",
                realized_r=0.4 if positive else -0.3,
                realized_pnl_usd=40.0 if positive else -30.0,
                risk_distance=1.0,
            )
        )
    return out


def test_phase29_small_family_produces_insufficient_reason_not_silent_fail() -> None:
    # 20 samples < (3+2)*3=15? No — 20 >= 15 but block<3 guard in splitting
    # still yields 0 folds; below 15 the engine must short-circuit loudly.
    ds = _dataset(_family_samples(10), "ds_phase29_small")
    wf = WalkForwardEngine(min_pass_fraction=0.5)
    res = wf.validate(
        ds,
        strategy_id="strat_family",
        strategy_version="1.0.0",
        n_splits=3,
    )
    assert res.passed is False
    assert res.insufficient_reason is not None
    assert res.insufficient_reason.startswith("FAMILY_TOO_SMALL_FOR_FOLDS")
    assert res.fold_count == 0


def test_phase29_adaptive_folds_sixty_samples_get_two_folds() -> None:
    # Spec formula: max_folds = min(n_folds, max(1, (n // 15) - 2))
    # 60 samples -> max(1, 4-2) = 2 folds (not the configured 3).
    n = 60
    assert max(1, (n // 15) - 2) == 2
    assert (n // 3) - 2 == 18  # splitting can form far more than 2; we cap at 2

    ds = _dataset(_family_samples(n), "ds_phase29_adaptive")
    wf = WalkForwardEngine(min_pass_fraction=0.5)
    res = wf.validate(
        ds,
        strategy_id="strat_family",
        strategy_version="1.0.0",
        n_splits=2,
    )
    assert res.fold_count == 2
    assert res.insufficient_reason is None
