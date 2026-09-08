"""TASK-AUDREV-C4 (audit rev2) — policy live-ticket symbol/magic hardcode removal.

Census finding (audit rev2, finding C4): every live-ticket match site in
``SignalPolicy`` hardcoded ``t_symbol == "XAUUSD" and t_magic == 888101``
(4 sites). The lock was purely in the policy layer (nse-cli dataset tooling
is already symbol-parameterized), which made the policy structurally blind to:

  * any live ticket on a second symbol (EURUSD multi-symbol unlock goal),
  * any reconfigured execution magic.

Contract after the fix:
  * the expected identity is resolved ONCE per evaluate call via
    ``SignalPolicy._expected_live_ticket_identity(current_tick)``:
    symbol = the tick being evaluated's symbol, magic = the configured
    execution magic (robust getattr chains with the ExecutionConfig
    default 888101 as documented fallback);
  * with the default configuration (tick symbol XAUUSD, magic 888101) the
    resolved pair is identical to the old literals — matching semantics for
    the configured path are byte-identical (regression pinned below);
  * a matching EURUSD live ticket against an EURUSD tick NOW matches
    (was impossible before the fix);
  * mismatches (magic or symbol) must still not match;
  * the ticket ``magic`` / ``magic_number`` alias-key read is preserved.

xdist-safe: fresh SignalPolicy per test, no shared filesystem state,
module-logger untouched (BUG-112/118 rules).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import torch

from nexus_scalp.configuration.config import AlgoConfig, ExecutionConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy

#: Audit C4 historical literals — the configured defaults that must keep
#: matching byte-identically (regression pin).
HARDCODED_SYMBOL = "XAUUSD"
HARDCODED_MAGIC = 888101


class MockOrderManager:
    """Same live-ticket fake the existing policy tests use (test_policy.py)."""

    def __init__(self, live_tickets=None):
        self.live_tickets = live_tickets or []

    def get_active_live_tickets(self):
        return self.live_tickets


def _feature_vector(symbol: str, tick: TickData, displacement: float = 0.5) -> FeatureVector:
    """Zone-neutral flat fixture mirroring tests/unit/test_policy.py."""
    return FeatureVector(
        symbol=symbol,
        timestamp_utc=tick.timestamp.isoformat(),
        live_tick_displacement=displacement,
        log_return_m1=0.0,
        atr_m1=2.00,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.8,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.5,
        consecutive_momentum_count=1.0,
        dist_to_swing_high_20=2.0,
        dist_to_swing_low_20=2.0,
        price_compression_flag_ratio=1.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=True,
        session_london=False,
        session_ny=False,
        session_overlap_london_ny=False,
        lag_1_log_return=0.0,
        lag_2_log_return=0.0,
        lag_3_log_return=0.0,
        lag_1_atr_ratio=1.0,
        lag_1_volume_z=0.0,
        lag_1_clv=0.0,
        fvg_bullish_active=False,
        fvg_bearish_active=False,
        order_block_type=0,
        liquidity_sweep_signal=0,
        choch_bullish=False,
        choch_bearish=False,
        broke_previous_high=False,
        broke_previous_low=False,
        rapid_reversal_spike=False,
        rapid_reversal_spike_val=0.0,
        tenkan_sen=2000.0,
        kijun_sen=2000.0,
        senkou_span_a=2000.0,
        senkou_span_b=2000.0,
        tk_cross_signal=0,
        is_above_kumo=True,
        is_below_kumo=False,
        rsi_14=50.0,
        dist_to_ema_21=1.0,
        dist_to_ema_50=1.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=1.0,
        htf_h1_momentum=1.0,
        htf_m30_structure=1.0,
        htf_m15_confirmation=1.0,
        support_zone_dist=5.0,
        resistance_zone_dist=5.0,
        trend_strength=1.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )


def _policy_with_configured_identity(symbol: str, magic: int, price_scale: str = "fx") -> SignalPolicy:
    """Policy whose execution config carries the (symbol, magic) under test.

    Mirrors the live wiring (live_engine passes ``config.algo``); the
    execution section is injected onto a stand-in config object the way
    ``AppConfig`` composes it (``config.algo.execution`` attribute access),
    without violating AlgoConfig's pydantic field contract.
    ``price_scale`` selects the fixture price family ("fx" for EURUSD-scale
    quotes, "gold" for XAUUSD-scale quotes) so the candidate SL/TP builder
    (which derives absolute levels from the quote) stays valid.
    """

    class _ConfiguredAlgoConfig(AlgoConfig):
        """AlgoConfig + an execution section, as AppConfig composition sees it."""

        execution: ExecutionConfig = ExecutionConfig()

    policy = SignalPolicy(algo_config=_ConfiguredAlgoConfig(execution=ExecutionConfig(symbol=symbol, magic_number=magic)))  # type: ignore[arg-type]
    policy.confidence_threshold = 0.10
    policy.algo_config.min_risk_reward_ratio = 0.10
    return policy


def _policy_with_default_identity() -> SignalPolicy:
    """Policy with NO execution section on algo_config (getattr fallback path)."""
    policy = SignalPolicy(algo_config=AlgoConfig())
    policy.confidence_threshold = 0.10
    policy.algo_config.min_risk_reward_ratio = 0.10
    return policy


# ---------------------------------------------------------------------------
# (a) Regression pin — configured path (XAUUSD + 888101) matches as before
# ---------------------------------------------------------------------------


def test_c4_configured_path_reentry_blocked_with_live_order() -> None:
    """XAUUSD tick + XAUUSD/888101 live POSITION ticket near entry -> blocked.

    Byte-identical regression pin for the pre-C4 behavior (same fixture as
    tests/unit/test_policy.py::test_same_level_reentry_blocked_triggers_with_live_order).
    """
    policy = _policy_with_default_identity()
    om = MockOrderManager(
        live_tickets=[
            {
                "ticket": 999,
                "symbol": HARDCODED_SYMBOL,
                "price": 2000.00,
                "magic": HARDCODED_MAGIC,
                "type": "POSITION",
            }
        ]
    )
    tick = TickData(
        symbol=HARDCODED_SYMBOL, timestamp=datetime.now(UTC), bid=2000.0, ask=2000.10, volume=1.0
    )

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=_feature_vector(HARDCODED_SYMBOL, tick),
        order_manager=om,
    )

    assert proposal.action == ActionType.NO_TRADE
    assert "SAME_LEVEL_REENTRY_BLOCKED" in proposal.reason_code


def test_c4_configured_path_clears_lock_when_no_live_orders() -> None:
    """XAUUSD tick + empty live tickets -> same-level lock released (as before)."""
    policy = _policy_with_default_identity()
    policy._last_active_direction = ActionType.BUY_MARKET
    policy._last_executed_price = 2000.0
    policy.last_order_price = 2000.0
    om = MockOrderManager(live_tickets=[])
    tick = TickData(
        symbol=HARDCODED_SYMBOL, timestamp=datetime.now(UTC), bid=2000.0, ask=2000.2, volume=1.0
    )

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=_feature_vector(HARDCODED_SYMBOL, tick),
        order_manager=om,
    )

    assert proposal.action == ActionType.BUY_MARKET
    assert "SAME_LEVEL_REENTRY_BLOCKED" not in proposal.reason_code


def test_c4_expected_identity_defaults_to_configured_literals() -> None:
    """_expected_live_ticket_identity resolves to the audit literals by default."""
    policy = _policy_with_default_identity()
    tick = TickData(
        symbol=HARDCODED_SYMBOL, timestamp=datetime.now(UTC), bid=2000.0, ask=2000.2, volume=1.0
    )
    assert policy._expected_live_ticket_identity(tick) == (HARDCODED_SYMBOL, HARDCODED_MAGIC)


def test_c4_expected_identity_reads_configured_execution_section() -> None:
    """A configured execution section feeds the resolver (symbol + magic)."""
    policy = _policy_with_configured_identity("EURUSD", 777123)
    tick = TickData(
        symbol="EURUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.2, volume=1.0
    )
    assert policy._expected_live_ticket_identity(tick) == ("EURUSD", 777123)


# ---------------------------------------------------------------------------
# (b) Multi-symbol unlock — EURUSD tick + matching EURUSD ticket NOW matches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alias_key", ["magic", "magic_number"])
def test_c4_multi_symbol_eurusd_live_ticket_matches(alias_key: str) -> None:
    """EURUSD tick + EURUSD live ticket with the configured magic is now seen.

    The exposure gate (MAX_TOTAL_EXPOSURE=1) must count the EURUSD ticket —
    the pre-C4 hardcode made this evaluation impossible (structurally blind).
    Asserts the same-level re-entry block fires via the exposure path, and
    the C4 evidence fields land in the decision payload.
    """
    policy = _policy_with_configured_identity("EURUSD", 777123)
    om = MockOrderManager(
        live_tickets=[
            {
                "ticket": 1001,
                "symbol": "EURUSD",
                "price": 2000.05,
                "type": "POSITION",
                alias_key: 777123,
            }
        ]
    )
    # EURUSD-symbol tick quoted on the gold fixture price family so the
    # candidate SL/TP builder (absolute levels derived from the quote)
    # produces a valid frozen TradeProposal — the C4 delta under test is
    # the ticket identity match, not the price scale.
    tick = TickData(
        symbol="EURUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.10, volume=1.0
    )

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=_feature_vector("EURUSD", tick),
        order_manager=om,
    )

    # With exposure=1 and the ticket within $0.50 of the proposed entry
    # (0.05 < 0.50), the re-entry gate must block as SAME_LEVEL_REENTRY.
    assert proposal.action == ActionType.NO_TRADE
    assert "SAME_LEVEL_REENTRY_BLOCKED" in proposal.reason_code
    # TASK-AUDREV-C4 evidence (additive): expected identity is surfaced.
    rc = proposal.risk_checks or {}
    assert rc.get("expected_symbol") == "EURUSD"
    assert rc.get("expected_magic") == 777123


# ---------------------------------------------------------------------------
# (c) Magic mismatch must not match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alias_key", ["magic", "magic_number"])
def test_c4_magic_mismatch_does_not_match(alias_key: str) -> None:
    """A ticket with the right symbol but a foreign magic is NOT the bot's."""
    policy = _policy_with_configured_identity("EURUSD", 777123)
    om = MockOrderManager(
        live_tickets=[
            {
                "ticket": 1002,
                "symbol": "EURUSD",
                "price": 2000.05,
                "type": "POSITION",
                alias_key: 999999,  # foreign magic
            }
        ]
    )
    tick = TickData(
        symbol="EURUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.10, volume=1.0
    )

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=_feature_vector("EURUSD", tick),
        order_manager=om,
    )

    # No matching live order -> no same-level block, BUY passes the gates.
    assert proposal.action == ActionType.BUY_MARKET
    assert "SAME_LEVEL_REENTRY_BLOCKED" not in proposal.reason_code


# ---------------------------------------------------------------------------
# (d) Symbol mismatch must not match
# ---------------------------------------------------------------------------


def test_c4_symbol_mismatch_does_not_match() -> None:
    """A foreign-symbol ticket (right magic) must not block or count as ours."""
    policy = _policy_with_configured_identity("EURUSD", 777123)
    om = MockOrderManager(
        live_tickets=[
            {
                "ticket": 1003,
                "symbol": "GBPUSD",  # foreign symbol
                "price": 2000.05,
                "magic": 777123,
                "type": "POSITION",
            }
        ]
    )
    tick = TickData(
        symbol="EURUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.10, volume=1.0
    )

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=_feature_vector("EURUSD", tick),
        order_manager=om,
    )

    assert proposal.action == ActionType.BUY_MARKET
    assert "SAME_LEVEL_REENTRY_BLOCKED" not in proposal.reason_code


# ---------------------------------------------------------------------------
# (e) magic_number alias key preserved (policy reads magic OR magic_number)
# ---------------------------------------------------------------------------


def test_c4_magic_number_alias_key_still_matches_on_configured_path() -> None:
    """XAUUSD ticket carrying only ``magic_number`` still matches (alias kept)."""
    policy = _policy_with_default_identity()
    om = MockOrderManager(
        live_tickets=[
            {
                "ticket": 1004,
                "symbol": HARDCODED_SYMBOL,
                "price": 2000.00,
                "magic_number": HARDCODED_MAGIC,  # alias key, no "magic" key
                "type": "POSITION",
            }
        ]
    )
    tick = TickData(
        symbol=HARDCODED_SYMBOL, timestamp=datetime.now(UTC), bid=2000.0, ask=2000.10, volume=1.0
    )

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=_feature_vector(HARDCODED_SYMBOL, tick),
        order_manager=om,
    )

    assert proposal.action == ActionType.NO_TRADE
    assert "SAME_LEVEL_REENTRY_BLOCKED" in proposal.reason_code


def test_c4_alias_key_prefers_magic_then_falls_back() -> None:
    """``magic`` wins when both keys exist (documented read order preserved)."""
    policy = _policy_with_configured_identity("EURUSD", 777123)
    om = MockOrderManager(
        live_tickets=[
            {
                "ticket": 1005,
                "symbol": "EURUSD",
                "price": 2000.05,
                "magic": 777123,
                "magic_number": 42,  # must be ignored: magic takes precedence
                "type": "POSITION",
            }
        ]
    )
    tick = TickData(
        symbol="EURUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.10, volume=1.0
    )

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=_feature_vector("EURUSD", tick),
        order_manager=om,
    )

    assert "SAME_LEVEL_REENTRY_BLOCKED" in proposal.reason_code
