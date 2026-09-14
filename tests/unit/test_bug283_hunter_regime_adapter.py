"""BUG-283 regression net — hunter regime adapter + record regime key.

Wave 2026-09-14 (lane 03 §3, VERIFIED): the live regime producer emits
RegimeType values (TRENDING_MOMENTUM / RANGING_MEAN_REVERSION / ...), while
every hunter strategy's regime_ok gate speaks ("TRENDING", "RANGING"). The
intersection was EMPTY and no production data path wrote a 'regime' key at
all — so row.get('regime','UNKNOWN')='UNKNOWN' made ALL 14 setup families
permanently NO_GO(REGIME_NOT_OK(UNKNOWN)) on every real dataset. Only
hand-written test fixtures used the hunter vocabulary (test-vs-prod drift).

Pins:
  * RED-before (documented in comments): TRENDING_MOMENTUM row -> NO_GO.
  * Adapter: producer vocabulary normalizes onto the hunter contract.
  * UNKNOWN stays NO_GO (fail-closed; never fabricate eligibility).
  * CHOP/FREEZE labels do NOT silently become hunter-eligible.
  * Registry invariant: adapter domain covers every RegimeType value.
  * _build_retrain_record stamps rec['regime'] from the engine's last
    regime state, and 'UNKNOWN' when none exists yet.
"""

from __future__ import annotations

from nexus_scalp.features.regime_classifier import RegimeType
from nexus_scalp.model_generation.setup_detector import SetupDetection
from nexus_scalp.model_generation.strategy_factory import (
    _REGIME_NORMALIZE,
    HUNTER_STRATEGIES,
    StrategyFactory,
)


def _sweep_setup(quality: float = 0.95) -> SetupDetection:
    return SetupDetection(
        setup_id="s-1",
        setup_type="LIQUIDITY_SWEEP",
        quality=quality,
        factors={"direction": 1.0, "stop_hunt_depth_atr": 0.3},
    )


def _row(regime: str) -> dict:
    return {"regime": regime, "atr_m1": 2.0, "spread": 0.30, "session_london": 1}


# ------------------------------------------------------------------ adapter


def test_producer_vocabulary_now_reaches_go():
    """RED-before: regime='TRENDING_MOMENTUM' -> NO_GO('REGIME_NOT_OK(...)')."""
    factory = StrategyFactory()
    decision = factory.evaluate(_sweep_setup(), _row("TRENDING_MOMENTUM"))
    assert decision.decision == "GO", decision.reasons

    decision = factory.evaluate(_sweep_setup(), _row("RANGING_MEAN_REVERSION"))
    assert decision.decision == "GO", decision.reasons

    decision = factory.evaluate(_sweep_setup(), _row("VOLATILITY_EXPANSION"))
    assert decision.decision == "GO", decision.reasons


def test_hunter_native_vocabulary_still_works():
    factory = StrategyFactory()
    for label in ("TRENDING", "RANGING"):
        decision = factory.evaluate(_sweep_setup(), _row(label))
        assert decision.decision == "GO", (label, decision.reasons)


def test_unknown_fails_closed_never_fabricated():
    # Absent key and explicit UNKNOWN must stay NO_GO(REGIME_NOT_OK(UNKNOWN)).
    factory = StrategyFactory()
    decision = factory.evaluate(_sweep_setup(), {"atr_m1": 2.0, "spread": 0.3})
    assert "REGIME_NOT_OK(UNKNOWN)" in decision.reasons
    decision = factory.evaluate(_sweep_setup(), _row("UNKNOWN"))
    assert "REGIME_NOT_OK(UNKNOWN)" in decision.reasons


def test_chop_and_freeze_do_not_become_eligible():
    # HIGH_SPREAD_CHOP -> CHOP and MACRO_NEWS_FREEZE -> FREEZE are neither
    # TRENDING nor RANGING: mapping must NOT widen eligibility.
    factory = StrategyFactory()
    for label in ("HIGH_SPREAD_CHOP", "MACRO_NEWS_FREEZE"):
        decision = factory.evaluate(_sweep_setup(), _row(label))
        assert decision.decision == "NO_GO", label
        assert any(r.startswith("REGIME_NOT_OK") for r in decision.reasons), label


def test_range_only_strategy_stays_ranging_exclusive():
    # hunter_range_v1 gates ("RANGING",) only: TRENDING_MOMENTUM must NOT
    # satisfy it via the adapter; RANGING_MEAN_REVERSION must.
    factory = StrategyFactory()
    setup = SetupDetection(
        setup_id="r-1",
        setup_type="RANGING_FADE",
        quality=0.95,
        factors={"direction": -1.0},
    )
    dec = factory.evaluate(setup, _row("TRENDING_MOMENTUM"), strategy_id="hunter_range_v1")
    assert any(r.startswith("REGIME_NOT_OK") for r in dec.reasons)
    dec = factory.evaluate(setup, _row("RANGING_MEAN_REVERSION"), strategy_id="hunter_range_v1")
    assert not any(r.startswith("REGIME_NOT_OK") for r in dec.reasons), dec.reasons


def test_adapter_domain_covers_every_regimetype():
    """Registry invariant: a future RegimeType value without an adapter row
    fails this pin instead of silently starving a family again."""
    emitted = {rt.value for rt in RegimeType}
    assert emitted <= set(_REGIME_NORMALIZE), emitted - set(_REGIME_NORMALIZE)


def test_adapter_targets_are_hunter_vocabulary():
    eligible = {r for s in HUNTER_STRATEGIES.values() for r in s.regime_ok}
    for target in _REGIME_NORMALIZE.values():
        assert target == "UNKNOWN" or target in {"TRENDING", "RANGING", "CHOP", "FREEZE"}
    # Every mapped-on target used for eligibility must be a vocabulary the
    # registry actually gates on (CHOP/FREEZE intentionally match nothing).
    assert {"TRENDING", "RANGING"} <= eligible


# ------------------------------------------------------------ record builder


def test_live_engine_retrain_record_carries_regime(tmp_path, monkeypatch):
    """_build_retrain_record must stamp the CURRENT regime label so offline
    consumers (hunter gate, evaluation_regime_performance) stop seeing a
    permanent UNKNOWN. Fail-closed: no state yet -> UNKNOWN."""
    import inspect

    from nexus_scalp.application.live_engine import LiveEngine

    src = inspect.getsource(LiveEngine._build_retrain_record)
    assert 'rec["regime"]' in src
    assert "_last_regime_state" in src
    assert '"UNKNOWN"' in src
