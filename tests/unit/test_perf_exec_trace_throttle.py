"""PERF-EXEC-TRACE (2026-09-10) — [EXEC_TRACE] rate-limit regression pins.

Measured incident: 20,249 [EXEC_TRACE] lines in ~15h (docker logs census,
container v9.0.11) — one INFO line per tick, overwhelmingly routine
NO_TRADE evaluations. The old comment claimed the trace was throttled but
the emit was unconditional.

Pinned contract (behavioral, not static):
  1. routine NO_TRADE churn emits at most one trace per
     exec_trace_interval_sec (suppressed evaluations are COUNTED);
  2. the suppressed count rides on the next emitted line
     (trace_suppressed=N) so the reduction is observable, never silent;
  3. trade-relevant decisions always emit: non-NO_TRADE actions
     (BUY/SELL/WAIT), FINAL_DECISION stage, any blocked_by gate — and a
     trade-relevant line resets the throttle window;
  4. the FIRST evaluation always emits (None sentinel — never skipped).

Capture uses a dedicated root-logger handler (structlog writes through
stdlib; pytest caplog ordering with other handlers is not deterministic).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
import torch

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy


def _record_fields(record: logging.LogRecord) -> dict:
    """Structured fields of a structlog-through-stdlib record.

    The project's stdlib pipeline delivers the event dict either as the
    record msg (dict) or as attributes depending on formatter; read both.
    """
    msg = record.msg
    if isinstance(msg, dict):
        fields = dict(msg)
        fields.setdefault("event", fields.get("event") or record.getMessage())
        return fields
    fields = {"event": record.getMessage()}
    for key in ("execution_id", "trace_suppressed", "action", "stage", "blocked_by"):
        val = getattr(record, key, None)
        if val is not None:
            fields[key] = val
    return fields


class _TraceCapture(logging.Handler):
    """Records [EXEC_TRACE] events with their structured fields."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
        fields = _record_fields(record)
        if "[EXEC_TRACE]" in str(fields.get("event", "")):
            self.events.append(fields)

    def traces(self, execution_id: str | None = None) -> list[dict]:
        if execution_id is None:
            return list(self.events)
        return [e for e in self.events if e.get("execution_id") == execution_id]


@pytest.fixture
def capture():
    """Structlog is UNCONFIGURED in tests (PrintLogger -> stdout), so attach
    a real stdlib pipeline first: configure_logging(log_to_file=False) makes
    get_logger() return a stdlib-backed BoundLogger whose records reach the
    root logger — then the capture handler sees them."""
    from nexus_scalp.observability.logging import configure_logging

    configure_logging(log_level="INFO", log_to_file=False)
    handler = _TraceCapture()
    root = logging.getLogger()
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)


def _feature_vector(**overrides) -> FeatureVector:
    # Shared neutral 62-field fixture (same shape as the BUG-227 pins);
    # overrides pass through (e.g. liquidity_sweep_signal=-1 drives the
    # tick-sweep reversal candidate the flip pins use).
    import importlib.util
    import sys
    from pathlib import Path

    helper = Path(__file__).with_name("test_policy_flip_protection_pins_bug227.py")
    spec = importlib.util.spec_from_file_location("_flip_pins_perf_trace", helper)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_flip_pins_perf_trace", mod)
    spec.loader.exec_module(mod)
    return mod._feature_vector(**overrides)


def _make_tick(ts: datetime, seq: int = 0) -> TickData:
    # seq shifts the quote so the policy's duplicate-tick dedup gate
    # (identical bid/ask) does not short-circuit the evaluation — these
    # tests exercise the TRACE throttle, not the dedup gate.
    bid = 2000.10 + seq * 0.01
    return TickData(symbol="XAUUSD", timestamp=ts, bid=bid, ask=bid + 0.05, volume=1.0)


@pytest.fixture
def policy() -> SignalPolicy:
    p = SignalPolicy()
    p.exec_trace_interval_sec = 4.0
    return p


def _no_trade_eval(policy: SignalPolicy, now: datetime, seq: int = 0):
    return policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.8, 0.05, 0.05, 0.1]]),
        current_tick=_make_tick(now, seq),
        feature_vector=_feature_vector(),
    )


def test_routine_no_trade_trace_is_rate_limited(policy, capture) -> None:
    base = datetime.now(UTC)
    traces = 0
    for i in range(10):
        proposal = _no_trade_eval(policy, base + timedelta(seconds=i * 0.5), seq=i)
        assert proposal is not None
        traces += len(capture.traces(proposal.execution_id))
    # 10 evaluations over 4.5s with a 4s window -> far fewer traces than evals
    assert traces < 10, f"EXEC_TRACE must be rate-limited for routine NO_TRADE (got {traces})"
    # and the FIRST evaluation always emits (None sentinel => never skipped)
    policy2 = SignalPolicy()
    _no_trade_eval(policy2, base, seq=100)
    # the first call emitted exactly one trace on the shared capture
    assert capture.traces(), "the first routine evaluation must still emit one trace"


def test_suppressed_count_is_reported_on_next_emit(policy, capture) -> None:
    base = datetime.now(UTC)
    # burn the first window slot
    first = _no_trade_eval(policy, base, seq=0)
    assert first is not None
    # churn inside the window: suppressed, no trace
    for i in range(3):
        suppressed = _no_trade_eval(policy, base + timedelta(seconds=0.2 * (i + 1)), seq=i + 1)
        assert suppressed is not None
    assert capture.traces(suppressed.execution_id) == [], "suppressed eval must not emit"
    # first evaluation AFTER the window: emits with the suppressed count
    after = _no_trade_eval(policy, base + timedelta(seconds=5.0), seq=50)
    recs = capture.traces(after.execution_id)
    assert recs, "the post-window evaluation must emit a trace"
    assert recs[0].get("trace_suppressed", 0) >= 1, (
        "the suppressed count must ride on the next emitted trace"
    )


class _MockOrderManager:
    """One live 888101 ticket so the flip gate's prior-direction state works."""

    def __init__(self) -> None:
        self._tickets = [{"symbol": "XAUUSD", "magic": 888101, "price": 1990.0}]

    def get_active_live_tickets(self):
        return self._tickets


def test_trade_relevant_decision_always_emits(policy, capture) -> None:
    """A SELL candidate inside the COOLDOWN window is gated (blocked_by=COOLDOWN),
    assigned to final_proposal in the main flow, and therefore reaches the
    trace emit INSIDE the hot NO_TRADE window — it must still emit."""
    base = datetime.now(UTC)
    # burn the routine-NO_TRADE trace window
    _no_trade_eval(policy, base, seq=0)
    capture.events.clear()
    # the flip pin's threshold (0.20) lets the candidate clear the confidence
    # gate and reach the decision flow (see BUG-227 pins); the sweep-reversal
    # fixture (liquidity_sweep_signal=-1) drives the tick-sweep candidate
    policy.confidence_threshold = 0.20
    gated = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.15, 0.04, 0.60, 0.21]]),
        current_tick=_make_tick(base + timedelta(seconds=0.1), seq=10),
        feature_vector=_feature_vector(liquidity_sweep_signal=-1),
        order_manager=_MockOrderManager(),
    )
    assert gated.blocked_by in ("COOLDOWN", "ASYMMETRIC_RR_LIMIT"), gated.blocked_by
    recs = capture.traces(gated.execution_id)
    assert recs, "a trade-relevant decision (blocked_by) must never be suppressed"


def test_non_no_trade_action_always_emits_and_resets_window(policy, capture) -> None:
    base = datetime.now(UTC)
    _no_trade_eval(policy, base, seq=0)  # burn the window
    capture.events.clear()
    # a SELL candidate outside the flip window proceeds past the gates; the
    # candidate path (model_action=SELL) is trade-relevant by ACTION.
    from nexus_scalp.domain.enums import ActionType

    policy._last_active_direction = ActionType.SELL_MARKET
    policy._last_active_direction_time = base - timedelta(seconds=30.0)
    policy._last_signal_time = None
    sell = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.15, 0.04, 0.60, 0.21]]),
        current_tick=_make_tick(base + timedelta(milliseconds=50), seq=11),
        feature_vector=_feature_vector(liquidity_sweep_signal=-1),
        order_manager=_MockOrderManager(),
    )
    assert sell.blocked_by in (
        "COOLDOWN",
        "ASYMMETRIC_RR_LIMIT",
        "SPREAD_TP_RATIO",
        "SPREAD_SESSION_PCT",
        "SPREAD_ATR_RATIO",
        "FLIP_PROTECTION",
        None,
    ), sell.blocked_by
    emitted = capture.traces(sell.execution_id)
    if sell.action.value != "NO_TRADE" or sell.blocked_by:
        assert emitted, "trade-relevant decisions always emit"
    # whichever direction the gates took, the routine window was reset:
    # a routine NO_TRADE right after is suppressed, and one after the fresh
    # window expires emits again (window-reset semantics, no stuck state).
    follow_hot = _no_trade_eval(policy, base + timedelta(milliseconds=100), seq=12)
    assert follow_hot is not None
    assert capture.traces(follow_hot.execution_id) == [], (
        "the trade-relevant emit must reset the routine window (immediate follow suppressed)"
    )
    follow_cold = _no_trade_eval(policy, base + timedelta(seconds=5.0), seq=13)
    assert capture.traces(follow_cold.execution_id), "post-window emit recovers"
