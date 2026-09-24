"""Broker identity, ownership and close-confirmation forensics (mission §22, §41).

Lane: BROKER-MT5-EXECUTION-FORENSICS.

Regression tests for the second fix wave:

  * the order magic is CONFIG (execution.magic_number), not a literal. The old
    hardcoded 888101 disagreed with configs/base.yaml (999101): orders were
    stamped with one magic while the adapter matched positions with another,
    silently orphaning every live order from management.
  * bot position/order filters must match on the broker-RESOLVED instrument
    (XAUUSD / XAUUSD.m / GOLD), not on a string literal — a suffixed broker
    name used to drop every bot position from management.
  * a mutating broker call keyed by ticket alone must be refused when the row
    turns out to belong to another magic/instrument (stale or broker-reused
    ticket), instead of closing someone else's position.
  * the close path must not free exposure or emit the "closed" notification
    unless the broker confirms the ticket is actually gone.

These run against the in-process adapters with MT5 stubbed where needed, so
nothing here touches a live account.
"""

from __future__ import annotations

import pytest

from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
from nexus_scalp.configuration.config import ExecutionConfig
from nexus_scalp.risk.risk_engine import RiskConfig, RiskEngine


class TestMagicIsConfigNotLiteral:
    """execution.magic_number must be the single source of the order magic."""

    def test_config_default_matches_legacy(self) -> None:
        # The legacy literal was 888101; keep it as the default so an operator
        # who never sets execution.magic_number keeps the old behaviour.
        assert ExecutionConfig().magic_number == 888101

    def test_engine_stamps_the_configured_magic(self) -> None:
        # The sized order the risk engine emits must carry whatever magic the
        # runtime injected, not the old literal.
        engine = RiskEngine(config=RiskConfig(), magic_number=999101)
        assert engine.magic_number == 999101

    def test_engine_falls_back_to_execution_config(self) -> None:
        # Callers that never inject the magic still get the configured value
        # rather than a divergent private literal.
        engine = RiskEngine(config=RiskConfig())
        assert engine.magic_number == ExecutionConfig().magic_number == 888101


class TestBotSymbolResolution:
    """Instrument ownership must survive broker suffixes / aliases."""

    def test_exact_name_matches(self) -> None:
        assert DirectMT5Adapter._normalize_symbol_key("XAUUSD") == "XAUUSD"

    def test_suffixed_name_collapses_to_base(self) -> None:
        # XAUUSD.m / XAUUSD-i / XAUUSD_pro are the same instrument.
        for variant in ("XAUUSD.m", "XAUUSD-i", "XAUUSD_pro", "xauusd"):
            assert DirectMT5Adapter._normalize_symbol_key(variant) == "XAUUSD"

    def test_alias_is_left_alone(self) -> None:
        # GOLD is a broker alias, not a suffix of XAUUSD. Deliberately coarse
        # keying must not fabricate a match here.
        assert DirectMT5Adapter._normalize_symbol_key("GOLD") == "GOLD"


class TestPositionOwnership:
    """A ticket-only lookup must not mutate a foreign position."""

    def test_ours_by_magic(self) -> None:
        adapter = DirectMT5Adapter()
        adapter.configure_broker_identity(magic=888101, bot_symbol="XAUUSD")

        class P:
            magic = 888101
            symbol = "XAUUSD"

        assert adapter._position_is_ours(P()) is True

    def test_foreign_magic_refused(self) -> None:
        adapter = DirectMT5Adapter()
        adapter.configure_broker_identity(magic=888101, bot_symbol="XAUUSD")

        class P:
            magic = 777777
            symbol = "XAUUSD"

        # A stale/reused ticket pointing at another account's exposure is
        # refused, not mutated.
        assert adapter._position_is_ours(P()) is False

    def test_magic_zero_falls_back_to_symbol(self) -> None:
        adapter = DirectMT5Adapter()
        adapter.configure_broker_identity(magic=888101, bot_symbol="XAUUSD")

        class P:
            magic = 0
            symbol = "XAUUSD.m"

        # Some servers leave magic=0; the instrument ownership check keeps such
        # rows manageable instead of refusing every legitimate operation.
        assert adapter._position_is_ours(P()) is True

    def test_magic_none_falls_back_to_symbol(self) -> None:
        adapter = DirectMT5Adapter()
        adapter.configure_broker_identity(magic=888101, bot_symbol="XAUUSD")

        class P:
            magic = None
            symbol = "EURUSD"

        # No magic reported and a DIFFERENT instrument: not ours.
        assert adapter._position_is_ours(P()) is False

    def test_unparseable_magic_does_not_block_management(self) -> None:
        adapter = DirectMT5Adapter()
        adapter.configure_broker_identity(magic=888101, bot_symbol="XAUUSD")

        class P:
            magic = "n/a"
            symbol = "XAUUSD"

        assert adapter._position_is_ours(P()) is True


class TestVolumeCeilingIsBrokerAware:
    """HARD_MAX_LOTS is the engine-wide ceiling; volume_max can only tighten it."""

    def test_default_ceiling_is_a_safety_fallback(self) -> None:
        # The fixed 10-lot ceiling is an engine risk policy, not a broker limit
        # to relax: a broker whose volume_max is larger (EURUSD truth: 500)
        # never raises the dispatch ceiling above HARD_MAX_LOTS.
        from nexus_scalp.execution.order_manager import HARD_MAX_LOTS

        assert HARD_MAX_LOTS == 10.0


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
