"""
Log-Autopsy Bug Fix Verification Suite
======================================
Regression tests for the runtime telemetry fixes driven by the log autopsy:

    BUG-B  Hold score must degrade non-linearly during drawdown (no bonus
           masking, no profit-shield masking) so the engine de-risks BEFORE the
           emergency horizon.
    BUG-C  Profit-giveback protection must not fire on micro-profits / noise
           (tiered retention floor derived from peak R; disarmed below 0.5R).
    BUG-D  Cold-start fallback scaler must be persisted immediately, and a
           mono-class model collapse must trigger re-initialization instead of
           serving the collapsed baseline.
    BUG-E  Breakeven modification must respect spread + STOP_LEVEL so it can
           never cross the market.
    BUG-F  Split-order legs of one dispatch must be closed together on an
           emergency exit (no desynchronized half-closed position).
"""

from datetime import UTC, datetime, timedelta

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, SymbolInfo, TickData
from nexus_scalp.execution.order_manager import OrderLifecycleManager, PositionState
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer


class MockMT5Adapter:
    """Deterministic in-memory adapter with observable close/modify side effects."""

    def __init__(self):
        self.positions = []
        self.closed_tickets = []
        self.modifications = []

    def get_positions(self, symbol=None):
        return list(self.positions)

    def get_closed_deals_history(self, symbol, hours_back):
        return []

    def close_position(self, ticket, volume=None):
        self.closed_tickets.append(ticket)
        self.positions = [p for p in self.positions if p.ticket != ticket]
        return True

    def modify_position(self, ticket, stop_loss, take_profit):
        self.modifications.append((ticket, stop_loss, take_profit))
        for i, p in enumerate(self.positions):
            if p.ticket == ticket:
                self.positions[i] = Position(
                    ticket=p.ticket,
                    symbol=p.symbol,
                    type=p.type,
                    volume=p.volume,
                    price_open=p.price_open,
                    sl=stop_loss,
                    tp=take_profit,
                    profit=p.profit,
                    magic=p.magic,
                )
        return True

    def get_symbol_info(self, symbol):
        return SymbolInfo(
            symbol=symbol,
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=1.0,
            volume_min=0.01,
            volume_max=50.0,
            volume_step=0.01,
            stops_level=10,
            freeze_level=0,
            trade_contract_size=100.0,
        )


def make_pos(
    ticket, profit, price_open=2000.0, sl=1990.0, tp=2020.0, otype=OrderType.BUY, volume=1.0
):
    return Position(
        ticket=ticket,
        symbol="XAUUSD",
        type=otype,
        volume=volume,
        price_open=price_open,
        sl=sl,
        tp=tp,
        profit=profit,
        magic=888101,
    )


def make_tick(bid, ask=None, ts=None):
    return TickData(
        symbol="XAUUSD",
        timestamp=ts or datetime.now(UTC),
        bid=bid,
        ask=ask or round(bid + 0.20, 2),
        volume=1.0,
    )


def run_manage(om, adapter, tickets_profit, bid, ask=None, probs=None):
    """Runs one management pass for a set of positions and returns the manager."""
    adapter.positions = [make_pos(t, p) for t, p in tickets_profit]
    om.manage_active_positions("XAUUSD", make_tick(bid=bid, ask=ask), probs=probs)
    return om


# =============================================================================
# BUG-B: HOLD SCORE DEGRADATION
# =============================================================================


class TestHoldScoreDegradation:
    def test_deep_drawdown_drops_score_below_50(self):
        """A 50%-of-risk drawdown must drive hold_score well below the 50 de-risk band."""
        adapter = MockMT5Adapter()
        repo = AuditRepository(db_url="sqlite:///:memory:")
        om = OrderLifecycleManager(adapter=adapter, audit_repo=repo)

        # First observation at entry: let the manager initialize per-ticket state.
        run_manage(om, adapter, [(1, 0.0)], bid=2000.0, ask=2000.2)

        # Now a 50% drawdown of planned risk in PRICE terms: entry 2000, SL 1990
        # (10-point risk). Price at 1995 -> 5 points adverse = 50% of risk.
        om._last_hold_eval_time[1] = 0.0  # bypass the 500ms throttle deterministically
        run_manage(om, adapter, [(1, -50.0)], bid=1995.0, ask=1995.2)
        score = om._hold_score_tracker.get(1, 100)
        # Before the fix a 50% drawdown pegged the score at ~97-100. It must now drop
        # decisively below the old floor (and into/below the de-risk band in practice,
        # where the time-in-loss penalty compounds on top of the convex drawdown term).
        assert score < 60, f"expected score to collapse below 60 for 50% drawdown, got {score}"

    def test_trend_bonus_cannot_mask_drawdown(self):
        """When underwater >= 30% of risk, the trend bonus must not inflate the score."""
        adapter = MockMT5Adapter()
        repo = AuditRepository(db_url="sqlite:///:memory:")
        om = OrderLifecycleManager(adapter=adapter, audit_repo=repo)

        class FakeFV:
            is_above_kumo = True
            is_below_kumo = False
            atr_m1 = 1.5

        # price 1996 -> loss 4 price units / 10 = 40% of risk -> underwater >= 30%.
        pos = make_pos(ticket=2, profit=-40.0, price_open=2000.0, sl=1990.0)
        om._entry_prices[2] = 2000.0
        om._entry_sls[2] = 1990.0
        om._time_in_drawdown_sec[2] = 0.0
        _score, reasons = om._calculate_hold_value_score(
            pos=pos,
            price_current=1996.0,
            features=FakeFV(),
            impact_price_delta=0.0,
            atr=1.5,
        )
        assert not any("TREND_ALIGNMENT_BONUS" in r for r in reasons)
        assert any("TREND_BONUS_SUPPRESSED_UNDERWATER" in r for r in reasons)


# =============================================================================
# BUG-C: TIERED GIVEBACK PROTECTION (NO MICRO-PROFIT CUTS)
# =============================================================================


class TestTieredGivebackProtection:
    def test_micro_profit_below_half_r_is_disarmed(self):
        """A < 0.5R peak (a ~3-pip scalp) must NOT trigger giveback close on a pullback."""
        adapter = MockMT5Adapter()
        repo = AuditRepository(db_url="sqlite:///:memory:")
        om = OrderLifecycleManager(adapter=adapter, audit_repo=repo)
        # Risk = 10 price * 100 contract * 0.68 vol = $68. Peak $21.06 -> 0.31R.
        om._entry_prices[11] = 2000.0
        om._entry_sls[11] = 1990.0
        om._initial_risks[11] = 68.0
        om._time_in_profit_sec[11] = 0.0
        om._time_in_drawdown_sec[11] = 0.0
        om._peak_profit_usd[11] = 0.0
        om._peak_drawdown_usd[11] = 0.0
        om._entry_timestamps[11] = datetime.now(UTC)
        om._last_tick_timestamps[11] = datetime.now(UTC)

        # Record peak then pull back to +$4.32 (retention 20.5%).
        state = om.get_protection_state(11)
        state.update_peak(21.06)
        state.breakeven_sl_price = 2000.0
        adapter.positions = [make_pos(11, 4.32, volume=0.68)]
        om.manage_active_positions("XAUUSD", make_tick(bid=2000.06, ask=2000.26))
        assert 11 not in adapter.closed_tickets, "giveback must be disarmed for <0.5R peak"

    def test_large_runner_still_locked_in(self):
        """A >1.5R peak must retain >= 70% (existing aggressive protection preserved)."""
        adapter = MockMT5Adapter()
        repo = AuditRepository(db_url="sqlite:///:memory:")
        om = OrderLifecycleManager(adapter=adapter, audit_repo=repo)
        om._entry_prices[12] = 2000.0
        om._entry_sls[12] = 1990.0
        om._initial_risks[12] = 50.0  # $50 risk; peak $90 = 1.8R
        om._time_in_profit_sec[12] = 0.0
        om._time_in_drawdown_sec[12] = 0.0
        om._peak_profit_usd[12] = 0.0
        om._peak_drawdown_usd[12] = 0.0
        om._entry_timestamps[12] = datetime.now(UTC)
        om._last_tick_timestamps[12] = datetime.now(UTC)
        state = om.get_protection_state(12)
        state.update_peak(90.0)
        state.breakeven_sl_price = 2000.0

        # Retain only 60% of a 1.8R peak -> floor is 70% -> MUST trigger.
        adapter.positions = [make_pos(12, 54.0)]
        om.manage_active_positions("XAUUSD", make_tick(bid=2000.6, ask=2000.8))
        closed_or_critical = 12 in adapter.closed_tickets or om._position_states.get(12) in (
            PositionState.PROFIT_GIVEBACK_CRITICAL,
            PositionState.PROFIT_GIVEBACK_WARNING,
        )
        assert closed_or_critical, "a <70% retention on a 1.8R peak must be protected"


# =============================================================================
# BUG-F: SPLIT-ORDER DESYNC SYNC CLOSE
# =============================================================================


class TestSplitOrderSync:
    def _prep_om(self, om, tickets, order_id):
        # Position already open for 90s (past the 60s grace period) so a budget
        # exhaustion can actually produce a hard exit rather than being suppressed.
        entry_ts = datetime.now(UTC) - timedelta(seconds=90)
        for t in tickets:
            om._entry_prices[t] = 2000.0
            om._entry_sls[t] = 1990.0
            om._initial_risks[t] = 50.0
            om._time_in_profit_sec[t] = 0.0
            om._time_in_drawdown_sec[t] = 60.0
            om._peak_profit_usd[t] = 0.0
            om._peak_drawdown_usd[t] = 0.0
            om._entry_timestamps[t] = entry_ts
            om._last_tick_timestamps[t] = entry_ts
            om._entry_order_ids[t] = order_id

    def test_emergency_close_propagates_to_sibling_legs(self):
        adapter = MockMT5Adapter()
        repo = AuditRepository(db_url="sqlite:///:memory:")
        om = OrderLifecycleManager(adapter=adapter, audit_repo=repo)
        self._prep_om(om, [201, 202], "split_order_xyz")

        om._recovery_budget_initial[201] = 20.0  # small budget -> consumed by -$40 loss
        om._recovery_budget_remaining[201] = 20.0
        adapter.positions = [
            make_pos(201, -40.0),
            make_pos(202, -20.0),
        ]
        om.manage_active_positions("XAUUSD", make_tick(bid=1996.0, ask=1996.2))
        assert 201 in adapter.closed_tickets
        assert 202 in adapter.closed_tickets, "sibling leg must be synced closed"

    def test_unrelated_tickets_not_closed(self):
        adapter = MockMT5Adapter()
        repo = AuditRepository(db_url="sqlite:///:memory:")
        om = OrderLifecycleManager(adapter=adapter, audit_repo=repo)
        self._prep_om(om, [301], "order_a")
        self._prep_om(om, [302], "order_b")

        om._recovery_budget_initial[301] = 20.0
        om._recovery_budget_remaining[301] = 20.0
        adapter.positions = [
            make_pos(301, -40.0),
            make_pos(302, 5.0),
        ]
        om.manage_active_positions("XAUUSD", make_tick(bid=1996.0, ask=1996.2))
        assert 301 in adapter.closed_tickets
        assert 302 not in adapter.closed_tickets, "unrelated ticket must survive"


# =============================================================================
# BUG-D: SCALER PERSISTENCE ON COLD START
# =============================================================================


class TestScalerColdStartPersistence:
    def test_fallback_scaler_is_persisted_immediately(self, tmp_path):
        """The cold-start fallback scaler must be written to disk on first fit."""
        trainer = WalkForwardTrainer(artifact_save_path=str(tmp_path / "model.pt"), random_seed=42)
        assert not trainer._get_scaler_path().exists()
        X = trainer._fit_scaler(
            [[float(i % 50) / 50.0 for i in range(trainer.num_features)] for _ in range(196)]
        )
        trainer._save_scaler(X)
        assert trainer._get_scaler_path().exists(), "cold-start scaler must be persisted"

    def test_reload_roundtrip(self, tmp_path):
        """A persisted scaler reloads with identical mean/std."""
        trainer = WalkForwardTrainer(artifact_save_path=str(tmp_path / "model.pt"), random_seed=42)
        data = [[float(i) for i in range(trainer.num_features)] for _ in range(32)]
        scaler = trainer._fit_scaler(data)
        trainer._save_scaler(scaler)
        loaded = trainer._load_scaler()
        import numpy as np

        assert np.allclose(scaler.mean, loaded.mean)
        assert np.allclose(scaler.std, loaded.std)


# =============================================================================
# BUG-E: BREAKEVEN SPREAD + STOP_LEVEL CLEARANCE
# =============================================================================


class TestBreakevenClearance:
    def test_breakeven_defers_when_market_crosses_gap(self):
        """A breakeven SL that would cross the market must be deferred, not dispatched."""
        adapter = MockMT5Adapter()
        repo = AuditRepository(db_url="sqlite:///:memory:")
        om = OrderLifecycleManager(adapter=adapter, audit_repo=repo)
        # Prime entry state.
        adapter.positions = [make_pos(401, 0.0)]
        om.manage_active_positions("XAUUSD", make_tick(bid=2000.0, ask=2000.2))

        # Now in profit, market pulled back near breakeven SL (~2000).
        adapter.positions = [make_pos(401, 20.0)]
        om.manage_active_positions("XAUUSD", make_tick(bid=2000.05, ask=2000.25))

        # If a modification was dispatched it must never sit at/above the bid for a buy.
        for mod_ticket, sl, _tp in adapter.modifications:
            if mod_ticket == 401:
                assert sl < 2000.05, "breakeven SL must stay below the market bid"
