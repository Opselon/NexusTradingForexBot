"""TEST-DEBUG-01..32 — Debug 70D forensic console acceptance suite.

Covers the canonical /api/debug/state snapshot contract:

    TEST-DEBUG-01  active schema displayed correctly
    TEST-DEBUG-02  70D dimension validated
    TEST-DEBUG-03  all 70 indices displayed
    TEST-DEBUG-04  feature names match registry
    TEST-DEBUG-05  feature values match backend
    TEST-DEBUG-06  feature status accurate
    TEST-DEBUG-07  Liquidity 60..69 displayed
    TEST-DEBUG-08  News state displayed
    TEST-DEBUG-09  model input displayed
    TEST-DEBUG-10  model output displayed
    TEST-DEBUG-11  confidence pipeline displayed
    TEST-DEBUG-12  policy gate trace
    TEST-DEBUG-13  risk trace
    TEST-DEBUG-14  exposure broker/internal comparison
    TEST-DEBUG-15  execution trace
    TEST-DEBUG-16  position state
    TEST-DEBUG-17  exit decision trace
    TEST-DEBUG-18  worker state
    TEST-DEBUG-19  database state
    TEST-DEBUG-20  cache state
    TEST-DEBUG-21  chart state
    TEST-DEBUG-22  SSE state
    TEST-DEBUG-23  correlation ID
    TEST-DEBUG-24  snapshot capture
    TEST-DEBUG-25  JSON snapshot serialization
    TEST-DEBUG-26  datetime serialization
    TEST-DEBUG-27  invalid model contract displayed
    TEST-DEBUG-28  70D mismatch displayed
    TEST-DEBUG-29  no secret leakage
    TEST-DEBUG-30  Debug does not block hot path
    TEST-DEBUG-31  snapshot comparison
    TEST-DEBUG-32  feature diff

Plus regression fixtures (brief 46/47):
    actual_dim=50 / actual_classes=128 -> MODEL CONTRACT INVALID shown
    SSE payload with datetime/Liquidity/Model/Feature vector serializes
    (or surfaces SSE_SERIALIZATION_ERROR with correlation_id).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web.debug_snapshot import (
    DebugSnapshotStore,
    build_debug_snapshot,
    diff_snapshots,
)
from nexus_scalp.web.server import canonical_json, create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeBundle:
    def __init__(
        self,
        *,
        num_features: int = 50,
        num_classes: int = 4,
        scaler_ready: bool = True,
    ) -> None:
        self.artifact_path = "/secret/host/machine/models/champion.pt"
        self.scaler = SimpleNamespace(is_ready=lambda: scaler_ready)
        self.model = SimpleNamespace(
            num_features=num_features,
            classifier_out=SimpleNamespace(out_features=num_classes),
            parameters=lambda: iter([SimpleNamespace(device=SimpleNamespace(type="cpu"))]),
        )
        self.input_dim = num_features


class _FakePosition:
    def __init__(self, ticket: int, ptype: str = "BUY", vol: float = 0.1) -> None:
        self.ticket = ticket
        self.symbol = "XAUUSD"
        self.type = SimpleNamespace(value=ptype)
        self.volume = vol
        self.price_open = 2400.0
        self.price_current = 2405.0
        self.sl = 2395.0
        self.tp = 2420.0
        self.profit = 12.5
        self.swap = 0.0
        self.commission = 0.0
        self.magic = 12345


class _FakeOrderManager:
    def __init__(self) -> None:
        self._live_tickets_cache: dict[int, dict] = {}
        self._mfe_tracker: dict[int, float] = {}
        self._mae_tracker: dict[int, float] = {}
        self._peak_profit_usd: dict[int, float] = {}
        self._entry_timestamps: dict[int, datetime] = {}
        self._last_reasons_tracker: dict[int, list[str]] = {}
        self._last_reconcile_attempt: float | None = None
        self.global_state = "NORMAL"
        self._consecutive_failures = 0
        self._processed_orders: dict[str, bool] = {}

    def count_total_exposure(self, symbol: str | None = None) -> tuple[int, int]:
        positions = sum(
            1 for info in self._live_tickets_cache.values() if info.get("type") != "PENDING"
        )
        pendings = sum(
            1 for info in self._live_tickets_cache.values() if info.get("type") == "PENDING"
        )
        return positions, pendings

    def get_active_live_tickets(self) -> list[dict]:
        return list(self._live_tickets_cache.values())

    def get_protection_state(self, ticket: int):
        return SimpleNamespace(
            breakeven_armed=True,
            trailing_armed=False,
            giveback_state="ARMED",
            strategy_exit_state="HOLD",
            exit_state="TRACKING",
        )


class _FakeGovernor:
    def __init__(self, enabled: bool = True, eqh_strength: float = 1.2) -> None:
        self._enabled = enabled
        self._eqh_strength = eqh_strength

    def status(self) -> str:
        return "ENABLED"

    def report(self) -> dict:
        feats = {
            "bsl_distance_atr": 0.5,
            "ssl_distance_atr": -0.3,
            "eqh_strength": self._eqh_strength,
            "eql_strength": 0.8,
            "htf_liquidity_score": 0.6,
            "internal_liquidity_distance": -0.2,
            "external_liquidity_distance": 0.4,
            "liquidity_confluence": 0.9,
            "liquidity_sweep_state": 1.0,
            "post_sweep_displacement": 0.3,
        }
        return {
            "enabled": True,
            "available": True,
            "status": "ENABLED",
            "causal_state": "VALID",
            "source": "LIVE_ENGINE",
            "algorithm_version": "v1",
            "last_update": datetime.now(UTC).isoformat(),
            "age_sec": 0.1,
            "latency_ms": 2.0,
            "schema": {"id": "scalp_liquidity_v1", "dimension": 10},
            "reserved_70d_schema": {
                "id": "scalp_v3",
                "dimension": 70,
                "family_indices": "0..49 BASE | 50..59 FAMILY | 60..69 LIQUIDITY",
            },
            "features": feats,
            "feature_count": 10,
            "feature_names": list(feats.keys()),
            "error": None,
            "error_at": None,
            "pools": [
                {
                    "side": "BSL",
                    "source": "H1",
                    "state": "CONFIRMED",
                    "price": 2410.0,
                    "confirmed_at": datetime.now(UTC).isoformat(),
                }
            ],
            "model_compatibility": {"result": "PASS", "reason": "SCHEMA_DIMENSION_MATCH"},
        }

    def snapshot_payload(self) -> dict:
        rep = self.report()
        feats = rep["features"]
        ts = rep["last_update"]
        return {
            "schema_id": "scalp_liquidity_v1",
            "dimension": 10,
            "timestamp": ts,
            "source": "LIVE_ENGINE",
            "features": {
                name: {
                    "index": 60 + i,
                    "value": float(value),
                    "timestamp": ts,
                    "source": "LIVE_ENGINE",
                    "status": "ENABLED",
                    "raw_value": float(value),
                    "normalized_value": float(value),
                    "clipped_value": float(value),
                }
                for i, (name, value) in enumerate(feats.items())
            },
            "available": True,
        }


class _FakeNewsContext:
    available = True
    timestamp = datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC)
    state = SimpleNamespace(value="NORMAL")
    active_event_count = 2
    xauusd_relevance = 0.7
    usd_relevance = 0.3
    bullish_score = 0.2
    bearish_score = 0.1
    confidence = 0.5
    conflict_score = 0.05
    freshness = 0.9
    source_consensus = 0.6
    stale = False
    active_high_impact: ClassVar[list[str]] = ["CPI", "FOMC"]


class _FakeEngine:
    """Minimal engine with enough surface for every snapshot section."""

    def __init__(
        self,
        *,
        dim: int = 50,
        classes: int = 4,
        has_fv: bool = True,
        has_bundle: bool = True,
    ) -> None:
        self._running = True
        self._runtime_mode = "LIVE"
        self._inference_enabled = True
        self.warmup_state = "READY"
        self._last_tick = SimpleNamespace(
            symbol="XAUUSD",
            timestamp=datetime(2026, 8, 19, 12, 0, 5, tzinfo=UTC),
            bid=2400.0,
            ask=2400.2,
        )
        self._last_fv = None
        if has_fv:
            self._last_fv = SimpleNamespace(
                to_tensor_input=lambda: [0.1 * (i % 7) - 0.2 for i in range(dim)],
                timestamp_utc="2026-08-19T12:00:04+00:00",
                feature_hash="f0001",
            )
        self._last_model_input_tensor = (
            [float(i) / 100.0 for i in range(dim)] if has_bundle else None
        )
        self._last_regime_state = SimpleNamespace(
            regime_type=SimpleNamespace(value="CALM"),
            regime_probability=0.9,
        )
        self._last_probs = None
        if has_bundle:
            import torch

            if classes == 4:
                self._last_probs = torch.tensor([[0.5, 0.3, 0.15, 0.05]])
            else:
                self._last_probs = torch.zeros(1, classes)
        self._last_proposal = SimpleNamespace(
            action=SimpleNamespace(value="NO_TRADE"),
            confidence=0.31,
            risk_reward_ratio=2.14,
            reason_code="CONFIDENCE_BELOW_THRESHOLD",
            model_action="NO_TRADE",
            decision_stage="CONFIDENCE_GATE",
            blocked_by="CONFIDENCE_GATE",
            rejection_reason="confidence 0.31 < 0.35",
            request_id="req-abc-123",
            generated_at=datetime(2026, 8, 19, 12, 0, 4, tzinfo=UTC),
            guardian_status="IDLE",
            risk_allowed=False,
            confidence_before_filters=0.42,
            confidence_after_filters=0.31,
        )
        self._last_experience_decision = SimpleNamespace(
            adjusted_confidence=0.30,
            action=SimpleNamespace(value="INSUFFICIENT_EVIDENCE"),
        )
        self._last_suitability_verdict = SimpleNamespace(
            adjusted_confidence=0.29,
            decision=SimpleNamespace(value="WARN"),
        )
        self._last_news_gate = SimpleNamespace(
            decision="IGNORE",
            confidence_adjustment=-0.02,
            blocked=False,
            reason="low relevance",
        )
        self._news_enabled = True
        self._news_worker_started = True
        self._accounting_worker_started = True
        self._intelligence_worker_started = True
        self._research_worker_started = True
        self._training_worker_started = True
        self._shadow_worker_started = True
        self._shadow70_worker_started = True
        self._shadow70_enabled = True
        self._survival_mode_active = False
        self._peak_equity = 10000.0
        self._last_inference_latency_ms = 3.2
        self.FEATURE_SCHEMA_ID = "scalp_v1"
        self.FEATURE_DIM = dim
        self.FEATURE_SCHEMA_HASH = None
        self.champion_manager = SimpleNamespace(
            model_id="champ-70d-0001",
            model_version="1.0.0",
            info=SimpleNamespace(scaler_hash="abc123", artifact_hash="def456"),
        )
        self.config = SimpleNamespace(
            execution=SimpleNamespace(symbol="XAUUSD", mode=SimpleNamespace(value="LIVE")),
            algo=SimpleNamespace(min_risk_reward_ratio=1.8),
            risk=SimpleNamespace(
                risk_per_trade_pct=0.5,
                max_concurrent_positions=1,
                max_spread_points=30,
                max_account_drawdown_pct=25,
            ),
            model=SimpleNamespace(confidence_threshold=0.35),
            base_dir="artifacts",
        )
        self.risk_engine = SimpleNamespace(
            _kill_switch_active=False,
            max_allowed_lots=5.0,
            min_risk_reward_ratio=1.8,
        )
        self.order_manager = _FakeOrderManager()
        self.liquidity_governor = _FakeGovernor()
        self.accounting_worker = SimpleNamespace(
            running=True,
            cycle_count=42,
            last_cycle_start=datetime.now(UTC),
            last_cycle_duration=0.05,
            last_error="",
        )
        self.history_sync_worker = SimpleNamespace(
            running=True,
            cycle_count=7,
            last_cycle_start=datetime.now(UTC),
            last_cycle_duration=0.01,
            last_error="",
        )
        self.intelligence_worker = SimpleNamespace(
            running=True,
            cycle_count=3,
            last_cycle_start=datetime.now(UTC),
            last_cycle_duration=0.02,
            last_error="",
        )
        self.research_worker = SimpleNamespace(
            running=True,
            cycle_count=1,
            last_cycle_start=datetime.now(UTC),
            last_cycle_duration=0.1,
            last_error="",
        )
        self.training_worker = SimpleNamespace(
            running=True,
            cycle_count=0,
            last_cycle_start=datetime.now(UTC),
            last_cycle_duration=0.0,
            last_error="",
        )
        self.shadow_worker = SimpleNamespace(
            running=True,
            cycle_count=11,
            last_cycle_start=datetime.now(UTC),
            last_cycle_duration=0.03,
            last_error="",
        )
        self._shadow70_worker = SimpleNamespace(
            running=True,
            cycle_count=9,
            last_cycle_start=datetime.now(UTC),
            last_cycle_duration=0.04,
            last_error="",
        )
        self.news_worker = SimpleNamespace(
            running=True,
            cycle_count=5,
            last_cycle_start=datetime.now(UTC),
            last_cycle_duration=0.01,
            last_error="",
            interval_sec=60.0,
            _jobs=SimpleNamespace(qsize=lambda: 0),
            _queued_ids=set(),
            engine=SimpleNamespace(
                current_context=_FakeNewsContext,
            ),
        )
        self.telegram_notifier = SimpleNamespace(
            health_state=lambda: {
                "state": "READY",
                "queue_size": 0,
                "last_success_at": datetime.now(UTC),
                "last_failure_at": None,
                "last_failure_category": "",
            }
        )
        self.news_engine = SimpleNamespace(
            current_context=_FakeNewsContext,
            db=SimpleNamespace(db_path="artifacts/news.db"),
        )
        self.adapter = SimpleNamespace(
            is_connected=lambda: True,
            get_broker_tick=lambda symbol: None,
            get_account_snapshot=lambda: SimpleNamespace(
                available=True,
                source="BROKER",
                balance=10000.0,
                equity=10100.0,
                margin_free=8000.0,
                margin=2100.0,
                margin_level=480.0,
                floating_pnl=100.0,
                open_positions_count=1,
                pending_orders_count=0,
            ),
            get_account_info=lambda: None,
            get_all_positions=lambda symbol=None: [_FakePosition(1001)],
            get_positions=lambda symbol=None: [_FakePosition(1001)],
            get_pending_orders=lambda symbol=None: [],
            connection_state=lambda: SimpleNamespace(
                to_dict=lambda: {"connected": True, "login": 12345}
            ),
            diagnostics_summary=lambda: {},
        )
        self.aggregator = SimpleNamespace(
            get_completed_bars=lambda: [
                SimpleNamespace(
                    timestamp=datetime(2026, 8, 19, 11, 59, 0, tzinfo=UTC),
                    open=2400.0,
                    high=2402.0,
                    low=2398.0,
                    close=2401.0,
                    tick_volume=12,
                ),
                SimpleNamespace(
                    timestamp=datetime(2026, 8, 19, 12, 0, 0, tzinfo=UTC),
                    open=2401.0,
                    high=2403.0,
                    low=2399.0,
                    close=2402.0,
                    tick_volume=9,
                ),
            ],
            get_current_forming_bar=lambda: None,
        )
        self.audit = SimpleNamespace(
            _db_path="artifacts/audit.db",
            _worker_thread=None,
            _queue=None,
            get_recent_predictions=lambda limit=40: [],
            get_trading_rules=lambda: [],
        )
        self.accounting_core = SimpleNamespace(
            load_trades=lambda limit=1000: [],
            live_state=lambda: SimpleNamespace(available=False),
        )
        self.signal_policy = SimpleNamespace(
            _last_active_direction=None,
            evaluate_probabilities=lambda **kw: self._last_proposal,
        )
        self.regime_classifier = SimpleNamespace(_stable_regime=None)
        self._bundle_lock = _FakeLock()
        self._bundle = _FakeBundle(num_features=dim, num_classes=classes) if has_bundle else None

    def _load_or_create_bundle(self, **kw):
        return self._bundle

    def _register_active_model(self, **kw):
        pass


def _make_app(engine) -> TestClient:
    from nexus_scalp.web.server import create_app as _create_app

    app = _create_app(engine)
    return TestClient(app)


# ---------------------------------------------------------------------------
# TEST-DEBUG-01..06 — schema / dimension / registry / values / status
# ---------------------------------------------------------------------------


class TestDebugSchemaContract:
    def test_debug_01_active_schema_displayed(self):
        """The snapshot exposes schema_id/dimension/hash/algorithm_version."""
        snap = build_debug_snapshot(None, None)
        rt = snap["runtime"]
        assert rt["schema_id"] == "scalp_v3"
        assert rt["dimension"] == 70
        assert rt["schema_hash"]
        assert rt["algorithm_version"]

    def test_debug_02_70d_dimension_validated(self):
        """Contract section: expected 70 vs actual runtime dim."""
        eng = _FakeEngine(dim=50)
        snap = build_debug_snapshot(eng, None)
        ct = snap["contract"]
        assert ct["expected_dimension"] == 70
        assert ct["actual_dimension"] == 50
        assert ct["status"] == "70D CONTRACT BROKEN"
        assert ct["dimension_match"] is False

    def test_debug_03_all_70_indices_displayed(self):
        """Feature matrix carries exactly indices 0..69."""
        snap = build_debug_snapshot(None, None)
        rows = snap["features"]["rows"]
        assert len(rows) == 70
        assert [r["index"] for r in rows] == list(range(70))

    def test_debug_04_feature_names_match_registry(self):
        """Names/families come from schema_contract, not hardcoded JS."""
        from nexus_scalp.features.schema_contract import canonical_feature_names

        names = canonical_feature_names()
        snap = build_debug_snapshot(None, None)
        rows = snap["features"]["rows"]
        assert [r["name"] for r in rows] == list(names)
        # family layout: base 0..49, news 50..59, liquidity 60..69
        assert all(r["family"] == "base" for r in rows[:50])
        assert all(r["family"] == "news" for r in rows[50:60])
        assert all(r["family"] == "liquidity" for r in rows[60:70])

    def test_debug_05_feature_values_match_backend(self):
        """Live base values equal the engine FeatureVector values."""
        eng = _FakeEngine(dim=50)
        snap = build_debug_snapshot(eng, None)
        rows = snap["features"]["rows"]
        base = [r for r in rows[:50]]
        expected = eng._last_fv.to_tensor_input()
        for i, row in enumerate(base):
            assert row["final"] == expected[i]

    def test_debug_06_feature_status_accurate(self):
        """Unavailable engine -> UNAVAILABLE status, never fake zero."""
        snap = build_debug_snapshot(None, None)
        rows = snap["features"]["rows"]
        for r in rows:
            assert r["status"] in ("VALID", "FALLBACK", "STALE", "UNAVAILABLE", "INVALID")
        # engine off -> base rows are UNAVAILABLE (explicit, not zero)
        assert rows[0]["status"] == "UNAVAILABLE"
        assert rows[0]["raw"] == "NOT_EXPOSED"


class TestDebugSections:
    def test_debug_07_liquidity_60_69_displayed(self):
        eng = _FakeEngine()
        snap = build_debug_snapshot(eng, None)
        liq_rows = snap["features"]["rows"][60:70]
        names = [r["name"] for r in liq_rows]
        assert names == [
            "bsl_distance_atr",
            "ssl_distance_atr",
            "eqh_strength",
            "eql_strength",
            "htf_liquidity_score",
            "internal_liquidity_distance",
            "external_liquidity_distance",
            "liquidity_confluence",
            "liquidity_sweep_state",
            "post_sweep_displacement",
        ]
        # values come from the governor snapshot
        assert liq_rows[0]["final"] == 0.5
        assert liq_rows[0]["source"] == "LIQUIDITY_GOVERNOR"

    def test_debug_08_news_state_displayed(self):
        eng = _FakeEngine()
        snap = build_debug_snapshot(eng, None)
        news = snap["news"]
        assert news["available"] is True
        assert news["state"] == "NORMAL"
        assert news["bullish"] == 0.2
        assert news["xauusd_relevance"] == 0.7
        assert news["high_impact"] == 2
        assert news["active_events"] == ["CPI", "FOMC"]
        assert len(news["model_dimensions"]) == 10

    def test_debug_09_model_input_displayed(self):
        eng = _FakeEngine()
        snap = build_debug_snapshot(eng, None)
        model = snap["model"]
        assert model["available"] is True
        assert model["input_tensor_shape"] == [1, 50]
        assert len(model["input_tensor"]) == 50
        assert model["input_dtype"] == "float32"
        assert model["schema_id"] == "scalp_v1"

    def test_debug_10_model_output_displayed(self):
        eng = _FakeEngine()
        snap = build_debug_snapshot(eng, None)
        model = snap["model"]
        assert model["status"] == "MODEL_OUTPUT_OK"
        assert model["probabilities"]["NO_TRADE"] == pytest.approx(0.5)
        assert model["probabilities"]["BUY_MARKET"] == pytest.approx(0.3)
        assert model["probabilities"]["SELL_MARKET"] == pytest.approx(0.15)
        assert model["probabilities"]["WAIT"] == pytest.approx(0.05)
        assert model["predicted_class"] == 0  # argmax NO_TRADE

    def test_debug_11_confidence_pipeline_displayed(self):
        eng = _FakeEngine()
        snap = build_debug_snapshot(eng, None)
        conf = snap["confidence"]
        assert conf["available"] is True
        assert conf["raw_confidence"] == 0.5
        assert conf["news_adjustment"] == -0.02
        assert conf["experience_adjusted_confidence"] == 0.30
        assert conf["final_confidence"] == 0.31
        assert conf["required_threshold"] == 0.35
        assert conf["decision"] == "REJECT"
        stage_names = [s["name"] for s in conf["stages"]]
        assert stage_names == ["RAW_MODEL", "NEWS_GATE", "EXPERIENCE", "SUITABILITY", "FINAL"]

    def test_debug_12_policy_gate_trace(self):
        eng = _FakeEngine()
        snap = build_debug_snapshot(eng, None)
        pol = snap["policy"]
        gates = {g["name"]: g for g in pol["gates"]}
        assert set(gates) >= {
            "SIGNAL",
            "CONFIDENCE",
            "REGIME",
            "R:R",
            "SAME-LEVEL",
            "NEWS",
            "EXPOSURE",
            "RISK",
            "EXECUTION",
        }
        assert gates["CONFIDENCE"]["status"] == "FAIL"
        assert gates["CONFIDENCE"]["actual"] == 0.31
        assert gates["CONFIDENCE"]["threshold"] == 0.35
        assert gates["R:R"]["actual"] == 2.14
        assert gates["RISK"]["status"] == "FAIL"
        assert pol["decision_stage"] == "CONFIDENCE_GATE"
        assert pol["blocked_by"] == "CONFIDENCE_GATE"

    def test_debug_13_risk_trace(self):
        eng = _FakeEngine()
        snap = build_debug_snapshot(eng, None)
        risk = snap["risk"]
        assert risk["available"] is True
        assert risk["account"]["balance"] == 10000.0
        assert risk["account"]["equity"] == 10100.0
        assert risk["decision"] == "BLOCK"
        assert risk["max_allowed_lots"] == 5.0
        assert risk["kill_switch_active"] is False

    def test_debug_14_exposure_broker_internal_comparison(self):
        eng = _FakeEngine()
        eng.order_manager._live_tickets_cache[1001] = {
            "type": "POSITION",
            "symbol": "XAUUSD",
            "ticket": 1001,
        }
        snap = build_debug_snapshot(eng, None)
        exp = snap["exposure"]
        assert exp["internal"]["positions"] == 1
        assert exp["internal"]["pendings"] == 0
        assert exp["broker"]["positions"] == 1
        assert exp["broker"]["pendings"] == 0
        assert exp["mismatch"] is False

    def test_debug_15_execution_trace(self):
        eng = _FakeEngine()
        eng.order_manager._processed_orders["o1"] = True
        snap = build_debug_snapshot(eng, None)
        ex = snap["execution"]
        assert ex["available"] is True
        assert ex["adapter"] is not None
        assert ex["connection"]["connected"] is True
        assert ex["processed_orders_count"] == 1

    def test_debug_16_position_state(self):
        eng = _FakeEngine()
        om = eng.order_manager
        om._mfe_tracker[1001] = 30.0
        om._mae_tracker[1001] = -5.0
        om._peak_profit_usd[1001] = 30.0
        om._entry_timestamps[1001] = datetime(2026, 8, 19, 11, 30, 0, tzinfo=UTC)
        snap = build_debug_snapshot(eng, None)
        pos = snap["positions"]["positions"][0]
        assert pos["ticket"] == 1001
        assert pos["mfe"] == 30.0
        assert pos["mae"] == -5.0
        assert pos["breakeven_armed"] is True
        assert pos["hold_seconds"] is not None and pos["hold_seconds"] > 0

    def test_debug_17_exit_decision_trace(self):
        eng = _FakeEngine()
        eng.order_manager._last_reasons_tracker[1001] = ["PROFIT_GIVEBACK", "REGIME_CHANGE"]
        snap = build_debug_snapshot(eng, None)
        ex = snap["exit"]
        assert ex["available"] is True
        pos = ex["positions"][0]
        assert pos["ai_state"] == "TRACKING"
        assert pos["regime"] == "CALM"
        assert pos["news_state"] == "NORMAL"
        assert [c["reason"] for c in pos["exit_candidates"]] == [
            "PROFIT_GIVEBACK",
            "REGIME_CHANGE",
        ]

    def test_debug_18_worker_state(self):
        eng = _FakeEngine()
        snap = build_debug_snapshot(eng, None)
        wk = snap["workers"]["workers"]
        assert wk["accounting"]["state"] == "RUNNING"
        assert wk["accounting"]["cycle"] == 42
        assert wk["news"]["state"] == "RUNNING"
        assert wk["telegram"]["state"] == "READY"
        assert "shadow70" in wk

    def test_debug_19_database_state(self):
        eng = _FakeEngine()
        snap = build_debug_snapshot(eng, None)
        db = snap["database"]
        assert "audit" in db["databases"]
        assert "news" in db["databases"]
        assert "candle_intel" in db["databases"]
        # paths masked (no machine-specific prefix)
        for d in db["databases"].values():
            if d.get("path"):
                assert not d["path"].startswith(("C:", "c:", "/"))

    def test_debug_20_cache_state(self):
        eng = _FakeEngine()
        snap = build_debug_snapshot(eng, None)
        caches = snap["caches"]["caches"]
        assert caches["model"]["status"] == "LOADED"
        assert caches["feature"]["status"] == "CACHED"
        assert caches["liquidity"]["status"] == "ENABLED"
        assert caches["news"]["status"] == "CACHED"

    def test_debug_21_chart_state(self):
        eng = _FakeEngine()
        app = create_app(eng)
        snap = build_debug_snapshot(eng, app.state)
        ch = snap["chart"]
        assert ch["bars_received"] >= 2
        assert ch["first_timestamp"] is not None

    def test_debug_22_sse_state(self):
        app = create_app(None)
        app.state.sse_diag.update(
            {
                "connection": "CONNECTED",
                "connected_at": "2026-08-19T12:00:00+00:00",
                "event_count": 5,
                "serialization_errors": 1,
                "serialization_error": {
                    "correlation_id": "sse-99",
                    "error": "boom",
                    "failed_fields": ["liquidity.pools[0].confirmed_at"],
                    "event_type": "state",
                },
                "reconnect_count": 2,
            }
        )
        snap = build_debug_snapshot(None, app.state)
        sse = snap["sse"]
        assert sse["connection"] == "CONNECTED"
        assert sse["serialization_errors"] == 1
        assert sse["serialization_error"]["correlation_id"] == "sse-99"
        # errors section surfaces it
        errs = snap["errors"]["errors"]
        assert any(e["error_code"] == "SSE_SERIALIZATION_ERROR" for e in errs)

    def test_debug_23_correlation_id(self):
        snap = build_debug_snapshot(None, None)
        assert snap["correlation_id"]
        assert snap["snapshot_id"]
        assert snap["timestamp"]

    def test_debug_24_snapshot_capture(self):
        store = DebugSnapshotStore(max_snapshots=3)
        store.push(build_debug_snapshot(None, None))
        store.push(build_debug_snapshot(None, None))
        lst = store.list()
        assert len(lst) == 2
        got = store.get(lst[0]["snapshot_id"])
        assert got["snapshot_id"] == lst[0]["snapshot_id"]

    def test_debug_25_json_snapshot_serialization(self):
        eng = _FakeEngine()
        snap = build_debug_snapshot(eng, None)
        raw = canonical_json(snap)
        parsed = json.loads(raw)
        assert parsed["features"]["health"]["total"] == 70

    def test_debug_26_datetime_serialization(self):
        eng = _FakeEngine()
        snap = build_debug_snapshot(eng, None)
        # all datetimes must serialize through canonical_json without default=str
        raw = canonical_json(snap)
        assert "2026-08-19" in raw
        json.loads(raw)

    def test_debug_27_invalid_model_contract_displayed(self):
        """REGRESSION (brief 46): actual_classes=128 vs expected 4."""
        eng = _FakeEngine(dim=50, classes=128)
        snap = build_debug_snapshot(eng, None)
        ct = snap["contract"]
        assert ct["actual_classes"] == 128
        assert ct["expected_classes"] == 4
        assert ct["classes_match"] is False
        assert ct["model_status"] == "MODEL CONTRACT INVALID"
        # model section must not crash and flags invalid output
        assert snap["model"]["status"] == "MODEL_OUTPUT_INVALID"

    def test_debug_28_70d_mismatch_displayed(self):
        """50D live runtime -> 70D CONTRACT BROKEN visible."""
        eng = _FakeEngine(dim=50)
        snap = build_debug_snapshot(eng, None)
        assert snap["contract"]["status"] == "70D CONTRACT BROKEN"
        assert snap["contract"]["dimension_match"] is False
        assert snap["features"]["active_dimension"] == 70  # matrix still registry-driven

    def test_debug_29_no_secret_leakage(self):
        eng = _FakeEngine()
        raw = canonical_json(build_debug_snapshot(eng, None))
        assert "bot_token" not in raw.lower()
        assert "api_key" not in raw.lower()
        assert "password" not in raw.lower()
        assert "private_key" not in raw.lower()
        assert "-----BEGIN" not in raw
        # paths are masked
        assert "/secret/host/machine/" not in raw

    def test_debug_30_debug_does_not_block_hot_path(self):
        """Snapshot builder performs no DB scans / no model reload / no
        feature recompute: verify via the fake engine's call surfaces that
        the builder touches only attributes (no compute methods invoked)."""
        eng = _FakeEngine()
        with patch.object(eng, "_load_or_create_bundle") as reload_mock:
            build_debug_snapshot(eng, None)
            reload_mock.assert_not_called()

    def test_debug_31_snapshot_comparison(self):
        a = build_debug_snapshot(_FakeEngine(), None)
        b = build_debug_snapshot(_FakeEngine(), None)
        diff = diff_snapshots(a, b)
        assert diff["a_id"] == a["snapshot_id"]
        assert diff["b_id"] == b["snapshot_id"]

    def test_debug_32_feature_diff(self):
        eng_a = _FakeEngine()
        eng_b = _FakeEngine()
        # change one LIQUIDITY feature value (idx 63 = eqh_strength, 60..69)
        eng_b.liquidity_governor = _FakeGovernor(eqh_strength=1.7)
        a = build_debug_snapshot(eng_a, None)
        b = build_debug_snapshot(eng_b, None)
        diff = diff_snapshots(a, b)
        # canonical liquidity order (schema_contract): 60 bsl, 61 ssl,
        # 62 eqh_strength, 63 eql_strength, ...
        by_idx = {f["index"]: f for f in diff["feature_diffs"]}
        assert 62 in by_idx
        assert by_idx[62]["t0"] == 1.2
        assert by_idx[62]["t1"] == 1.7
        assert by_idx[62]["delta"] == 0.5


# ---------------------------------------------------------------------------
# API-level tests through the real FastAPI app
# ---------------------------------------------------------------------------


class TestDebugApi:
    def _client(self, engine=None):
        return _make_app(engine)

    def test_api_state_endpoint(self):
        c = self._client(None)
        r = c.get("/api/debug/state")
        assert r.status_code == 200
        d = r.json()
        assert d["snapshot_id"] and d["correlation_id"]
        assert d["features"]["health"]["total"] == 70

    def test_api_snapshots_endpoints(self):
        c = self._client(_FakeEngine())
        c.get("/api/debug/state")
        lst = c.get("/api/debug/snapshots").json()
        assert lst["available"] is True
        assert len(lst["snapshots"]) >= 1
        sid = lst["snapshots"][0]["snapshot_id"]
        got = c.get(f"/api/debug/snapshots/{sid}").json()
        assert got["snapshot_id"] == sid
        miss = c.get("/api/debug/snapshots/does-not-exist").json()
        assert "NOT_FOUND" in miss["reason"]
        cmp = c.get(
            "/api/debug/compare",
            params={"a": sid, "b": sid},
        ).json()
        assert cmp["a_id"] == sid

    def test_api_compare_requires_both(self):
        c = self._client(_FakeEngine())
        r = c.get("/api/debug/compare", params={"a": "x", "b": "y"}).json()
        assert r["available"] is False

    def test_sse_serialization_error_surfaces_in_debug(self):
        """REGRESSION (brief 47): a payload with datetime + liquidity +
        model + feature vector must serialize; when a field breaks, the
        debug console surfaces SSE_SERIALIZATION_ERROR with correlation_id."""
        # payload with every canonical type the SSE stream can carry
        payload = {
            "state_version": 5,
            "snapshot_timestamp": datetime.now(UTC).isoformat(),
            "liquidity": {
                "pools": [{"side": "BSL", "confirmed_at": datetime.now(UTC)}],
            },
            "model": {"probabilities": {"buy": 0.5}},
            "features": [0.1, 0.2],
            "probs": {"no_trade": 0.4},
        }
        frame = canonical_json(payload)  # must not raise
        json.loads(frame)

        # broken field -> _find_non_json_fields locates it
        from nexus_scalp.web.server import _find_non_json_fields

        broken = {"liquidity": {"pools": [{"state": object()}]}}
        fields = _find_non_json_fields(broken)
        assert any("state" in f for f in fields)

    def test_api_state_no_exception_text_when_sections_raise(self):
        """CodeQL py/stack-trace-exposure (#84) regression: when a snapshot
        section raises, the payload carries a STABLE error code and the
        exception text stays server-side only. Exception text, paths, SQL or
        tracebacks must never appear in the JSON response body."""
        import nexus_scalp.web.debug_snapshot as ds

        def _boom(engine):
            raise RuntimeError("SECRET_INTERNAL_MARKER_FEATURES")

        with patch.object(ds, "_features_section", side_effect=_boom):
            c = self._client(_FakeEngine())
            r = c.get("/api/debug/state")
        assert r.status_code == 200
        body = r.text
        assert "SECRET_INTERNAL_MARKER_FEATURES" not in body
        assert "RuntimeError" not in body
        assert "Traceback" not in body
        assert "C:/Users" not in body
        # The stable code IS present so the UI can render a truthful error
        d = r.json()
        features = d["features"]
        assert features["available"] is False
        assert features["reason"] == "SECTION_ERROR"

    def test_section_error_reason_has_no_exc_interpolation(self):
        """CodeQL py/stack-trace-exposure (#84) regression: no reason field
        in the snapshot payload may embed exception text (f-string '{exc}',
        str(exc), tracebacks, paths, SQL). Legitimate stable codes / state
        markers are fine - exception internals are server-side only."""
        import re as _re

        c = self._client(None)
        r = c.get("/api/debug/state")
        assert r.status_code == 200
        body = r.text
        # exception internals must never appear in the wire body
        assert "RuntimeError" not in body
        assert "Traceback" not in body
        assert "C:/Users" not in body
        assert "Error(" not in body
        d = r.json()
        for sec_name, sec in d.items():
            if isinstance(sec, dict) and "reason" in sec:
                reason = sec["reason"]
                assert isinstance(reason, str)
                # stable code format: uppercase tokens separated by _
                assert _re.fullmatch(r"[A-Z][A-Z0-9_]*(?: [A-Z][A-Z0-9_]*)*", reason), (
                    f"reason {reason!r} in section {sec_name} looks like exception text"
                )

    def test_contract_section_engine_none_no_unbound_var(self):
        """BUG-137 regression: _contract_section must render cleanly when
        engine is None (offline / pre-start). It previously referenced
        live_tensor_schema only inside the `if engine is not None` branch
        but returned it unconditionally -> NameError -> the entire
        /api/debug/state contract section failed to build (Intelligence
        Hub showed no live 70D contract status). The variable is now
        initialized at function scope so the contract section is always
        emitted with explicit unavailable markers, never a crash."""
        from nexus_scalp.web import debug_snapshot as ds

        # Must not raise even with engine=None.
        contract = ds._contract_section(None)
        assert isinstance(contract, dict)
        # explicit unavailable markers, not a fabricated OK state
        assert contract.get("live_tensor_schema") is None
        assert "status" in contract
        assert "70D CONTRACT" in contract["status"]
        # And the full snapshot + live state endpoints must still return 200.
        from fastapi.testclient import TestClient

        from nexus_scalp.web.server import create_app

        app = create_app()
        app.state.engine = None
        c = TestClient(app)
        assert c.get("/api/debug/state").status_code == 200
        assert c.get("/api/live/state").status_code == 200
