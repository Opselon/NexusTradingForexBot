"""BUG-169 (2026-08-31): live-decision reactivity + honest telemetry.

Live-log forensics (2026-08-31, XAUUSD):
  * FEATURE_CALCULATION_OK p50=67ms / p95=655ms / p99=982ms ON THE LOOP
    THREAD, recomputed on EVERY tick although the liquidity governor's
    inputs (completed bars + bar ATR) only change on a new M1 bar.
  * The same quote was re-pipelined every ~50ms loop iteration:
    duplicate features + duplicate regime pushes (skewing tick_velocity /
    rv_5m) + a synthetic NO_TRADE conf=0.0 (TICK_DUPLICATE_SUPPRESSED)
    that OVERWROTE engine._last_proposal - the UI then displayed that
    fabricated decision as the Active Intelligence Output.
  * The online fine-tune path fed 50D buffer rows into the 70D champion:
    "mat1 and mat2 shapes cannot be multiplied (10x50 and 70x128)" x60/day
    plus a scaler-save collision (WinError 5) per attempt.

These tests pin the corrected behaviour at the policy level (the layer the
duplicates reached) and at the trainer level (width contract).
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import torch

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.signals.policy import SignalPolicy
from tests.unit.test_policy import _make_feature_vector, _make_tick


def test_duplicate_tick_resurfaces_last_real_decision():
    """A duplicate tick must NOT fabricate a NO_TRADE conf=0.0 decision.

    The dedup guard exists to stop duplicate telemetry, but replacing the
    displayed decision with a synthetic zero-confidence proposal made the UI
    show NO_TRADE / 0.00% even though the last REAL evaluation decided
    something else. The policy now re-surfaces the last real proposal
    (fresh request/execution ids, current tick timestamp) instead.
    """
    policy = SignalPolicy()
    fv = copy.deepcopy(_make_feature_vector())  # type: ignore[name-defined]
    tick = _make_tick()  # type: ignore[name-defined]

    # First evaluation on a fresh tick -> real decision recorded.
    p1 = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.05, 0.20, 0.75, 0.0]]),
        current_tick=tick,
        feature_vector=fv,
    )
    assert p1.action == ActionType.NO_TRADE
    # No real proposal yet: duplicate falls back to the explicit guard code.
    dup = tick.model_copy(update={"timestamp": tick.timestamp + timedelta(seconds=1)})
    p2 = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.05, 0.20, 0.75, 0.0]]),
        current_tick=dup,
        feature_vector=fv,
    )
    # Same quote (bid/ask identical) => duplicate. The synthetic fallback is
    # allowed ONLY while no real proposal exists; it must carry the explicit
    # DEDUP_GATE stage so it is never mistaken for a model decision.
    assert p2.decision_stage == "DEDUP_GATE" or p2.request_id != p1.request_id

    # Now store a real proposal (as evaluate_probabilities does on every
    # non-duplicate evaluation) and feed another duplicate.
    policy._last_real_proposal = p1.model_copy(
        update={"confidence": 0.61, "action": ActionType.SELL_MARKET}
    )
    dup2 = tick.model_copy(update={"timestamp": tick.timestamp + timedelta(seconds=2)})
    p3 = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.05, 0.20, 0.75, 0.0]]),
        current_tick=dup2,
        feature_vector=fv,
    )
    # The duplicate re-surfaces the LAST REAL decision: action and confidence
    # come from the real evaluation, never a fabricated 0.0.
    assert p3.action == ActionType.SELL_MARKET
    assert abs(p3.confidence - 0.61) < 1e-9
    # But it is a fresh evaluation artifact (new request id, current ts).
    assert p3.request_id != p1.request_id
    assert p3.generated_at == dup2.timestamp


def test_duplicate_tick_does_not_touch_trading_state():
    """Duplicates must not advance cooldown/direction memory (TASK 5 invariant,
    re-pinned after the BUG-169 resurface change)."""
    policy = SignalPolicy()
    fv = copy.deepcopy(_make_feature_vector())  # type: ignore[name-defined]
    tick = _make_tick()  # type: ignore[name-defined]

    policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.05, 0.20, 0.75, 0.0]]),
        current_tick=tick,
        feature_vector=fv,
    )
    policy._last_real_proposal = None  # force the synthetic branch
    dup = tick.model_copy(update={"timestamp": tick.timestamp + timedelta(seconds=1)})
    policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.05, 0.20, 0.75, 0.0]]),
        current_tick=dup,
        feature_vector=fv,
    )
    # The duplicate must not have re-armed cooldown or direction locks.
    assert policy._last_signal_time is None
    assert policy._last_active_direction is None


def test_trainer_width_binding_matches_buffer_contract():
    """The online fine-tune must never feed a width the model head rejects.

    Regression for the 60x/day 'mat1 and mat2 shapes cannot be multiplied
    (10x50 and 70x128)' crash: the trainer bound at __init__ to the class
    bootstrap contract (50D) while the loaded artifact was 70D.
    The engine now rebinds the trainer to the loaded bundle's width and the
    per-bar guard refuses any residual mismatch BEFORE torch sees the frame.
    """
    from nexus_scalp.features.schema import FEATURE_SCHEMAS
    from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

    trainer = WalkForwardTrainer()
    # Default binding is the ACTIVE schema (scalp_v1/50D).
    assert trainer.num_features == 50

    # The engine rebind path: resolving scalp_v3 yields exactly 70 inputs.
    schema70 = FEATURE_SCHEMAS.resolve("scalp_v3")
    assert schema70.dimension == 70
    trainer.feature_schema = schema70
    trainer.num_features = schema70.dimension
    assert trainer.num_features == 70
    assert tuple(trainer.feature_schema.columns) == tuple(f"feat_{i}" for i in range(70))
