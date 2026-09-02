"""PHASE 14 FORENSIC HARDENING — canonical live-state contract tests.

Verifies the no-synthetic-values invariant end to end:

1. `/api/status` and `/api/live/state` NEVER render fake market data when the
   engine has no live state (previous revision served bid=2334.21,
   ask=2334.41, spread=20, atr=1.15, regime=NORMAL_VOLATILITY,
   probs=99.5/0.2/0.3% — see agents/bugs.md BUG-020 lineage).
2. Every section carries explicit provenance (LIVE_MT5 / ENGINE_STATE /
   MODEL_INFERENCE / ACCOUNTING_CORE / UNAVAILABLE).
3. Snapshot identity (state_version + per-section timestamps) is present so
   the UI can detect mixed-age renders.
4. Features are 50-dim, schema-driven, with explicit validity status.
5. `/api/simulation/tick` is a no-op in LIVE mode (synthetic prices can never
   enter the production pipeline).
6. `/api/live/accounting` computes a deterministic risk plan across account
   sizes $10 .. $1M+ with no NaN/Inf/negative values.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web.server import create_app

XAU = 2334.21  # only used as REAL tick fixture input, never as a default


class _FakeBar:
    def __init__(self, ts, o, h, l, c, v=10):
        self.timestamp = ts
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.tick_volume = v


class _FakeAggregator:
    def __init__(self):
        base = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
        self.bars = [
            _FakeBar(base.replace(minute=base.minute + i), 2330 + i, 2332 + i, 2328 + i, 2331 + i)
            for i in range(30)
        ]

    def get_completed_bars(self):
        return list(self.bars)

    def get_current_forming_bar(self):
        return _FakeBar(datetime(2026, 8, 17, 2, 31, tzinfo=UTC), 2331, 2332.5, 2330.8, 2332.1, 5)


class _FakeTick:
    def __init__(self):
        self.bid = 2334.21
        self.ask = 2334.41
        self.volume = 1.0
        self.timestamp = datetime(2026, 8, 17, 2, 31, 5, tzinfo=UTC)
        self.symbol = "XAUUSD"


class _FakeFV:
    def __init__(self):
        self.timestamp_utc = "2026-08-17T02:31:05+00:00"

    def to_tensor_input(self):
        return [0.0 if i % 2 == 0 else 0.1 for i in range(50)]


class _FakeProposal:
    def __init__(self):
        self.action = SimpleNamespace(value="NO_ACTION")
        self.confidence = 0.62
        self.reason_code = "WAITING"
        self.generated_at = datetime(2026, 8, 17, 2, 31, 5, tzinfo=UTC)
        self.risk_reward_ratio = 1.5
        self.proposed_entry = 2334.0
        self.stop_loss = 2332.0
        self.take_profit = 2337.0


class _FakeAdapter:
    def is_connected(self):
        return True

    def get_last_tick(self, symbol):
        return _FakeTick()

    def get_broker_tick(self, symbol):
        from datetime import UTC

        from nexus_scalp.adapters.mt5.providers import BrokerTickSnapshot

        snap = BrokerTickSnapshot()
        snap.available = True
        snap.source = "BROKER_NATIVE"
        snap.symbol = symbol
        snap.bid = 2334.21
        snap.ask = 2334.41
        snap.last = 2334.21
        snap.volume = 1.0
        snap.time = 1784500000
        snap.time_utc = datetime(2026, 8, 17, 2, 31, 5, tzinfo=UTC)
        snap.freshness_ms = 0.0
        snap.stale = False
        snap.spread_points = 0.20
        return snap

    def get_account_snapshot(self):
        from nexus_scalp.adapters.mt5.providers import AccountSnapshot

        snap = AccountSnapshot()
        snap.available = True
        snap.source = "BROKER_NATIVE"
        snap.login = 123456
        snap.server = "MetaQuotes-Demo"
        snap.currency = "USD"
        snap.leverage = 100
        snap.trade_mode = 0
        snap.trade_allowed = True
        snap.balance = 10000.0
        snap.equity = 10000.0
        snap.margin = 200.0
        snap.margin_free = 9800.0
        snap.margin_level = 5000.0
        snap.floating_pnl = 0.0
        snap.open_positions_count = 0
        snap.pending_orders_count = 0
        return snap

    def connection_state(self):
        from nexus_scalp.adapters.mt5.diagnostics import MT5ConnectionState

        state = MT5ConnectionState()
        state.set_state(MT5ConnectionState.CONNECTED, "test")
        return state

    def get_symbol_snapshot(self, symbol):
        from nexus_scalp.adapters.mt5.providers import SymbolSnapshot

        snap = SymbolSnapshot()
        snap.available = True
        snap.source = "BROKER_NATIVE"
        snap.spec = {
            "name": symbol,
            "digits": 2,
            "point": 0.01,
            "trade_contract_size": 100.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "trade_stops_level": 10,
            "trade_freeze_level": 0,
        }
        snap.tick = {"bid": 2334.21, "ask": 2334.41, "last": 2334.21}
        snap.spread_points = 0.20
        snap.spread_points_source = "BROKER_NATIVE"
        return snap

    def get_all_positions(self, symbol=None):
        return []

    def get_pending_orders_snapshot(self, symbol=None):
        return []

    def get_history_orders(self, from_utc=None, to_utc=None, symbol=None):
        return []

    def get_history_deals(self, from_utc=None, to_utc=None, symbol=None):
        return []

    def get_rate_history(self, symbol, timeframe="M1", count=500, from_utc=None):
        return []

    def get_tick_history(self, symbol, count=500, from_utc=None, to_utc=None):
        return []

    def order_calc_profit_snapshot(self, **kw):
        from nexus_scalp.adapters.mt5.providers import BrokerCalcSnapshot

        snap = BrokerCalcSnapshot()
        snap.available = True
        snap.source = "BROKER_NATIVE"
        snap.value = 1.0
        snap.value_source = "BROKER_NATIVE"
        return snap

    def order_calc_margin_snapshot(self, **kw):
        from nexus_scalp.adapters.mt5.providers import BrokerCalcSnapshot

        snap = BrokerCalcSnapshot()
        snap.available = True
        snap.source = "BROKER_NATIVE"
        snap.value = 20.0
        snap.value_source = "BROKER_NATIVE"
        return snap

    def get_terminal_state(self):
        return {"available": True, "connected": True}

    def get_account_info(self):
        return SimpleNamespace(
            balance=10000.0, equity=10000.0, margin=200.0, margin_free=9800.0, leverage=100
        )

    def get_positions(self, symbol=None):
        return []


class _FakeAudit:
    def get_recent_predictions(self, limit=40):
        return []


class _FakeEngine:
    """Engine with REAL live state (tick, features, bars, account)."""

    def __init__(self):
        import threading

        self._bundle_lock = threading.RLock()
        self._bundle = None
        self._running = True
        self._peak_equity = 10000.0
        self._runtime_mode = "LIVE"
        self._last_tick = _FakeTick()
        self._last_fv = _FakeFV()
        self._last_regime_state = SimpleNamespace(
            regime_type=SimpleNamespace(name="NORMAL_VOLATILITY"),
            realized_volatility_5m=1.15,
        )
        self._last_probs = None
        self._last_proposal = _FakeProposal()
        self._symbol_info = SimpleNamespace(
            trade_contract_size=100.0,
            volume_step=0.01,
            volume_min=0.01,
            volume_max=100.0,
        )
        self.config = SimpleNamespace(
            execution=SimpleNamespace(symbol="XAUUSD", mode=SimpleNamespace(value="LIVE")),
            algo=SimpleNamespace(
                atr_sl_buffer_multiplier=1.5,
                min_risk_reward_ratio=1.8,
                ai_zone_confidence_threshold=0.82,
                fvg_mitigation_sensitivity=0.5,
                order_block_lookback_bars=30,
            ),
            risk=SimpleNamespace(
                risk_per_trade_pct=0.5,
                max_account_drawdown_pct=20.0,
                max_concurrent_positions=2,
                max_spread_points=40,
            ),
            model=SimpleNamespace(confidence_threshold=0.75, feature_schema_version="v1.0"),
        )
        self.aggregator = _FakeAggregator()
        self.adapter = _FakeAdapter()
        self.audit = _FakeAudit()
        self.FEATURE_SCHEMA_ID = "scalp_v1"
        self.FEATURE_DIM = 50
        # Real LIVE probabilities (available case)
        self._last_probs = _ProbsTensor([0.995, 0.002, 0.003, 0.0])

    @property
    def risk_engine(self):
        from nexus_scalp.risk.risk_engine import RiskEngine

        if not hasattr(self, "_risk_engine"):
            self._risk_engine = RiskEngine(
                max_allowed_lots=10.0, min_risk_reward_ratio=1.5, config=self.config.risk
            )
        return self._risk_engine


class _ProbsTensor:
    def __init__(self, values):
        self._v = values

    def cpu(self):
        return self

    def numpy(self):
        return _NumpyArray(self._v)


class _NumpyArray:
    def __init__(self, values):
        self._v = list(values)

    def flatten(self):
        return self

    def tolist(self):
        return list(self._v)


class _EmptyEngine(_FakeEngine):
    """Engine with NO live state (offline / no tick yet)."""

    def __init__(self):
        super().__init__()
        self._running = False
        self._last_tick = None
        self._last_fv = None
        self._last_regime_state = None
        self._last_probs = None
        self._last_proposal = None
        self._symbol_info = None
        self.adapter = _EmptyAdapter()
        self.aggregator = _EmptyAggregator()


class _EmptyAdapter:
    def is_connected(self):
        return False

    def get_last_tick(self, symbol):
        return None

    def get_account_info(self):
        return None

    def get_positions(self, symbol=None):
        return []


class _EmptyAggregator:
    def get_completed_bars(self):
        return []

    def get_current_forming_bar(self):
        return None


# ---------------------------------------------------------------------------


@pytest.fixture
def live_client():
    return TestClient(create_app(engine_ref=_FakeEngine()))


@pytest.fixture
def empty_client():
    return TestClient(create_app(engine_ref=_EmptyEngine()))


@pytest.fixture
def no_engine_client():
    return TestClient(create_app(engine_ref=None))


def test_no_fake_defaults_when_engine_offline(no_engine_client):
    """Without an engine the state must be explicit UNAVAILABLE, never fake."""
    state = no_engine_client.get("/api/status").json()
    assert state["bid"] is None
    assert state["ask"] is None
    assert state["spread"] is None
    assert state["atr"] is None
    assert state["regime"] is None
    assert state["probs"]["available"] is False
    assert state["probs"]["no_trade"] is None
    assert state["account"]["available"] is False
    assert state["account"]["balance"] is None
    assert state["provenance"]["price"] == "UNAVAILABLE"
    assert state["provenance"]["model"] == "UNAVAILABLE"
    # The forbidden literals must never appear as live values.
    assert state["bid"] != 2334.21
    assert state["probs"].get("no_trade") != 0.995


def test_no_fake_defaults_when_engine_has_no_state_yet(empty_client):
    """Engine present but no tick/features/model yet -> UNAVAILABLE, not mocks."""
    state = empty_client.get("/api/status").json()
    assert state["bid"] is None
    assert state["atr"] is None
    assert state["regime"] is None
    assert state["probs"]["available"] is False
    # Features: 50 entries, all UNAVAILABLE status (never zero-masquerade).
    assert len(state["features"]) == 50
    assert state["features"][0]["status"] == "UNAVAILABLE"
    assert state["features"][0]["value"] is None
    # execution_mode reflects config even when offline.
    assert state["execution_mode"] == "LIVE"


def test_real_values_flow_when_live(no_engine_client, live_client):
    """Live engine state must reach the API verbatim (broker tick path)."""
    state = live_client.get("/api/status").json()
    assert state["bid"] == 2334.21
    assert state["ask"] == 2334.41
    assert state["spread"] == round((2334.41 - 2334.21) * 100, 2)
    assert state["atr"] == 1.15
    assert state["regime"] == "NORMAL_VOLATILITY"
    assert state["probs"]["available"] is True
    assert state["probs"]["no_trade"] == 0.995
    assert state["provenance"]["price"] == "LIVE_MT5"
    assert state["provenance"]["features"] == "ENGINE_STATE"
    assert state["provenance"]["model"] == "MODEL_INFERENCE"
    assert state["provenance"]["accounting"] == "BROKER_NATIVE"
    # Features are the real 50D vector with VALID status.
    assert len(state["features"]) == 50
    assert state["features"][1]["status"] == "VALID"
    assert state["features"][1]["value"] == 0.1
    # Snapshot identity present.
    assert isinstance(state["state_version"], int)
    assert state["snapshot_timestamp"]
    assert state["timestamps"]["tick"]
    assert state["timestamps"]["features"]
    # Account identity from the typed snapshot.
    assert state["account"]["login"] == 123456
    assert state["account"]["server"] == "MetaQuotes-Demo"
    assert state["runtime_mode"] == "LIVE"


def test_canonical_live_state_contract(live_client):
    """/api/live/state exposes the unified LiveUiState.2 contract."""
    body = live_client.get("/api/live/state").json()
    assert body["contract"] == "LiveUiState.2"
    for section in ("market", "chart", "features", "model", "strategy", "risk", "accounting"):
        assert section in body
    assert body["market"]["bid"] == 2334.21
    assert body["market"]["source"] == "LIVE_MT5"
    assert body["model"]["probabilities_available"] is True
    assert body["model"]["probabilities"]["no_trade"] == 0.995
    assert body["accounting"]["balance"] == 10000.0
    assert len(body["features"]["entries"]) == 50
    assert body["mt5"]["available"] is True
    assert body["mt5"]["connection"]["state"] == "CONNECTED"
    # Bars are real (30 completed + 1 forming), forming flagged.
    assert body["chart"]["bars_available"] is True
    forming = [b for b in body["chart"]["bars"] if not b["is_complete"]]
    assert len(forming) == 1


def test_simulation_tick_blocked_in_live_mode(live_client):
    """Synthetic price injection must be impossible in LIVE mode."""
    r = live_client.post("/api/simulation/tick", json={"type": "BUY_PRESSURE"})
    body = r.json()
    assert body["success"] is False
    assert "blocked" in body["message"].lower()


def test_predictions_never_fabricated(live_client):
    """Predictions come from audit_signals (empty here) - never fabricated."""
    state = live_client.get("/api/status").json()
    assert state["predictions"] == []


# ---------------------------------------------------------------------------
# Accounting single-source-of-truth across account sizes
# ---------------------------------------------------------------------------


def _accounting_plan(client, equity):
    r = client.get(
        "/api/live/accounting",
        params={"equity": equity, "entry": 2334.21, "stop_loss": 2328.0},
    )
    assert r.status_code == 200
    return r.json()


@pytest.mark.parametrize(
    "equity",
    [10.0, 25.0, 50.0, 100.0, 500.0, 1000.0, 10000.0, 100000.0, 1000000.0],
)
def test_accounting_plan_scales_across_account_sizes(live_client, equity):
    """No NaN/Inf/negative numbers for $10 .. $1M+ accounts."""
    body = _accounting_plan(live_client, equity)
    assert body["available"] is True
    plan = body["plan"]
    assert plan is not None
    for key, val in plan.items():
        if key in (
            "note",
            "entry",
            "stop_loss",
            "sl_distance",
            "lot_size",
            "lot_step",
            "min_lot",
            "max_lot",
            "margin_required",
            "exposure_pct",
        ):
            if val is not None and isinstance(val, (int, float)):
                import math

                assert math.isfinite(val), f"{key}={val} not finite at equity={equity}"
                assert val >= 0, f"{key}={val} negative at equity={equity}"
    assert plan["risk_usd"] == round(equity * 0.005, 2)
    assert plan["equity"] == equity
    # Lot size must respect the broker minimum or be explicitly rejected.
    assert plan["lot_size"] >= plan["min_lot"] or plan["note"] == "INSUFFICIENT_EQUITY_FOR_MIN_LOT"


def test_accounting_plan_rejects_nonfinite(live_client):
    """The endpoint must never emit NaN/Inf even on degenerate input."""
    import math

    r = live_client.get(
        "/api/live/accounting",
        params={"equity": float("nan"), "entry": 2334.21, "stop_loss": 2328.0},
    )
    body = r.json()
    # NaN equity must not crash; plan either absent or finite.
    plan = body.get("plan") or {}
    for val in plan.values():
        if isinstance(val, float):
            assert math.isfinite(val)
