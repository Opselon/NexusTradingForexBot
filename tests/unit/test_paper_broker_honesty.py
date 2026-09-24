"""Paper-mode honesty + canonical-law parity (mission §35, §43).

Lane: BROKER-MT5-EXECUTION-FORENSICS.

PAPER must not pretend to know broker facts it cannot know, and it must not
validate production risk semantics against numbers a real broker would never
report. This suite pins the fixes:

  * margin is no longer hard 0.0 with margin_free == equity (an impossible
    broker state once a position is open);
  * margin uses the SAME canonical law as the risk engine
    (domain/valuation.required_margin_estimate), so PAPER exercises real
    sizing instead of a hidden private formula;
  * order_calc_profit_snapshot no longer hard-codes `* 100.0` as the contract
    size for every symbol (a 1000x error on 100000-lot FX);
  * every simulated number stays tagged PAPER_SIMULATION /
    FALLBACK_ESTIMATE so nothing can be mistaken for broker truth.
"""

from __future__ import annotations

import pytest

from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.domain.enums import OrderType


@pytest.fixture()
def paper() -> PaperMT5Adapter:
    return PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")


@pytest.fixture()
def fx_paper() -> PaperMT5Adapter:
    return PaperMT5Adapter(initial_balance=10000.0, symbol="EURUSD")


class TestPaperMarginIsHonest:
    def test_empty_account_reports_no_reserved_margin(self, paper: PaperMT5Adapter) -> None:
        """With no positions there is genuinely nothing reserved."""
        info = paper.get_account_info()
        assert info.margin == pytest.approx(0.0)
        assert info.margin_free == pytest.approx(info.equity)

    def test_margin_is_not_faked_to_zero_once_a_position_exists(
        self, paper: PaperMT5Adapter
    ) -> None:
        """The old bug: margin stayed 0.0 and free margin stayed == equity,
        handing Risk an unlimited budget no real broker would ever report."""
        paper._connected = True
        filled = paper.execute_market_order(
            symbol="XAUUSD",
            order_type=OrderType.BUY,
            volume=1.0,
            price=4400.00,
            stop_loss=4390.00,
            take_profit=4420.00,
        )
        assert filled != 0, "paper order must fill for the margin test"

        info = paper.get_account_info()
        snap = paper.get_account_snapshot()

        # canonical law: 1.0 lot * 100 contract * 4400 / 100 leverage
        expected = 1.0 * 100.0 * 4400.00 / 100.0
        assert info.margin == pytest.approx(expected, abs=0.5)
        assert snap.margin == pytest.approx(expected, abs=0.5)
        assert info.margin_free == pytest.approx(info.equity - info.margin, abs=0.5)
        assert snap.margin_free == pytest.approx(snap.equity - snap.margin, abs=0.5)

    def test_free_margin_is_never_raw_equity_with_an_open_position(
        self, paper: PaperMT5Adapter
    ) -> None:
        paper._connected = True
        assert (
            paper.execute_market_order("XAUUSD", OrderType.BUY, 0.5, 4400.00, 4390.0, 4420.0) != 0
        )
        info = paper.get_account_info()
        assert info.margin > 0.0
        assert info.margin_free < info.equity

    def test_margin_level_follows_mql5_definition(self, paper: PaperMT5Adapter) -> None:
        paper._connected = True
        assert (
            paper.execute_market_order("XAUUSD", OrderType.BUY, 1.0, 4400.00, 4390.0, 4420.0) != 0
        )
        snap = paper.get_account_snapshot()
        if snap.margin and snap.margin > 0:
            assert snap.margin_level == pytest.approx(snap.equity / snap.margin * 100.0)
        else:
            assert snap.margin_level is None


class TestPaperUsesCanonicalLaw:
    def test_margin_required_matches_risk_engine_law(self, paper: PaperMT5Adapter) -> None:
        """Paper must exercise the SAME formula Risk uses — no private copy."""
        from nexus_scalp.domain.valuation import required_margin_estimate

        got = paper._margin_required("XAUUSD", 4400.00, 2.0)
        assert got == pytest.approx(
            required_margin_estimate(2.0, 100.0, 4400.00, paper.PAPER_LEVERAGE)
        )

    def test_calc_margin_snapshot_uses_contract_size_not_hardcoded_100(
        self, fx_paper: PaperMT5Adapter
    ) -> None:
        """FX contract is 100000, not the metal 100 the old code assumed."""
        snap = fx_paper.order_calc_margin_snapshot("EURUSD", 0, 1.0, 1.08500)
        assert snap.value == pytest.approx(1.0 * 100000.0 * 1.08500 / 100.0)

    def test_calc_profit_snapshot_uses_real_contract_size(self, fx_paper: PaperMT5Adapter) -> None:
        """Old code did delta * volume * 100 for EVERY symbol — 1000x wrong
        on a 100000-contract FX pair."""
        snap = fx_paper.order_calc_profit_snapshot("EURUSD", 0, 1.0, 1.08500, 1.08600)
        assert snap.value == pytest.approx(1.0 * 100000.0 * 0.00100)

    def test_metal_profit_snapshot_matches_order_calc_profit_shape(
        self, paper: PaperMT5Adapter
    ) -> None:
        snap = paper.order_calc_profit_snapshot("XAUUSD", 0, 1.0, 4400.00, 4401.00)
        assert snap.value == pytest.approx(1.0 * 100.0 * 1.00)

    def test_realized_pnl_is_the_canonical_law(self, paper: PaperMT5Adapter) -> None:
        from nexus_scalp.domain.valuation import price_delta_value

        paper._connected = True
        assert (
            paper.execute_market_order("XAUUSD", OrderType.BUY, 1.0, 4400.00, 4390.0, 4420.0) != 0
        )
        pos = paper._positions[0]
        # Pin to the broker-reported open price: paper fills at ask + slippage,
        # so price_open != the requested 4400.00 (fill realism, not a bug).
        exit_price = float(pos.price_open) + 5.00
        got = paper._realized_pnl(pos, exit_price, 1.0)
        assert got == pytest.approx(price_delta_value(1.0, 100.0, 5.00))
        # and it is exactly the same law the canonical module states
        assert got == pytest.approx(
            price_delta_value(float(pos.volume), 100.0, exit_price - float(pos.price_open))
        )

    def test_sell_realized_pnl_is_sign_symmetric(self, paper: PaperMT5Adapter) -> None:
        from nexus_scalp.domain.valuation import price_delta_value

        paper._connected = True
        assert (
            paper.execute_market_order("XAUUSD", OrderType.SELL, 1.0, 4400.00, 4410.0, 4380.0) != 0
        )
        pos = paper._positions[0]
        # SELL gains when price falls below its open price. Direction-adjusted
        # delta = -(exit - open) = +5, so the canonical law yields +500.
        exit_price = float(pos.price_open) - 5.00
        got = paper._realized_pnl(pos, exit_price, 1.0)
        assert got == pytest.approx(
            price_delta_value(1.0, 100.0, -(exit_price - float(pos.price_open)))
        )
        assert got == pytest.approx(500.0)
        assert got > 0.0  # a falling market profits a short


class TestPaperProvenanceIsNeverBrokerTruth:
    def test_snapshot_source_is_paper_simulation(self, paper: PaperMT5Adapter) -> None:
        snap = paper.get_account_snapshot()
        assert snap.source == "PAPER_SIMULATION"
        assert snap.company == "Nexus Paper Simulator"
        assert snap.server == "PAPER"

    def test_calc_snapshots_are_labeled_fallback_estimate(self, paper: PaperMT5Adapter) -> None:
        m = paper.order_calc_margin_snapshot("XAUUSD", 0, 1.0, 4400.0)
        p = paper.order_calc_profit_snapshot("XAUUSD", 0, 1.0, 4400.0, 4401.0)
        assert m.source == "FALLBACK_ESTIMATE"
        assert m.value_source == "FALLBACK_ESTIMATE"
        assert p.source == "FALLBACK_ESTIMATE"
        assert p.value_source == "FALLBACK_ESTIMATE"

    def test_margin_level_source_is_paper_simulation(self, paper: PaperMT5Adapter) -> None:
        snap = paper.get_account_snapshot()
        assert snap.margin_level_source == "PAPER_SIMULATION"

    def test_account_source_tag_is_paper(self, paper: PaperMT5Adapter) -> None:
        assert paper.current_account_source == "PAPER"

    def test_paper_leverage_is_a_documented_simulator_invariant(
        self, paper: PaperMT5Adapter
    ) -> None:
        """1:100 is a property of the SIMULATOR, not a claim about brokers."""
        assert paper.PAPER_LEVERAGE == 100
        assert paper.get_account_info().leverage == 100


class TestPaperRespectsBrokerVolumeContract:
    def test_volume_below_min_is_refused(self, paper: PaperMT5Adapter) -> None:
        paper._connected = True
        ticket = paper.execute_market_order("XAUUSD", OrderType.BUY, 0.001, 4400.0, 4390.0, 4420.0)
        assert ticket == 0

    def test_volume_step_misaligned_is_refused(self, paper: PaperMT5Adapter) -> None:
        paper._connected = True
        ticket = paper.execute_market_order("XAUUSD", OrderType.BUY, 0.015, 4400.0, 4390.0, 4420.0)
        assert ticket == 0

    def test_symbol_info_reports_metal_contract_and_digits(self, paper: PaperMT5Adapter) -> None:
        si = paper.get_symbol_info("XAUUSD")
        assert si.trade_contract_size == pytest.approx(100.0)
        assert si.digits == 2
        assert si.volume_min == pytest.approx(0.01)
        assert si.volume_step == pytest.approx(0.01)

    def test_symbol_info_reports_fx_contract_and_digits(self, fx_paper: PaperMT5Adapter) -> None:
        si = fx_paper.get_symbol_info("EURUSD")
        assert si.trade_contract_size == pytest.approx(100000.0)
        assert si.digits == 5


class TestPaperEquityInvariantSurvives:
    def test_equity_equals_balance_plus_floating(self, paper: PaperMT5Adapter) -> None:
        paper._connected = True
        assert (
            paper.execute_market_order("XAUUSD", OrderType.BUY, 1.0, 4400.00, 4390.0, 4420.0) != 0
        )
        info = paper.reconcile_accounting()
        assert info["ok"] is True
