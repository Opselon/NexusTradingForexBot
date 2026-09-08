"""Unit tests for the E1/E2 zero-friction guard + canonical cost defaults.

TASK-AUDREV-E1-ZERO-FRICTION (audit rev2, findings E1 + E2).

xdist-safe: only monkeypatch (env) and monkeypatch.setattr are used; no
shared mutable state, no tmp-path coupling between tests.
"""

from __future__ import annotations

import pytest

from nexus_scalp.research.models import (
    CANONICAL_COSTS_PROVENANCE,
    FALLBACK_ZERO_PROVENANCE,
    ExecutionAssumptions,
    ResearchDataset,
    ResearchSample,
    ZeroFrictionError,
    default_research_assumptions,
    ensure_not_zero_friction,
)

# ---------------------------------------------------------------------------
# 1. ensure_not_zero_friction — the loud guard
# ---------------------------------------------------------------------------


def test_ensure_not_zero_friction_raises_without_allow() -> None:
    with pytest.raises(ZeroFrictionError) as exc:
        ensure_not_zero_friction(ExecutionAssumptions())
    assert "ZERO-FRICTION" in str(exc.value)


def test_ensure_not_zero_friction_allow_true_passes() -> None:
    # Must NOT raise when allow=True.
    ensure_not_zero_friction(ExecutionAssumptions(), allow=True, context="test")


def test_ensure_not_zero_friction_spread_only_is_not_zero_friction() -> None:
    # Zero friction = spread==0 AND slippage==0; one non-zero leg passes.
    a = ExecutionAssumptions(spread_ticks=2.0, slippage_ticks=0.0)
    ensure_not_zero_friction(a)


def test_ensure_not_zero_friction_latency_alone_does_not_trigger() -> None:
    # latency_ms does not change fill prices — not part of the trigger.
    a = ExecutionAssumptions(spread_ticks=0.5, slippage_ticks=0.5, latency_ms=120.0)
    ensure_not_zero_friction(a)


def test_ensure_not_zero_friction_canonical_bundle_passes() -> None:
    assumptions, _prov = default_research_assumptions()
    ensure_not_zero_friction(assumptions)


# ---------------------------------------------------------------------------
# 2. default_research_assumptions — canonical vs loud fallback
# ---------------------------------------------------------------------------


def test_default_research_assumptions_canonical_nonzero_spread() -> None:
    """Canonical artifact exists in the repo -> CANONICAL_COSTS + nonzero costs."""
    assumptions, provenance = default_research_assumptions()
    assert provenance == CANONICAL_COSTS_PROVENANCE == "CANONICAL_COSTS"
    assert assumptions.spread_ticks > 0.0
    assert assumptions.slippage_ticks > 0.0


def test_default_research_assumptions_fallback_zero_when_artifact_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing canonical artifact (env override points nowhere) -> loud FALLBACK_ZERO."""
    monkeypatch.setenv("NEXUS_EXECUTION_ASSUMPTIONS", "/nonexistent/does/not/exist.json")
    assumptions, provenance = default_research_assumptions()
    assert provenance == FALLBACK_ZERO_PROVENANCE == "FALLBACK_ZERO"
    # The frozen zero-default bundle is returned (engines will then REFUSE it).
    assert assumptions.spread_ticks == 0.0
    assert assumptions.slippage_ticks == 0.0
    assert assumptions.latency_ms == 0.0


def test_frozen_model_defaults_unchanged() -> None:
    """E1 contract: the FROZEN model defaults must stay zero (guard, not mutation)."""
    a = ExecutionAssumptions()
    assert a.spread_ticks == 0.0 and a.slippage_ticks == 0.0 and a.latency_ms == 0.0


# ---------------------------------------------------------------------------
# 3. BacktestEngine — provenance + entrypoint refusal
# ---------------------------------------------------------------------------


def _tiny_dataset() -> ResearchDataset:
    from datetime import UTC, datetime, timedelta

    base = datetime(2026, 9, 1, tzinfo=UTC)
    samples = [
        ResearchSample(
            sample_id=f"s{i}",
            experience_id=f"e{i}",
            idempotency_key=f"k{i}",
            decision_timestamp=base + timedelta(minutes=i),
            outcome_timestamp=base + timedelta(minutes=i + 5),
            symbol="XAUUSD",
            strategy_id="e1t",
            strategy_version="1",
            direction="BUY_MARKET",
            entry_price=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            realized_r=0.3,
            realized_pnl_usd=30.0,
            risk_distance=10.0,
        )
        for i in range(12)
    ]
    return ResearchDataset(dataset_id="ds_e1", samples=samples)


def test_backtest_engine_records_canonical_provenance() -> None:
    from nexus_scalp.research.backtest import BacktestEngine

    eng = BacktestEngine(assumptions=default_research_assumptions()[0])
    assert eng.assumptions_provenance == "EXPLICIT"
    eng2 = BacktestEngine()
    assert eng2.assumptions_provenance == "PRODUCTION_LIKE_DEFAULT"
    assert eng2.assumptions.spread_ticks > 0  # production-like is NON-zero


def test_backtest_engine_zero_entrypoint_refuses_then_allows() -> None:
    from nexus_scalp.research.backtest import BacktestEngine

    eng = BacktestEngine(assumptions=ExecutionAssumptions())
    assert eng.assumptions_provenance == FALLBACK_ZERO_PROVENANCE
    ds = _tiny_dataset()
    with pytest.raises(ZeroFrictionError):
        eng.run(ds, "e1t", "1")
    result = eng.run(ds, "e1t", "1", allow_zero_friction=True)
    assert result.total_trades == 12


def test_walkforward_engine_records_provenance_and_refuses_zero() -> None:
    from nexus_scalp.research.walkforward import WalkForwardEngine

    eng = WalkForwardEngine()
    assert eng.assumptions_provenance in {CANONICAL_COSTS_PROVENANCE, FALLBACK_ZERO_PROVENANCE}
    eng_zero = WalkForwardEngine(assumptions=ExecutionAssumptions())
    assert eng_zero.assumptions_provenance == "EXPLICIT"
    with pytest.raises(ZeroFrictionError):
        eng_zero.validate(_tiny_dataset(), "e1t", "1")  # type: ignore[arg-type]
    eng_zero.validate(_tiny_dataset(), "e1t", "1", allow_zero_friction=True)  # type: ignore[arg-type]


def test_oos_gate_records_provenance_and_refuses_zero() -> None:
    from nexus_scalp.research.oos import OOSGate

    gate = OOSGate()
    assert gate.assumptions_provenance in {CANONICAL_COSTS_PROVENANCE, FALLBACK_ZERO_PROVENANCE}
    gate_zero = OOSGate(assumptions=ExecutionAssumptions())
    with pytest.raises(ZeroFrictionError):
        gate_zero.evaluate(_tiny_dataset(), "e1t", "1")  # type: ignore[arg-type]
    gate_zero.evaluate(_tiny_dataset(), "e1t", "1", allow_zero_friction=True)  # type: ignore[arg-type]


def test_robustness_engine_records_provenance_and_refuses_zero() -> None:
    from nexus_scalp.research.robustness import RobustnessEngine

    eng = RobustnessEngine()
    assert eng.assumptions_provenance in {CANONICAL_COSTS_PROVENANCE, FALLBACK_ZERO_PROVENANCE}
    eng_zero = RobustnessEngine(baseline=ExecutionAssumptions())
    with pytest.raises(ZeroFrictionError):
        eng_zero.evaluate(_tiny_dataset(), "e1t", "1")
    eng_zero.evaluate(_tiny_dataset(), "e1t", "1", allow_zero_friction=True)


# ---------------------------------------------------------------------------
# 4. Paper adapter — canonical spread band (E2)
# ---------------------------------------------------------------------------


def test_paper_adapter_metal_spread_band_resolves_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

    # Sanity: the env does not override the canonical path.
    monkeypatch.delenv("NEXUS_EXECUTION_ASSUMPTIONS", raising=False)
    adapter = PaperMT5Adapter(symbol="XAUUSD")
    lo, hi = adapter._metal_spread_range
    # The canonical paper_model band is (0.08, 0.18) in the tracked artifact;
    # the assertion pins "band came from the canonical block" generally.
    assert 0.0 < lo < hi
    assert adapter._resolve_canonical_metal_spread_range() == (lo, hi)


def test_paper_adapter_band_matches_canonical_paper_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.configuration.execution_costs import get_execution_assumptions

    monkeypatch.delenv("NEXUS_EXECUTION_ASSUMPTIONS", raising=False)
    canonical = PaperMT5Adapter._resolve_canonical_metal_spread_range()
    paper_model = get_execution_assumptions().paper_model
    assert paper_model is not None
    expected = paper_model.spread_band_usd
    assert canonical == expected
    adapter = PaperMT5Adapter(symbol="XAUUSD")
    assert adapter._metal_spread_range == expected


def test_paper_adapter_band_falls_back_with_warning_when_canonical_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

    monkeypatch.setenv("NEXUS_EXECUTION_ASSUMPTIONS", "/nonexistent/does/not/exist.json")
    fallback = PaperMT5Adapter._resolve_canonical_metal_spread_range()
    assert fallback == PaperMT5Adapter._METAL_SPREAD_RANGE == (0.08, 0.18)
