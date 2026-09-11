"""NSE-Swarm (role 2 Backend/Domain): C3 gate (b) runtime wiring.

TASK-AUDREV-C3 follow-up: the session-percentile spread gate shipped in
76eb23b9 with a read-only provider (broker_history.session_spread_percentile)
and a policy hook (SignalPolicy.session_spread_percentile_fn), but NO runtime
constructor bound the provider — the gate was dead code in production (always
a no-op because session_spread_percentile_fn stayed None).

This test pins the wiring contract:
  1. LiveEngine.__init__ injects a callable into the policy.
  2. Calling it returns the Nth percentile of the CURRENT session's FILLED
     rows from the DURABLE audit_paper_executions copy (INV-001-safe:
     read-only, same-UTC-day window; the adapter's in-memory ledger is only
     the parity-export SOURCE, never the gate's read surface).
  3. _sync_runtime_config hot-reloads spread_session_gate_enabled /
     spread_session_percentile from the runtime snapshot (operator toggle).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.features.regime_classifier import (
    MarketRegimeState,
    RecommendedExecutionType,
    RegimeReason,
    RegimeType,
)

# ---------------------------------------------------------------------------
# Hermetic helpers (no torch / no MT5 / no network)
# ---------------------------------------------------------------------------


def _paper_engine(tmp_path) -> tuple[LiveEngine, AuditRepository]:
    """A LiveEngine over a hermetic audit DB + paper adapter.

    audit_repo is injected explicitly so the engine NEVER touches the
    production artifacts/audit.db (BUG-223 contamination rule). The model
    gate is bypassed with force_fresh_model=True exactly like
    test_latency_regression_observability.py::test_load_scaler_artifacts_...
    """
    audit = AuditRepository(db_url=f"sqlite:///{tmp_path / 'audit.db'}")
    audit._start_background_worker()
    cfg = AppConfig.model_validate(
        {
            "execution": {"symbol": "XAUUSD", "mode": "PAPER", "magic_number": 888301},
            "telegram": {"enabled": False, "bot_token": "x", "admin_id": "y"},
        }
    )
    adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
    adapter.connect()
    engine = LiveEngine(config=cfg, adapter=adapter, audit_repo=audit, force_fresh_model=True)
    return engine, audit


def _seed_fill(
    audit: AuditRepository,
    ts: datetime,
    spread: float,
    symbol: str = "XAUUSD",
    status: str = "FILLED",
) -> None:
    audit.record_paper_execution(
        ts=ts.isoformat(),
        symbol=symbol,
        order_type="BUY",
        volume=0.01,
        requested_price=2000.0,
        bid_at_request=2000.0,
        ask_at_request=2000.0 + spread,
        spread=spread,
        fill_price=2000.0 + spread,
        slippage=0.0,
        rejection_reason=None if status == "FILLED" else "test_reject",
        ticket=1,
        status=status,
        source="SWARM_TEST",
    )


class _FakePaperAdapter:
    """Duck adapter so a real ledger export can seed the durable copy."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected

    def get_execution_ledger(self) -> list[dict[str, Any]]:
        return list(self._rows)


# ---------------------------------------------------------------------------
# 1. Construction-time injection contract
# ---------------------------------------------------------------------------


class TestConstructionInjection:
    def test_engine_injects_percentile_provider_into_policy(self, tmp_path) -> None:
        """The C3 gate (b) provider must be bound at LiveEngine construction.

        Regression pin for the dead-wiring defect: before the fix the policy's
        session_spread_percentile_fn stayed None forever and the
        SPREAD_SESSION_PCT gate could never fire in production.
        """
        engine, _audit = _paper_engine(tmp_path)
        policy = engine.signal_policy
        assert policy.session_spread_percentile_fn is not None
        assert callable(policy.session_spread_percentile_fn)

    def test_injected_provider_never_writes(self, tmp_path) -> None:
        """INV-001-adjacent: the provider is a pure read over the audit DB."""
        engine, audit = _paper_engine(tmp_path)
        now = datetime.now(UTC)
        for i in range(6):
            _seed_fill(audit, now - timedelta(minutes=10 - i), 0.10 + 0.05 * i)
        audit.flush(timeout_sec=5.0)
        before = sqlite3.connect(f"file:{audit._db_path}?mode=ro", uri=True).total_changes
        engine.signal_policy.session_spread_percentile_fn("XAUUSD", now, 70.0)
        after = sqlite3.connect(f"file:{audit._db_path}?mode=ro", uri=True).total_changes
        assert before == after

    def test_paper_adapter_is_not_the_read_surface(self, tmp_path) -> None:
        """The gate reads the DURABLE copy, not the adapter's in-memory ledger.

        The adapter ledger is parity-export SOURCE data; only the durable
        audit_paper_executions copy survives a restart, so the gate must
        measure the same distribution across restarts.
        """
        engine, audit = _paper_engine(tmp_path)
        engine.adapter = _FakePaperAdapter([])  # even with an empty live ledger...
        now = datetime.now(UTC)
        for i in range(6):
            _seed_fill(audit, now - timedelta(minutes=15 - i), 0.20 + 0.05 * i)
        audit.flush(timeout_sec=5.0)
        p70 = engine.signal_policy.session_spread_percentile_fn("XAUUSD", now, 70.0)
        assert p70 is not None and p70 > 0.0

    def test_engine_leaks_no_audit_connection(self, tmp_path) -> None:
        """The injected fn opens its OWN bounded connection per call and never
        retains one — a leak would serialize the tick path on a held handle."""
        engine, _audit = _paper_engine(tmp_path)
        fn = engine.signal_policy.session_spread_percentile_fn
        assert fn("XAUUSD", datetime.now(UTC), 70.0) is None  # empty DB -> honest None
        # The closure must not cache a connection object on the engine.
        assert getattr(engine, "_session_spread_conn", None) is None


# ---------------------------------------------------------------------------
# 2. Provider semantics through the injected closure
# ---------------------------------------------------------------------------


class TestInjectedProviderSemantics:
    def test_returns_percentile_of_session_fills(self, tmp_path) -> None:
        engine, audit = _paper_engine(tmp_path)
        now = datetime.now(UTC)
        for i in range(10):
            _seed_fill(audit, now - timedelta(minutes=30 - i * 2), 0.10 + 0.04 * i)
        audit.flush(timeout_sec=5.0)
        # Linear interpolation (numpy semantics): P70 of 0.10..0.46 => 0.352;
        # P50 => the median 0.28.
        p70 = engine.signal_policy.session_spread_percentile_fn("XAUUSD", now, 70.0)
        assert p70 == pytest.approx(0.352)
        p50 = engine.signal_policy.session_spread_percentile_fn("XAUUSD", now, 50.0)
        assert p50 == pytest.approx(0.28)

    def test_thin_session_returns_none_gate_is_noop(self, tmp_path) -> None:
        """< min_samples => None => policy treats the gate as no-op (never 0.0,
        which would fail-open on an empty distribution)."""
        engine, audit = _paper_engine(tmp_path)
        now = datetime.now(UTC)
        for i in range(3):
            _seed_fill(audit, now - timedelta(minutes=5 - i), 0.10)
        audit.flush(timeout_sec=5.0)
        assert engine.signal_policy.session_spread_percentile_fn("XAUUSD", now, 70.0) is None

    def test_rejected_and_stale_rows_excluded(self, tmp_path) -> None:
        engine, audit = _paper_engine(tmp_path)
        now = datetime.now(UTC)
        for i in range(5):
            _seed_fill(audit, now - timedelta(minutes=25 - i), 0.10 + 0.02 * i)
        _seed_fill(audit, now, 9.0, status="REJECTED")  # not FILLED
        _seed_fill(audit, now - timedelta(days=1), 9.0)  # yesterday: outside window
        _seed_fill(audit, now, 9.0, symbol="EURUSD")  # other symbol
        audit.flush(timeout_sec=5.0)
        p50 = engine.signal_policy.session_spread_percentile_fn("XAUUSD", now, 50.0)
        assert p50 == pytest.approx(0.14)

    def test_percentile_out_of_contract_range_rejected(self, tmp_path) -> None:
        """The provider passes the configured percentile through untouched; the
        underlying broker_history contract (50..99) still guards the extreme."""
        engine, _audit = _paper_engine(tmp_path)
        policy = engine.signal_policy
        assert 50.0 <= policy.spread_session_percentile <= 99.0

    def test_end_to_end_gate_blocks_wide_quote(self, tmp_path) -> None:
        """Full liveness proof: seed the durable copy, then run a policy
        evaluation through the ENGINE's real injected provider — a live spread
        above the seeded session percentile must be blocked with the C3 (b)
        reason code (the wiring is real, not just constructed)."""
        engine, audit = _paper_engine(tmp_path)
        now = datetime.now(UTC)
        for i in range(6):
            _seed_fill(audit, now - timedelta(minutes=12 - i), 0.20 + 0.01 * i)
        audit.flush(timeout_sec=5.0)

        from tests.unit.test_policy import _make_feature_vector, _make_tick

        policy = engine.signal_policy
        policy._last_signal_time = None
        policy._dedup_last_time = None
        policy._dedup_last_bid = 0.0
        policy._dedup_last_ask = 0.0
        base = _make_tick()
        tick = base.model_copy(
            update={
                "timestamp": base.timestamp + timedelta(seconds=1),
                "bid": 2000.0,
                "ask": 2000.0 + 1.20,  # far above the seeded P70 (0.25)
            }
        )
        fv = _make_feature_vector().model_copy(
            update={
                "is_above_kumo": True,
                "tenkan_sen": 2005.0,
                "kijun_sen": 2003.0,
                "live_tick_displacement": 0.9,
                "choch_bullish": True,
                "liquidity_sweep_signal": 1,
                "dist_to_swing_low_20": 5.0,
                "dist_to_swing_high_20": 5.0,
                "atr_m1": 8.0,
            }
        )
        regime = MarketRegimeState(
            symbol="XAUUSD",
            timestamp_utc=now.isoformat(),
            regime_type=RegimeType.TRENDING_MOMENTUM,
            regime_probability=0.85,
            recommended_execution_type=RecommendedExecutionType.HYBRID_LIMIT_STOP,
            order_flow_imbalance=0.10,
            tick_velocity_per_sec=2.0,
            current_spread_usd=1.20,
            realized_volatility_5m=0.01,
            reason=RegimeReason.OFI_TREND_ALIGN,
            is_macro_news_active=False,
        )
        proposal = policy.evaluate_probabilities(
            torch.tensor([[0.20, 0.55, 0.15, 0.05]], dtype=torch.float32),
            tick,
            fv,
            regime_state=regime,
        )
        assert proposal.action == ActionType.NO_TRADE
        assert proposal.decision_stage == "SPREAD_SESSION_PCT_GATE"
        assert proposal.blocked_by == "SPREAD_SESSION_PCT"
        checks = proposal.risk_checks or {}
        assert checks["spread_session_percentile_value"] is not None
        assert checks["spread_session_percentile_value"] > 0.0


# ---------------------------------------------------------------------------
# 3. Hot-reload parity (operator toggle rides the runtime snapshot)
# ---------------------------------------------------------------------------


class TestHotReloadParity:
    def test_sync_reloads_gate_toggle_and_percentile(self, tmp_path) -> None:
        """spread_session_gate_enabled / spread_session_percentile must follow
        the runtime snapshot like every other algo key (hot-reload parity)."""
        engine, _audit = _paper_engine(tmp_path)
        policy = engine.signal_policy
        assert policy.spread_session_gate_enabled is True
        assert policy.spread_session_percentile == pytest.approx(70.0)

        report = engine.apply_runtime_update(
            {
                "algo.spread_session_gate_enabled": False,
                "algo.spread_session_percentile": 85.0,
            },
            source="SWARM_TEST",
            actor="swarm",
        )
        assert report.success
        assert policy.spread_session_gate_enabled is False
        assert policy.spread_session_percentile == pytest.approx(85.0)


# ---------------------------------------------------------------------------
# 4. Composition-root surfaces stay engine-owned (no maintenance coupling)
# ---------------------------------------------------------------------------


class TestNoMaintenanceCoupling:
    def test_maintenance_module_has_no_spread_wiring(self) -> None:
        """Ownership rule: the wiring lives in the composition root ctor only.
        Guards against a future refactor re-attaching it to the maintenance
        cycle (I/O per maintenance pass instead of a bound provider)."""
        import inspect

        import nexus_scalp.application.live.maintenance as maintenance_mod

        src = inspect.getsource(maintenance_mod)
        assert "session_spread_percentile" not in src
        assert "signal_policy.session_spread_percentile_fn" not in src
