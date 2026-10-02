"""TASK-DEDUP-REPLAY-001 — replay/duplicate decision flood regression tests.

Forensic root cause (2026-10-01, XAUUSD LIVE): after an MT5 IPC reconnect the
broker re-serves its last cached quote; bid/ask drift while the *effective*
decision is unchanged, so the engine's 3-field duplicate guard (ts+bid+ask)
does not fire, the full pipeline runs, and the policy re-surfaces
``_last_real_proposal`` with a FRESH ``request_id``. The executor DEDUP_GATE
net correctly refused each one — 3,476 ``ORDER_MUTATION_SUPPRESSED`` lines in
a single hour.

These tests pin the fix at each layer:

* Part 1 — feed epoch: a reconnect-bumped epoch classifies the first polled
  quote as REACQUIRE, not fresh.
* Part 2 — the ``_last_real_proposal`` replay is gone: a duplicate tick no
  longer produces an actionable proposal with a new request_id.
* Part 3 — semantic decision identity: same intent + different request_id is a
  duplicate; a real parameter change is not.
* Part 4 — broker-state awareness: a broker/order state change re-arms a
  suppressed identity.
* Part 7 — the redactor no longer masks lowercase-word telemetry values.

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m pytest tests/unit/test_decision_dedup_replay.py -p no:cacheprovider -q
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from nexus_scalp.application.live.feed_epoch import FeedEpochTracker, TickClass
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TradeProposal
from nexus_scalp.observability.logging import _redact_value
from nexus_scalp.signals.decision_dedup import (
    ReplayDecisionDedup,
    decision_fingerprint,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SYM = "XAUUSD"
_TICK = 0.001


def _proposal(
    *,
    action: ActionType = ActionType.BUY_LIMIT,
    entry: float = 2650.50,
    sl: float = 2645.00,
    tp: float = 2662.00,
    request_id: str = "REQ-1",
    ticket: int = 0,
    volume: float | None = None,
) -> TradeProposal:
    """Build a valid TradeProposal (action invariants satisfied)."""
    if action in (
        ActionType.SELL,
        ActionType.SELL_MARKET,
        ActionType.SELL_LIMIT,
        ActionType.SELL_STOP,
    ):
        # Pydantic invariant: for a SELL, SL must be strictly ABOVE entry and
        # TP strictly BELOW it — the mirror of the BUY contract.
        sl, tp = max(entry + 5.0, tp), min(entry - 12.0, sl)
    return TradeProposal(
        request_id=request_id,
        symbol=_SYM,
        generated_at=datetime.now(UTC),
        action=action,
        confidence=0.72,
        proposed_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward_ratio=2.0,
        ticket=ticket,
        volume=volume,
    )


class _FakeOrderManager:
    """Minimal order-manager double exposing get_active_live_tickets()."""

    def __init__(self, tickets: list[dict] | None = None) -> None:
        self._tickets = list(tickets or [])

    def get_active_live_tickets(self) -> list[dict]:
        return list(self._tickets)


# ---------------------------------------------------------------------------
# PART 3 — semantic decision identity
# ---------------------------------------------------------------------------


class TestSemanticFingerprint:
    def test_same_intent_different_request_id_is_duplicate(self):
        """The core regression: identical intent, fresh request_id => duplicate."""
        gate = ReplayDecisionDedup()
        om = _FakeOrderManager()

        first = gate.check(_proposal(request_id="REQ-A"), order_manager=om)
        assert first.duplicate is False

        replay = gate.check(_proposal(request_id="REQ-B"), order_manager=om)
        assert replay.duplicate is True, (
            "a replayed decision with a new request_id must be classified a duplicate"
        )

    def test_different_entry_price_is_not_a_duplicate(self):
        gate = ReplayDecisionDedup()
        om = _FakeOrderManager()

        gate.check(_proposal(entry=2650.50), order_manager=om)
        verdict = gate.check(_proposal(entry=2651.00), order_manager=om)
        assert verdict.duplicate is False, "a one-tick entry change is a new decision"

    def test_different_sl_tp_is_not_a_duplicate(self):
        gate = ReplayDecisionDedup()
        om = _FakeOrderManager()

        gate.check(_proposal(sl=2645.00, tp=2662.00), order_manager=om)
        verdict = gate.check(_proposal(sl=2644.00, tp=2662.00), order_manager=om)
        assert verdict.duplicate is False, "a changed SL is a new decision"

    def test_different_action_is_not_a_duplicate(self):
        gate = ReplayDecisionDedup()
        om = _FakeOrderManager()

        gate.check(_proposal(action=ActionType.BUY_LIMIT), order_manager=om)
        verdict = gate.check(_proposal(action=ActionType.SELL_LIMIT), order_manager=om)
        assert verdict.duplicate is False

    def test_float_noise_does_not_defeat_dedup(self):
        """Sub-tick float drift must NOT escape dedup (quantization)."""
        gate = ReplayDecisionDedup()
        om = _FakeOrderManager()

        gate.check(_proposal(entry=2650.5000000001), order_manager=om)
        verdict = gate.check(_proposal(entry=2650.5000000007), order_manager=om)
        assert verdict.duplicate is True, "sub-tick float noise is the same intent"

    def test_non_actionable_proposals_pass_through(self):
        gate = ReplayDecisionDedup()
        for action in (ActionType.NO_TRADE, ActionType.WAIT):
            v = gate.check(_proposal(action=action), order_manager=_FakeOrderManager())
            assert v.duplicate is False
            assert v.fingerprint is None

    def test_fingerprint_excludes_request_id_and_timestamps(self):
        """Identity must not depend on per-replay ephemera."""
        base = _proposal(request_id="X", ticket=0)
        other = _proposal(request_id="Y", ticket=0)
        # generated_at differs by construction (datetime.now), request_id too.
        assert decision_fingerprint(base) == decision_fingerprint(other)

    def test_strategy_revision_change_is_a_new_decision(self):
        """A model/champion swap is a genuine decision change (Part 4)."""
        gate = ReplayDecisionDedup()
        om = _FakeOrderManager()

        gate.check(_proposal(), tick_size=_TICK, strategy_revision="rev-1", order_manager=om)
        verdict = gate.check(
            _proposal(), tick_size=_TICK, strategy_revision="rev-2", order_manager=om
        )
        assert verdict.duplicate is False


# ---------------------------------------------------------------------------
# PART 4 — broker-state awareness
# ---------------------------------------------------------------------------


class TestBrokerStateAwareness:
    def test_broker_state_change_re_arms_suppression(self):
        """A fill/cancel/new position makes the same intent actionable again."""
        gate = ReplayDecisionDedup()
        om = _FakeOrderManager(tickets=[])

        gate.check(_proposal(), order_manager=om)
        assert gate.check(_proposal(), order_manager=om).duplicate is True

        # The broker world changed (a new live ticket appeared).
        om = _FakeOrderManager(tickets=[{"ticket": 5001, "type": "BUY"}])
        verdict = gate.check(_proposal(), order_manager=om)
        assert verdict.duplicate is False, "a broker/order state change must re-arm the decision"

    def test_missing_order_manager_still_dedups_on_identity(self):
        """No order manager => dedup works on identity alone (graceful degrade)."""
        gate = ReplayDecisionDedup()

        gate.check(_proposal())
        assert gate.check(_proposal()).duplicate is True

    def test_reset_forgets_all_suppression(self):
        gate = ReplayDecisionDedup()
        om = _FakeOrderManager()

        gate.check(_proposal(), order_manager=om)
        gate.check(_proposal(), order_manager=om)
        assert gate.total_suppressed == 1

        gate.reset()
        assert gate.total_suppressed == 0
        assert gate.check(_proposal(), order_manager=om).duplicate is False

    def test_snapshot_is_bounded_and_read_only(self):
        gate = ReplayDecisionDedup()
        om = _FakeOrderManager()
        gate.check(_proposal(), order_manager=om)
        snap = gate.snapshot()
        assert snap["active_identities"] == 1
        assert "total_suppressed" in snap


# ---------------------------------------------------------------------------
# PART 1 — feed connection epoch
# ---------------------------------------------------------------------------


class TestFeedEpoch:
    def test_fresh_tick_passes(self):
        tracker = FeedEpochTracker()
        v = tracker.classify(timestamp=_ts(1_000_000), bid=2650.0, ask=2650.5)
        assert v.accept is True

    def test_identical_tick_is_rejected(self):
        tracker = FeedEpochTracker()
        tracker.classify(timestamp=_ts(1_000_000), bid=2650.0, ask=2650.5)
        v = tracker.classify(timestamp=_ts(1_000_000), bid=2650.0, ask=2650.5)
        assert v.accept is False
        assert v.tick_class is TickClass.DUPLICATE

    def test_reconnect_opens_reacquisition_window(self):
        """After a reconnect the first polled quote must NOT be trusted."""
        tracker = FeedEpochTracker()
        tracker.classify(timestamp=_ts(1_000_000), bid=2650.0, ask=2650.5)

        tracker.note_reconnect()

        # The broker re-serves the SAME quote post-reconnect.
        v = tracker.classify(timestamp=_ts(1_000_000), bid=2650.0, ask=2650.5)
        assert v.accept is False
        assert v.tick_class is TickClass.RECONNECT_REPLAY

    def test_reacquire_window_is_bounded(self):
        """The window cannot block the market indefinitely."""
        tracker = FeedEpochTracker()
        tracker.classify(timestamp=_ts(1_000_000), bid=2650.0, ask=2650.5)
        tracker.note_reconnect()

        # Exhaust the bounded re-acquisition window with replayed quotes.
        for _ in range(tracker._max_reacquire_ticks + 4):
            tracker.classify(timestamp=_ts(1_000_000), bid=2650.0, ask=2650.5)

        # A genuinely NEW quote is accepted even though the window is open.
        fresh = tracker.classify(timestamp=_ts(2_000_000), bid=2660.0, ask=2660.5)
        assert fresh.accept is True

    def test_reconnect_epoch_bumps(self):
        tracker = FeedEpochTracker()
        e0 = tracker.epoch
        tracker.note_reconnect()
        assert tracker.epoch > e0

    def test_timestamp_reordering_is_safe(self):
        """A tick with an older broker timestamp must not be accepted as fresh."""
        tracker = FeedEpochTracker()
        tracker.classify(timestamp=_ts(2_000_000), bid=2650.0, ask=2650.5)
        v = tracker.classify(timestamp=_ts(1_000_000), bid=2650.0, ask=2650.5)
        assert v.accept is False


def _ts(msc: int) -> datetime:
    """Broker timestamp from an MT5 time_msc value (ms since epoch, UTC)."""
    return datetime.fromtimestamp(msc / 1000.0, tz=UTC)


def _tick(*, time_msc: int, bid: float, ask: float) -> MagicMock:
    """A tick double exposing only the fields the tracker reads."""
    t = MagicMock()
    t.time_msc = time_msc
    t.timestamp = _ts(time_msc)
    t.bid = bid
    t.ask = ask
    return t


# ---------------------------------------------------------------------------
# PART 2 — the _last_real_proposal replay is removed from the policy
# ---------------------------------------------------------------------------


class TestPolicyReplayRemoved:
    """A duplicate tick must not surface an actionable proposal with a new id."""

    def test_duplicate_tick_yields_no_trade_not_replayed_proposal(self):
        from nexus_scalp.signals.policy import SignalPolicy

        policy = SignalPolicy()
        # Seed the last-real-proposal memory with a genuine decision.
        real = _proposal(request_id="REQ-REAL", action=ActionType.BUY_LIMIT)
        policy._last_real_proposal = real

        probs = [0.10, 0.72, 0.18]  # buy-leaning (index 1)
        verdict = policy._evaluate_duplicate_tick(
            probs=probs,
            current_tick=_make_tick(),
            execution_id="EXEC-1",
            regime_state=None,
        )
        # The replay path is gone: no actionable BUY_LIMIT/SELL_LIMIT with a
        # fresh request_id may be produced by a duplicate tick.
        if verdict is not None:
            assert verdict.action not in (
                ActionType.BUY_LIMIT,
                ActionType.SELL_LIMIT,
            ), "a duplicate tick must not re-emit an actionable limit proposal"
            if verdict.action == ActionType.NO_TRADE:
                assert verdict.decision_stage in (
                    "DEDUP_GATE",
                    "DECISION_DEDUP_REPLAY",
                    "DUPLICATE_TICK",
                )


def _make_tick():
    from nexus_scalp.domain.models import TickData

    return TickData(
        symbol=_SYM,
        timestamp=datetime.now(UTC),
        bid=2650.0,
        ask=2650.5,
        time_msc=int(datetime.now(UTC).timestamp() * 1000),
        volume=1.0,
    )


# ---------------------------------------------------------------------------
# PART 7 — redactor no longer masks lowercase telemetry values
# ---------------------------------------------------------------------------


class TestRedactorTelemetry:
    @pytest.mark.parametrize(
        "token",
        [
            "suppressed_action=replayed",
            "detail=replayed",
            "reason=dedup_replay",
            "tick_class=reacquire",
        ],
    )
    def test_lowercase_telemetry_value_survives(self, token: str):
        assert _redact_value(token) == token

    @pytest.mark.parametrize(
        "token",
        [
            "password=[REDACTED_SECRET]",
            "token=[REDACTED_SECRET]",
            "api_key=[REDACTED_SECRET]",
            "secret=[REDACTED_SECRET]",
        ],
    )
    def test_secret_keys_still_redacted(self, token: str):
        assert _redact_value(token) != token

    def test_uppercase_and_numeric_values_still_survive(self):
        assert _redact_value("event=ORDER_MUTATION_SUPPRESSED") == (
            "event=ORDER_MUTATION_SUPPRESSED"
        )
        assert _redact_value("epoch=3") == "epoch=3"
