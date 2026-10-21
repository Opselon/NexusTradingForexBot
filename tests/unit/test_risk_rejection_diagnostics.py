"""OBS-013 companion: the risk-engine rejection lines that dominated the soak
report ("Proposal rejected: ...", 60+ identical warnings in 120s) used to carry
NO diagnostic context at all. The gates now log the symbol, the distances and a
reason code, so an operator can tell a broker-rule artefact (stops_level) from a
stale-price bug - and so the flood is groupable downstream.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import AccountInfo, SymbolInfo, TickData, TradeProposal
from nexus_scalp.observability.logging import configure_logging
from nexus_scalp.risk.risk_engine import RiskEngine


class _Capture(logging.Handler):
    """structlog routes through stdlib logging, so a handler on the engine
    logger sees the real records. Capture the rendered event dict."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.events: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        payload = record.msg
        if isinstance(payload, dict):
            self.events.append(payload)


@pytest.fixture(scope="module", autouse=True)
def _pipeline() -> None:
    """Configure the real logging pipeline.

    Without configure_logging, structlog keeps its default PrintLogger and
    writes straight to stdout, bypassing stdlib logging entirely - which is
    why a handler on the engine logger would capture nothing. The production
    pipeline routes through ProcessorFormatter onto root handlers instead.
    """
    configure_logging(json_format=False, log_to_file=False)


def _account() -> AccountInfo:
    return AccountInfo(
        login=123,
        trade_mode=0,
        leverage=100,
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        margin_free=10000.0,
    )


def _symbol(stops_level: int = 10) -> SymbolInfo:
    # point=0.01 (XAUUSD-shaped) so stops_level=10 => 0.10 minimum distance,
    # exactly the geometry that produced the soak flood.
    return SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=stops_level,
        freeze_level=0,
        trade_contract_size=100.0,
    )


def _proposal(entry: float) -> TradeProposal:
    now = datetime.now(UTC)
    return TradeProposal(
        request_id="req-test-0001",
        symbol="XAUUSD",
        generated_at=now,
        action=ActionType.BUY_LIMIT,
        confidence=0.9,
        proposed_entry=entry,
        stop_loss=entry - 2.0,
        take_profit=entry + 4.0,
        risk_reward_ratio=2.0,
    )


def test_stops_level_rejection_logs_context_and_reason_code() -> None:
    """A pending entry inside the stops level must log WHY, with numbers.

    Before this change the line was bare - 'Proposal rejected: Pending entry
    price is too close to market (Stops Level violation)' - so a soak report
    with 60 identical copies could not distinguish a broker rule from a
    stale-price defect.
    """
    engine = RiskEngine(RiskConfig(risk_per_trade_pct=0.5))
    now = datetime.now(UTC)
    # BUY_LIMIT measures distance as ask - entry, so to be TOO CLOSE the entry
    # must sit just under the ask: ask=2000.20 with entry=2000.12 gives 0.08,
    # under the 0.10 minimum imposed by stops_level=10 * point=0.01.
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=2000.00, ask=2000.20)

    capture = _Capture()
    logger_name = "nexus_scalp.risk.risk_engine"
    target = logging.getLogger(logger_name)
    target.addHandler(capture)
    target.setLevel(logging.WARNING)
    try:
        order = engine.evaluate_proposal(_proposal(2000.12), _account(), _symbol(), [], tick)
    finally:
        target.removeHandler(capture)

    assert order is None, "a stops-level violation must reject the proposal"

    stops = [e for e in capture.events if "Stops Level" in str(e.get("event", ""))]
    assert stops, "no stops-level warning emitted; got: " + str(
        [e.get("event") for e in capture.events]
    )

    rec = stops[0]
    # Structured fields, not just prose: these are what make the line
    # diagnosable and what the flood suppressor can group on.
    assert rec["symbol"] == "XAUUSD"
    assert rec["order_type"] == "BUY_LIMIT"
    assert rec["reason_code"] == "STOPS_LEVEL_ENTRY_TOO_CLOSE"
    assert rec["min_dist_price"] == 0.1
    assert rec["entry_market_dist"] == 0.08
    assert rec["shortfall"] == 0.02
    assert rec["stops_level_points"] == 10
    assert rec["point"] == 0.01


def test_stops_level_rejection_accepts_a_compliant_entry() -> None:
    """The gate must still PASS an entry far enough away - adding context must
    not have changed the decision logic, only its observability."""
    engine = RiskEngine(RiskConfig(risk_per_trade_pct=0.5))
    now = datetime.now(UTC)
    # ask=2000.20, entry=1999.90 => distance 0.30 > 0.10 minimum.
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=2000.00, ask=2000.20)

    capture = _Capture()
    target = logging.getLogger("nexus_scalp.risk.risk_engine")
    target.addHandler(capture)
    target.setLevel(logging.WARNING)
    try:
        order = engine.evaluate_proposal(_proposal(1999.90), _account(), _symbol(), [], tick)
    finally:
        target.removeHandler(capture)

    assert order is not None, "a compliant entry must be accepted"
    assert not [e for e in capture.events if "Stops Level" in str(e.get("event", ""))]
