"""History reconstruction tests (RED phase — target modules not yet built).

Builds the canonical trade-lifecycle reconstruction contract from REAL MT5
fixtures: deal+order streams -> normalized broker orders/deals -> logical
trades (position_id lifecycle), with exact deduplication identity.

Expected values are the REAL aggregates computed from the live capture:
    positions=44, net_total=+741.05, wins=37, losses=5, breakeven=2,
    best=+178.11, worst=-11.60, one 3-deal partial-close position.
"""

from __future__ import annotations

from tests.helpers.mt5_fixtures import EXPECTED, fixture_objects


class TestReconstructTradesFromRealFixture:
    def test_reconstructs_logical_trades_count(self) -> None:
        from nexus_scalp.adapters.database.broker_history import reconstruct_trades

        deals = fixture_objects("history_deals")
        orders = fixture_objects("history_orders")
        trades = reconstruct_trades(orders=orders, deals=deals, symbol="XAUUSD")
        assert len(trades) == EXPECTED["positions_count"]

    def test_logical_trade_net_pnl_matches_real_broker_sum(self) -> None:
        from nexus_scalp.adapters.database.broker_history import reconstruct_trades

        deals = fixture_objects("history_deals")
        trades = reconstruct_trades(orders=[], deals=deals, symbol="XAUUSD")
        total = sum(t.net_pnl for t in trades)
        assert round(total, 2) == EXPECTED["trades_net_total"]

    def test_wins_losses_breakeven_counts(self) -> None:
        from nexus_scalp.adapters.database.broker_history import reconstruct_trades

        deals = fixture_objects("history_deals")
        trades = reconstruct_trades(orders=[], deals=deals, symbol="XAUUSD")
        wins = sum(1 for t in trades if t.net_pnl > 0.01)
        losses = sum(1 for t in trades if t.net_pnl < -0.01)
        still_open = sum(1 for t in trades if t.exit_time is None)
        assert wins == EXPECTED["wins"]
        assert losses == EXPECTED["losses"]
        assert still_open == EXPECTED["open_positions_in_window"]

    def test_best_and_worst_trade(self) -> None:
        from nexus_scalp.adapters.database.broker_history import reconstruct_trades

        deals = fixture_objects("history_deals")
        trades = reconstruct_trades(orders=[], deals=deals, symbol="XAUUSD")
        net_pnls = [t.net_pnl for t in trades]
        assert round(max(net_pnls), 2) == EXPECTED["best_trade"]
        assert round(min(net_pnls), 2) == EXPECTED["worst_trade"]

    def test_partial_close_aggregates_to_one_trade(self) -> None:
        """position 152487940044 has 3 close deals + 1 open deal -> ONE trade."""
        from nexus_scalp.adapters.database.broker_history import reconstruct_trades

        deals = fixture_objects("history_deals")
        trades = reconstruct_trades(orders=[], deals=deals, symbol="XAUUSD")
        partial = [t for t in trades if t.position_id == EXPECTED["partial_close_position"]]
        assert len(partial) == 1
        assert round(partial[0].gross_pnl, 2) == round(EXPECTED["partial_close_gross"], 2)
        assert partial[0].volume == 0.53  # full lifecycle volume, all deals summed
        assert len(partial[0].deal_ids) == 4

    def test_order_deal_never_confused_with_position(self) -> None:
        from nexus_scalp.adapters.database.broker_history import reconstruct_trades

        deals = fixture_objects("history_deals")
        orders = fixture_objects("history_orders")
        trades = reconstruct_trades(orders=orders, deals=deals, symbol="XAUUSD")
        # One lifecycle per position, NOT one per order (136 orders -> 44 trades).
        assert len(trades) < len(orders)

    def test_trade_identity_is_position_id_not_uuid(self) -> None:
        from nexus_scalp.adapters.database.broker_history import reconstruct_trades

        deals = fixture_objects("history_deals")
        trades = reconstruct_trades(orders=[], deals=deals, symbol="XAUUSD")
        ids = {str(t.trade_id) for t in trades}
        assert len(ids) == len(trades)  # unique
        position_ids = {str(t.position_id) for t in trades}
        assert ids == position_ids  # deterministic broker identity, no random UUID


class TestNormalizeAndDeduplicate:
    def test_deal_identity_key_is_ticket(self) -> None:
        from nexus_scalp.adapters.database.broker_history import deal_identity

        deals = fixture_objects("history_deals")
        keys = {deal_identity(d) for d in deals}
        assert len(keys) == EXPECTED["deals_count"]  # all 88 deal tickets unique

    def test_order_identity_key_is_ticket(self) -> None:
        from nexus_scalp.adapters.database.broker_history import order_identity

        orders = fixture_objects("history_orders")
        keys = {order_identity(o) for o in orders}
        assert len(keys) == EXPECTED["orders_count"]
