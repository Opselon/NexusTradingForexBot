"""Clearly-marked SIMULATED TEST DATA (ECOSYSTEM-001, Section 56).

Safe sample position snapshots used by the Test Centre, the CLI test path and
the adapter unit tests. Every constant here is synthetic:

    * The request is tagged ``decision_context_version="SIMULATED_TEST_DATA"``
      so a record written from a test can never be mistaken for a live
      decision in the decision trace or the experience ledger.
    * ``ticket`` is 0 -- no broker ticket, so nothing downstream can mistake it
      for a real position.
    * The numbers are plausible-but-fake (XAUUSD long, in profit), chosen so
      the deterministic policy has a meaningful bracket to evaluate.

A test request MUST NOT create a live order. Nothing in the test path calls
the execution layer: the adapter under test returns evidence, the policy scores
it, and the risk gate evaluates it -- execution is never reached, because the
test harness never asks for eligibility.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nexus_scalp.ai_providers.contract import PositionDecisionRequest

__all__ = ["SIMULATED_SAMPLE_REQUEST", "SIMULATED_CONTEXT_VERSION", "build_simulated_request"]

#: Written into every simulated request so a persisted test decision is
#: unambiguously identifiable as test data.
SIMULATED_CONTEXT_VERSION = "SIMULATED_TEST_DATA"


def build_simulated_request(
    *,
    symbol: str = "XAUUSD",
    side: str = "BUY",
    entry_price: float = 4290.00,
    current_price: float = 4291.65,
    request_id: str = "sim-test-0001",
) -> PositionDecisionRequest:
    """Build a synthetic position snapshot clearly marked as test data.

    ``entry_price`` / ``current_price`` are the only knobs a caller may vary --
    everything else stays fixed so two tests that differ only in price are
    comparable, and so no caller can accidentally construct a "test" request
    carrying real account state.
    """
    is_buy = side == "BUY"
    price_delta = current_price - entry_price if is_buy else entry_price - current_price
    # Synthetic initial risk of $25 per unit-risk, in the position's own units.
    risk_per_unit = 25.0
    unrealized_r = price_delta / risk_per_unit
    tick_size = 0.01
    atr = 2.50
    spread = 0.30
    return PositionDecisionRequest(
        schema_version="1.0.0",
        position_id="sim-0001",
        ticket=0,  # 0 == synthetic; no broker ticket exists for this snapshot
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        current_price=current_price,
        average_price=entry_price,
        volume=0.10,
        position_age_sec=300.0,
        unrealized_pnl=price_delta * 100.0 * 0.10,
        unrealized_r=unrealized_r,
        current_tp=entry_price + (4.0 if is_buy else -4.0),
        current_sl=entry_price - (1.5 if is_buy else -1.5),
        bid=current_price,
        ask=current_price + spread,
        spread=spread,
        spread_relative=spread / max(current_price, 1e-9),
        volatility=atr,
        atr=atr,
        momentum=0.32,
        trend="UP" if is_buy else "DOWN",
        market_structure="HH" if is_buy else "LL",
        regime="TREND",
        regime_confidence=0.72,
        session="LONDON",
        liquidity_state="NORMAL",
        news_state="NONE_SCHEDULED",
        balance=30000.00,
        equity=30041.65,
        margin=120.00,
        free_margin=29921.65,
        margin_level=250.00,
        leverage=100,
        account_currency="USD",
        current_risk=25.00,
        max_allowed_risk=60.00,
        risk_budget=60.00,
        distance_to_sl=abs(current_price - (entry_price - (1.5 if is_buy else -1.5))),
        distance_to_tp=abs(current_price - (entry_price + (4.0 if is_buy else -4.0))),
        reward_to_risk=1.60,
        exposure=429.16,
        model_confidence=0.61,
        model_prediction="HOLD",
        broker="SIMULATED",
        server="SIMULATED-DEMO",
        tick_size=tick_size,
        digits=2,
        volume_step=0.01,
        stops_level=0.0,
        freeze_level=0.0,
        timestamp=datetime(2026, 9, 24, 2, 0, tzinfo=UTC),
        data_freshness_sec=1.0,
        provider_request_id=request_id,
        decision_context_version=SIMULATED_CONTEXT_VERSION,
    )


#: Module-level constant for callers that just need *a* test snapshot.
SIMULATED_SAMPLE_REQUEST: PositionDecisionRequest = build_simulated_request()
