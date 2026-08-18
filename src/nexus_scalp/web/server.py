"""
FastAPI Production Control Dashboard Backend
============================================
Handles high-performance async REST APIs and Server-Sent Events (SSE) live telemetry streams
connecting the modern front-end console to real-time broker states, AI parameters,
and risk engines.
"""

import asyncio
import json
import math
import sqlite3
import threading
import time
from collections import deque
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from nexus_scalp.accounting import PeriodKind
from nexus_scalp.accounting.aggregation import compute_advanced_metrics
from nexus_scalp.accounting.worker import format_worker_status
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ActionType, ExecutionMode, OrderType
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.observability.telegram_notifier import TelegramNotifier
from nexus_scalp.web.errors import (
    log_web_error,
    new_request_id,
    safe_error_payload,
)


def serialize_enums(obj: Any) -> Any:
    """Recursively converts Enum instances to their underlying values."""
    if isinstance(obj, dict):
        return {k: serialize_enums(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_enums(x) for x in obj]
    elif isinstance(obj, Enum):
        return obj.value
    return obj


logger = get_logger("nexus_scalp.web.server")

# Global/Static UI folder relative path.
#
# CANONICAL WEB ASSET SOURCE (BUG-077): the dashboard bundles (app.js,
# api_client.js, index.html, styles.css) are served from the repository
# `Web/` directory in development, and from the bundled `_internal/Web`
# directory in the packaged release (PyInstaller --add-data). Resolve the
# canonical directory at import time so the file server serves the SAME
# revision the release pipeline verified, and so `[UI_FORENSIC]` lines in
# the log identify which bundle is actually being served.
WEB_DIR = Path("Web")


def _resolve_web_root() -> Path:
    """Return the canonical Web asset directory for this runtime.

    Priority: an explicit ``NEXUS_WEB_DIR`` override (tests), then a bundled
    ``_internal/Web`` next to this package (packaged release), then the
    repository ``Web/`` directory (dev). The choice is logged once so
    operators can see which bundle is served.
    """
    import os

    override = os.environ.get("NEXUS_WEB_DIR")
    if override:
        return Path(override)
    packaged = Path(__file__).resolve().parent.parent.parent.parent / "_internal" / "Web"
    if packaged.is_dir():
        return packaged
    return Path("Web") if Path("Web").is_dir() else packaged


WEB_DIR = _resolve_web_root()


# ---------------------------------------------------------------------------
# Live-state snapshot identity: strictly monotonic across the server lifetime.
# A per-snapshot id lets the UI reject out-of-order state updates after an SSE
# reconnect, and a wall-clock `generated_at` plus per-section timestamps lets
# the UI compute real freshness (state age, tick age, inference age ...).
# ---------------------------------------------------------------------------
class StateVersioner:
    """Server-lifetime monotonic snapshot revision (thread-safe, lock-free by
    design: a single request may read it before bumping, so two concurrent
    snapshots can briefly share a version - never decrease)."""

    def __init__(self) -> None:
        self._v: int = 0

    def bump(self) -> int:
        self._v += 1
        return self._v

    @property
    def current(self) -> int:
        return self._v


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class ServerState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.bars: list[dict[str, Any]] = []
        self.real_overlays: dict[str, Any] = {
            "rectangles": [],
            "bos_lines": [],
            "midlines": [],
            "liq_markers": [],
        }
        # Diagnostics: last time each runtime producer refreshed the shared
        # state. Used by /api/live/state to report component age.
        self.last_bars_at: float | None = None
        self.last_overlays_at: float | None = None

    def update_live_visuals(
        self, bars: list[dict[str, Any]], real_overlays: dict[str, Any]
    ) -> None:
        with self._lock:
            self.bars = list(bars)
            self.real_overlays = dict(real_overlays)
            now = time.monotonic()
            self.last_bars_at = now
            self.last_overlays_at = now

    def get_live_visuals(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        with self._lock:
            return list(self.bars), dict(self.real_overlays)

    def visuals_age_sec(self) -> float | None:
        """Seconds since the engine last pushed chart state (None = never)."""
        with self._lock:
            if self.last_bars_at is None:
                return None
            return max(0.0, time.monotonic() - self.last_bars_at)


# Define API request bodies
class ModifyPositionRequest(BaseModel):
    ticket: int
    stop_loss: float
    take_profit: float


class ClosePositionRequest(BaseModel):
    ticket: int


class ToggleRequest(BaseModel):
    active: bool


class AlgoConfigRequest(BaseModel):
    atr_sl_buffer_multiplier: float
    min_risk_reward_ratio: float
    ai_zone_confidence_threshold: float
    fvg_mitigation_sensitivity: float
    order_block_lookback_bars: int


class ToggleReplayRequest(BaseModel):
    active: bool
    speed: int = 1


class SimulationTickRequest(BaseModel):
    type: str  # 'BUY_PRESSURE', 'SELL_PRESSURE', 'VOLATILE_SWEEP'


class ToggleRuleRequest(BaseModel):
    rule_name: str
    is_enabled: bool
    parameters: dict[str, Any] | None = None


class ModelTestRequest(BaseModel):
    """
    Debug Hub model-test payload.

    `features` accepts the 50-dimensional vector directly. When omitted, the live
    feature vector is used, which makes the endpoint a one-click "what does the net
    think right now" probe.
    """

    features: list[float] | None = None
    use_live_features: bool = False


def _classify_feature(value: Any) -> tuple[float, str]:
    """
    Normalizes a raw feature value and classifies its health for the Debug Hub.

    Returns:
        (sanitized_value, status) where status is VALID, NAN, INF or NON_NUMERIC.
    """
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return 0.0, "NON_NUMERIC"

    if math.isnan(fval):
        return 0.0, "NAN"
    if math.isinf(fval):
        return 0.0, "INF"
    return fval, "VALID"


async def _run_training_async(orchestrator: Any, dataset: Any, num_epochs: int) -> dict[str, Any]:
    """Runs controlled training off the event loop via asyncio.to_thread."""
    import asyncio

    return await asyncio.to_thread(
        orchestrator.run_controlled_training, dataset, num_epochs=num_epochs
    )


def _web_root_is_repo() -> bool:
    """True when WEB_DIR is the repository Web/ directory (dev)."""
    repo_web = Path("Web").resolve()
    return WEB_DIR.resolve() == repo_web


def _ui_bundle_sha256() -> str:
    """Deterministic sha256 of the served app.js bundle (cache per process)."""
    try:
        blob = (WEB_DIR / "app.js").read_bytes()
        import hashlib

        return hashlib.sha256(blob).hexdigest()
    except OSError:
        return ""


_UI_FORENSIC_LOGGED: bool = False


def _ui_forensic_once() -> None:
    """Log one [UI_FORENSIC] line identifying the served bundle."""
    global _UI_FORENSIC_LOGGED  # noqa: PLW0603 - module-level once-flag
    if _UI_FORENSIC_LOGGED:
        return
    _UI_FORENSIC_LOGGED = True
    source = "REPO" if _web_root_is_repo() else "PACKAGED"
    logger.info(
        "[UI_FORENSIC]",
        served_bundle="app.js",
        source=source,
        bytes=(WEB_DIR / "app.js").stat().st_size if (WEB_DIR / "app.js").exists() else -1,
        sha256=_ui_bundle_sha256(),
        web_dir=str(WEB_DIR),
    )


def create_app(engine_ref: Any = None) -> FastAPI:
    """Creates and configures the FastAPI web server instance."""
    app = FastAPI(title="Nexus Scalp Engine Control Center", version="0.1.0")

    # Store engine reference in app state
    app.state.engine = engine_ref
    app.state.server_state = ServerState()
    # Server-lifetime monotonic snapshot identity (never resets, survives SSE
    # reconnects; the UI rejects out-of-order versions).
    app.state.versioner = StateVersioner()
    # Bounded per-stream event ring for reconnect resynchronization.
    app.state.stream_history = deque(maxlen=200)  # type: ignore[assignment]
    # News refresh cooldown (bandwidth guard): monotonic timestamp of the last
    # forced fetch so repeated "Fetch News" clicks cannot hammer RSS feeds.
    import threading

    app.state.news_refresh_lock = threading.Lock()
    app.state.news_refresh_ts = 0.0

    # DASHBOARD HARDENING: correlation + sanitized 500s for every HTTP route.
    from nexus_scalp.web.errors import attach_request_id_middleware

    @app.middleware("http")
    async def _correlation_middleware(request: Request, call_next):
        return await attach_request_id_middleware(request, call_next)

    # ------------------------------------------------------------------
    # Unified safe error-handling helpers (module-visible to every route).
    # PUBLIC:  _err(code, **kw) -> sanitized envelope with request_id.
    # INTERNAL: _log_err(exc, msg, ...) -> full traceback to logs only.
    # ------------------------------------------------------------------
    def _err(code: str = "INTERNAL_ERROR", **kw: Any) -> dict[str, Any]:
        return safe_error_payload(code=code, request_id=new_request_id(), **kw)

    def _log_err(
        exc: BaseException, msg: str, *, endpoint: str = "/api", resource: str | None = None
    ) -> None:
        log_web_error(
            logger,
            endpoint,
            new_request_id(),
            exc,
            resource=resource,
            context={"msg": msg},
        )

    # Active simulation and replay parameters
    app.state.is_replaying = False
    app.state.replay_speed = 1
    app.state.simulated_history_ticks = []

    # Keep track of simulated signal outcomes
    app.state.simulated_outcomes = []

    # Helper functions: timestamp age + subsystem health (single source for
    # the /api/status health section and /api/live/state health block).
    def _age_sec(mono_now: float, iso_ts: str | None) -> float | None:
        """Age in seconds of an ISO timestamp relative to a monotonic anchor.

        The anchor is captured at snapshot start; parsing failures return
        None (unknown age - the UI renders UNAVAILABLE, never 0).
        """
        if not iso_ts:
            return None
        try:
            dt = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return max(0.0, (datetime.now(UTC) - dt).total_seconds())
        except (TypeError, ValueError):
            return None

    def _build_health_section(state_obj: Any, mono_now: float) -> dict[str, Any]:
        """Live subsystem health derived from REAL engine/DB state.

        Distinguishes ENGINE RUNNING from MT5 UNAVAILABLE explicitly so the
        dashboard can never present a stale live price as current.
        """
        engine = state_obj.engine
        subsystems: dict[str, Any] = {}
        details: dict[str, Any] = {}

        # --- engine ---
        running = bool(getattr(engine, "_running", False)) if engine else False
        mode = None
        try:
            mode = engine.config.execution.mode.value if engine else None
        except Exception:
            mode = None
        warmup = getattr(engine, "warmup_state", None) if engine else None
        inference_enabled = bool(getattr(engine, "_inference_enabled", False)) if engine else False
        if engine is None:
            engine_status = "UNAVAILABLE"
            details["engine"] = "engine reference not attached to web server"
        elif not running:
            engine_status = "STOPPED"
            details["engine"] = "engine loop is not running"
        elif warmup == "READY" and inference_enabled:
            engine_status = "READY"
            details["engine"] = f"engine running · warmup READY · inference ENABLED ({mode})"
        else:
            engine_status = "WARMING_UP"
            details["engine"] = f"engine running · warmup {warmup} · inference BLOCKED ({mode})"
        subsystems["engine"] = engine_status

        # --- mt5 / adapter ---
        adapter_status = "UNAVAILABLE"
        adapter_detail = "no engine"
        tick_age: float | None = None
        if engine is not None:
            try:
                is_conn = getattr(engine.adapter, "is_connected", None)
                connected = bool(is_conn()) if callable(is_conn) else True
                tick = getattr(engine, "_last_tick", None)
                if tick is not None and getattr(tick, "timestamp", None) is not None:
                    try:
                        tick_age = max(0.0, (datetime.now(UTC) - tick.timestamp).total_seconds())
                    except Exception:
                        tick_age = None
                if not connected:
                    adapter_status = "DISCONNECTED"
                    adapter_detail = "broker adapter reports disconnected"
                elif tick_age is None:
                    adapter_status = "WAITING_TICK"
                    adapter_detail = "connected but no tick received yet"
                elif tick_age > 15.0:
                    adapter_status = "STALE"
                    adapter_detail = f"tick stream stale ({tick_age:.1f}s since last tick)"
                else:
                    adapter_status = "READY"
                    adapter_detail = f"live tick stream ({tick_age:.1f}s ago)"
            except Exception as e:
                log_web_error(
                    logger, "/api", None, e, context={"msg": "Health: adapter introspection failed"}
                )
                adapter_status = "ERROR"
                adapter_detail = "adapter introspection failed"
        subsystems["mt5"] = adapter_status
        details["mt5"] = adapter_detail

        # --- database ---
        db_status = "UNAVAILABLE"
        db_detail = "no engine"
        if engine is not None:
            try:
                repo = engine.audit
                worker = getattr(repo, "_worker_thread", None)
                worker_alive = bool(worker.is_alive()) if worker is not None else False
                queue_obj = getattr(repo, "_queue", None)
                queue_size = int(queue_obj.qsize()) if queue_obj is not None else 0
                if worker_alive and queue_size <= 5000:
                    db_status = "READY"
                    db_detail = f"WAL worker alive · queue {queue_size}"
                elif worker_alive:
                    db_status = "DEGRADED"
                    db_detail = f"write queue backing up ({queue_size} pending)"
                else:
                    db_status = "DEGRADED"
                    db_detail = "background write worker not running"
            except Exception as e:
                log_web_error(
                    logger,
                    "/api",
                    None,
                    e,
                    context={"msg": "Health: database introspection failed"},
                )
                db_status = "ERROR"
                db_detail = "database introspection failed"
        subsystems["database"] = db_status
        details["database"] = db_detail

        # --- model ---
        if engine is None:
            model_status = "UNAVAILABLE"
            model_detail = "engine offline; no model bundle"
        else:
            try:
                with engine._bundle_lock:
                    bundle = engine._bundle
                if bundle is None:
                    model_status = "UNAVAILABLE"
                    model_detail = "model bundle not initialized"
                else:
                    scaler_ready = bool(getattr(bundle.scaler, "is_ready", lambda: False)())
                    if not scaler_ready:
                        model_status = "DEGRADED"
                        model_detail = "weights loaded but scaler not fitted"
                    elif getattr(engine, "_last_probs", None) is None:
                        model_status = "WARMING_UP"
                        model_detail = "model ready; awaiting first live inference"
                    else:
                        model_status = "READY"
                        model_detail = "model loaded · inference flowing"
            except Exception as e:
                log_web_error(
                    logger, "/api", None, e, context={"msg": "Health: model introspection failed"}
                )
                model_status = "ERROR"
                model_detail = "model introspection failed"
        subsystems["model"] = model_status
        details["model"] = model_detail

        # --- news ---
        if engine is None or not getattr(engine, "_news_enabled", False):
            news_status = "DISABLED"
            news_detail = "news subsystem not enabled in config"
        else:
            news_status = "READY"
            news_detail = "news engine enabled"
            try:
                ctx = engine.news_engine.current_context()
                if ctx is not None and getattr(ctx, "stale", False):
                    news_status = "STALE"
                    news_detail = "news context stale (no recent fetch)"
            except Exception as e:
                log_web_error(
                    logger, "/api", None, e, context={"msg": "Health: news introspection failed"}
                )
                news_status = "ERROR"
                news_detail = "news introspection failed"
        subsystems["news"] = news_status
        details["news"] = news_detail

        # --- workers ---
        worker_states: dict[str, Any] = {}
        for name, flag, started in (
            ("accounting", "_accounting_worker_started", engine is not None),
            ("intelligence", "_intelligence_worker_started", engine is not None),
            ("research", "_research_worker_started", engine is not None),
            ("training", "_training_worker_started", engine is not None),
            ("shadow", "_shadow_worker_started", engine is not None),
            ("news", "_news_worker_started", engine is not None),
        ):
            worker_states[name] = bool(getattr(engine, flag, False)) if started else False
        subsystems["workers"] = "READY" if any(worker_states.values()) else "IDLE"
        details["workers"] = worker_states

        # --- overall (worst wins) ---
        rank = {
            "READY": 0,
            "IDLE": 0,
            "WARMING_UP": 1,
            "STALE": 1,
            "DEGRADED": 2,
            "DISCONNECTED": 3,
            "ERROR": 3,
            "UNAVAILABLE": 3,
            "STOPPED": 3,
            "DISABLED": 0,
        }
        overall = "READY"
        for sub_status in subsystems.values():
            if rank.get(sub_status, 0) > rank.get(overall, 0):
                overall = sub_status

        return {
            "overall": overall,
            "subsystems": subsystems,
            "details": details,
            "checked_at": _iso_now(),
        }

    # Helper function to get live data from engine or return explicit unavailable state
    def get_system_state() -> dict[str, Any]:
        """Canonical live-state snapshot for the dashboard.

        FORENSIC HARDENING (2026-08-17): every field is either REAL engine
        state or an explicit unavailable marker - NEVER a synthetic value.
        When the engine has no tick / features / model output yet, the
        corresponding section reports `available: False` and the UI renders
        "WAITING FOR LIVE STATE" instead of fake numbers. Provenance labels
        (`source: LIVE_MT5 | ENGINE_STATE | MODEL_INFERENCE |
        ACCOUNTING_CORE | RESEARCH_REGISTRY | UNAVAILABLE`) and a snapshot
        identity (`state_version`, `snapshot_timestamp`, per-section
        timestamps) let the frontend detect mixed-age renders.
        """
        engine = app.state.engine
        now_iso = _iso_now()
        now_mono = time.monotonic()

        # Strictly monotonic snapshot identity (server lifetime).
        state_version = app.state.versioner.bump()

        # Retrieve thread-safe live visuals state if available
        real_bars = []
        real_smc_overlays = {}
        if hasattr(app.state, "server_state") and app.state.server_state is not None:
            real_bars, real_smc_overlays = app.state.server_state.get_live_visuals()

        # --- Explicit unavailable defaults (NEVER fake numbers) ------------
        symbol: str | None = None
        bid: float | None = None
        ask: float | None = None
        spread: float | None = None
        atr: float | None = None
        regime: str | None = None
        engine_running = False
        execution_mode: str | None = None
        runtime_mode: str | None = None
        tick_timestamp: str | None = None
        price_source = "UNAVAILABLE"
        tick_stale: bool = False
        tick_freshness_ms: float | None = None

        account_data: dict[str, Any] = {
            "available": False,
            "source": "UNAVAILABLE",
            "login": None,
            "server": None,
            "company": None,
            "currency": None,
            "leverage": None,
            "trade_mode": None,
            "trade_allowed": None,
            "balance": None,
            "credit": None,
            "equity": None,
            "profit": None,
            "margin": None,
            "margin_free": None,
            "margin_level": None,
            "floating": None,
            "drawdown": None,
            "win_rate": None,
            "open_positions": None,
            "pending_orders": None,
        }

        positions_list: list[dict[str, Any]] = []
        bars_list: list[dict[str, Any]] = []
        features_values: list[float] = []
        features_timestamp: str | None = None
        features_source = "UNAVAILABLE"

        probs_data: dict[str, Any] = {
            "available": False,
            "no_trade": None,
            "buy": None,
            "sell": None,
        }
        model_meta: dict[str, Any] = {
            "available": False,
            "model_id": None,
            "model_version": None,
            "architecture": None,
            "artifact_path": None,
            "feature_schema_id": None,
            "feature_dimension": None,
            "scaler_ready": None,
            "inference_timestamp": None,
            "latency_ms": None,
        }
        ai_decision: str | None = None
        ai_confidence: float | None = None
        ai_reason: str | None = None
        proposal_timestamp: str | None = None

        # Read actual live engine state if connected
        if engine:
            try:
                symbol = engine.config.execution.symbol or "XAUUSD"
            except Exception:
                symbol = None
            engine_running = bool(getattr(engine, "_running", False))
            try:
                execution_mode = engine.config.execution.mode.value
            except Exception:
                execution_mode = None

            # Fetch MT5 live ticks and prices (real broker tick - task 11).
            try:
                # Use the typed broker tick first (has freshness/stale flags),
                # falling back to the engine's synchronized last tick.
                broker_tick = engine.adapter.get_broker_tick(symbol)
                if broker_tick and broker_tick.available and broker_tick.bid and broker_tick.ask:
                    bid = broker_tick.bid
                    ask = broker_tick.ask
                    spread = round((broker_tick.ask - broker_tick.bid) * 100, 2)
                    tick_timestamp = (
                        broker_tick.time_utc.isoformat() if broker_tick.time_utc else None
                    )
                    tick_stale = bool(broker_tick.stale)
                    tick_freshness_ms = broker_tick.freshness_ms
                    price_source = "LIVE_MT5"
                else:
                    tick = engine._last_tick
                    if tick:
                        bid = tick.bid
                        ask = tick.ask
                        spread = round((tick.ask - tick.bid) * 100, 2)
                        tick_timestamp = tick.timestamp.isoformat()
                        price_source = "ENGINE_STATE"
            except Exception:
                pass

            # Real runtime mode (never derived from config alone).
            try:
                runtime_mode = (
                    engine._runtime_mode
                    if getattr(engine, "_runtime_mode", None)
                    else engine.config.execution.mode.value
                )
            except Exception:
                runtime_mode = None

            # Fetch regime state & ATR from the exact synchronized last state
            try:
                reg_state = engine._last_regime_state
                if reg_state:
                    regime = reg_state.regime_type.name
                    atr = reg_state.realized_volatility_5m  # Single source of truth for volatility
                elif (
                    hasattr(engine, "regime_classifier") and engine.regime_classifier._stable_regime
                ):
                    regime = engine.regime_classifier._stable_regime.name
            except Exception:
                pass

            # Fetch account info (full typed broker snapshot when available)
            try:
                snap = getattr(engine, "_account_snapshot", None)
                if snap is None or not getattr(snap, "available", False):
                    snap = engine.adapter.get_account_snapshot()
                if snap and getattr(snap, "available", False):
                    account_data["available"] = True
                    account_data["source"] = snap.source or "ACCOUNTING_CORE"
                    account_data["login"] = getattr(snap, "login", None)
                    account_data["server"] = getattr(snap, "server", None)
                    account_data["company"] = getattr(snap, "company", None)
                    account_data["currency"] = getattr(snap, "currency", None)
                    account_data["leverage"] = getattr(snap, "leverage", None)
                    account_data["trade_mode"] = getattr(snap, "trade_mode", None)
                    account_data["trade_allowed"] = getattr(snap, "trade_allowed", None)
                    account_data["balance"] = getattr(snap, "balance", None)
                    account_data["credit"] = getattr(snap, "credit", None)
                    account_data["equity"] = getattr(snap, "equity", None)
                    account_data["profit"] = getattr(snap, "profit", None)
                    account_data["margin"] = getattr(snap, "margin", None)
                    account_data["margin_free"] = getattr(snap, "margin_free", None)
                    account_data["margin_level"] = getattr(snap, "margin_level", None)
                    account_data["floating"] = getattr(snap, "floating_pnl", None)
                    account_data["open_positions"] = getattr(snap, "open_positions_count", None)
                    account_data["pending_orders"] = getattr(snap, "pending_orders_count", None)
                    account_data["drawdown"] = (
                        (
                            (engine._peak_equity - account_data["equity"])
                            / max(engine._peak_equity, 1.0)
                        )
                        * 100.0
                        if engine._peak_equity > 0 and account_data["equity"] is not None
                        else None
                    )
                else:
                    acc = engine.adapter.get_account_info()
                    if acc:
                        account_data["available"] = True
                        account_data["source"] = "ACCOUNTING_CORE"
                        account_data["login"] = acc.login
                        account_data["balance"] = acc.balance
                        account_data["equity"] = acc.equity
                        account_data["floating"] = acc.equity - acc.balance
                        account_data["margin_free"] = acc.margin_free
                        account_data["drawdown"] = (
                            ((engine._peak_equity - acc.equity) / max(engine._peak_equity, 1.0))
                            * 100.0
                            if engine._peak_equity > 0
                            else None
                        )
            except Exception:
                pass

            # Real win rate from the canonical AccountingCore (authoritative
            # ledger), not from the legacy duplicate calculator. Unavailable
            # stays None.
            try:
                core = getattr(engine, "accounting_core", None)
                if core is not None:
                    trades = core.load_trades(limit=1000)
                    closed = [t for t in trades if t.closed_at is not None]
                    decided = sum(1 for t in closed if t.outcome.value in ("WIN", "LOSS"))
                    wins = sum(1 for t in closed if t.is_win)
                    if decided:
                        account_data["win_rate"] = round(wins / decided * 100.0, 2)
            except Exception:
                pass

            # Fetch positions (ALL account positions - never restricted to bot magic)
            try:
                all_positions = engine.adapter.get_all_positions(symbol=symbol)
                for p in all_positions:
                    positions_list.append(
                        {
                            "ticket": getattr(p, "ticket", None),
                            "symbol": getattr(p, "symbol", None),
                            "type": getattr(p, "type", None),
                            "volume": getattr(p, "volume", None),
                            "price_open": getattr(p, "price_open", None),
                            "price_current": getattr(p, "price_current", None),
                            "sl": getattr(p, "sl", None),
                            "tp": getattr(p, "tp", None),
                            "profit": getattr(p, "profit", None),
                            "swap": getattr(p, "swap", None),
                            "commission": getattr(p, "commission", None),
                            "magic": getattr(p, "magic", None),
                            "time": getattr(p, "time", None),
                        }
                    )
                if positions_list:
                    account_data["open_positions"] = len(positions_list)
            except Exception:
                pass

            # Fetch bars (synchronized completed history) - Expand to 900 completed bars
            # for 900+ visible bars support (BUG-054 resync: after 5-6h downtime the
            # broker history must fully repaint, never a truncated 250-bar window).
            if real_bars and len(real_bars) >= 100:
                bars_list = real_bars
            else:
                try:
                    completed_bars = engine.aggregator.get_completed_bars()
                    for b in completed_bars[-900:]:
                        bars_list.append(
                            {
                                "time": b.timestamp.isoformat(),
                                "open": b.open,
                                "high": b.high,
                                "low": b.low,
                                "close": b.close,
                                "volume": b.tick_volume,
                                "is_complete": True,
                            }
                        )
                    # Single Source of Truth forming candle injection
                    forming_bar = engine.aggregator.get_current_forming_bar()
                    if forming_bar:
                        bars_list.append(
                            {
                                "time": forming_bar.timestamp.isoformat(),
                                "open": forming_bar.open,
                                "high": forming_bar.high,
                                "low": forming_bar.low,
                                "close": forming_bar.close,
                                "volume": forming_bar.tick_volume,
                                "is_complete": False,
                            }
                        )
                except Exception as e:
                    log_web_error(
                        logger,
                        "/api",
                        None,
                        e,
                        context={"msg": "Failed to fetch synchronized bar stream"},
                    )

            # Fetch synchronized features and model predictions
            try:
                fv = engine._last_fv
                if fv:
                    features_values = list(fv.to_tensor_input())
                    features_timestamp = getattr(fv, "timestamp_utc", None) or (
                        getattr(fv, "timestamp", None)
                    )
                    features_source = "ENGINE_STATE"

                # Sync actual live inference probabilities
                probs = engine._last_probs
                if probs is not None:
                    probs_list = probs.cpu().numpy().flatten().tolist()
                    probs_data = {
                        "available": True,
                        "no_trade": float(probs_list[0]),
                        "buy": float(probs_list[1]),
                        "sell": float(probs_list[2]),
                        "inference_timestamp": (
                            features_timestamp or datetime.now(UTC).isoformat()
                        ),
                    }
                    # Model metadata from the live bundle (real provenance)
                    try:
                        with engine._bundle_lock:
                            bundle = engine._bundle
                        if bundle is not None:
                            model_meta["available"] = True
                            model_meta["artifact_path"] = str(bundle.artifact_path)
                            model_meta["architecture"] = "ScalpNet"
                            model_meta["feature_schema_id"] = getattr(
                                engine, "FEATURE_SCHEMA_ID", "scalp_v1"
                            )
                            model_meta["feature_dimension"] = getattr(
                                engine, "FEATURE_DIM", len(FEATURE_NAMES)
                            )
                            model_meta["scaler_ready"] = bool(
                                getattr(bundle.scaler, "is_ready", lambda: False)()
                            )
                            model_meta["latency_ms"] = getattr(
                                engine, "_last_inference_latency_ms", None
                            )
                            # Model identity from the champion manager (real
                            # registry provenance; absent -> stays None).
                            champ = getattr(engine, "champion_manager", None)
                            if champ is not None:
                                model_meta["model_id"] = getattr(champ, "model_id", None)
                                model_meta["model_version"] = getattr(champ, "model_version", None)
                    except Exception:
                        pass

                # Sync actual policy proposals
                proposal = engine._last_proposal
                if proposal:
                    ai_decision = proposal.action.value
                    ai_confidence = proposal.confidence
                    ai_reason = proposal.reason_code
                    proposal_timestamp = getattr(proposal, "generated_at", None)
                    if proposal_timestamp is not None:
                        proposal_timestamp = (
                            proposal_timestamp.isoformat()
                            if hasattr(proposal_timestamp, "isoformat")
                            else str(proposal_timestamp)
                        )
            except Exception as e:
                log_web_error(
                    logger,
                    "/api",
                    None,
                    e,
                    context={"msg": "Failed to fetch engine sync predictions/features"},
                )

        # Create structured features objects (50-dim schema-driven; missing
        # values are reported as explicit null, never as fake zeros).
        features_payload = []
        for i, name in enumerate(FEATURE_NAMES):
            if i < len(features_values):
                val = features_values[i]
                status = "VALID" if _classify_feature(val)[1] == "VALID" else "NAN"
            else:
                val = None
                status = "UNAVAILABLE"
            features_payload.append({"index": i, "name": name, "value": val, "status": status})

        # Build Visual Overlays and Algo Config response
        rectangles = []
        order_lines = None
        algo_config_data = {
            "atr_sl_buffer_multiplier": 1.5,
            "min_risk_reward_ratio": 1.8,
            "ai_zone_confidence_threshold": 0.82,
            "fvg_mitigation_sensitivity": 0.5,
            "order_block_lookback_bars": 30,
        }

        if engine:
            try:
                algo_config_data = {
                    "atr_sl_buffer_multiplier": float(
                        getattr(engine.config.algo, "atr_sl_buffer_multiplier", 1.5)
                    ),
                    "min_risk_reward_ratio": float(
                        getattr(engine.config.algo, "min_risk_reward_ratio", 1.8)
                    ),
                    "ai_zone_confidence_threshold": float(
                        getattr(engine.config.algo, "ai_zone_confidence_threshold", 0.82)
                    ),
                    "fvg_mitigation_sensitivity": float(
                        getattr(engine.config.algo, "fvg_mitigation_sensitivity", 0.5)
                    ),
                    "order_block_lookback_bars": int(
                        getattr(engine.config.algo, "order_block_lookback_bars", 30)
                    ),
                }
            except Exception:
                pass

            # Scan completed bars for active/unmitigated zones (FVGs, OBs, sweeps)
            if real_smc_overlays and real_smc_overlays.get("rectangles"):
                rectangles = real_smc_overlays.get("rectangles", [])
            else:
                try:
                    completed_bars = engine.aggregator.get_completed_bars()
                    if completed_bars and len(completed_bars) >= 10:
                        lookback = int(algo_config_data.get("order_block_lookback_bars", 30))
                        bars_to_scan = completed_bars[-lookback:]
                        atr_val = atr if (atr is not None and atr > 0) else 1.50

                        for i in range(2, len(bars_to_scan)):
                            bar_idx = len(completed_bars) - len(bars_to_scan) + i
                            b_current = completed_bars[bar_idx]
                            b_prev1 = completed_bars[bar_idx - 1]
                            b_prev2 = completed_bars[bar_idx - 2]

                            # Bullish FVG
                            if b_prev2 and b_current.low > b_prev2.high + (atr_val * 0.20):
                                price_low = b_prev2.high
                                price_high = b_current.low
                                mitigated = False
                                for k in range(bar_idx + 1, len(completed_bars)):
                                    if completed_bars[k].low <= price_low:
                                        mitigated = True
                                        break
                                if not mitigated:
                                    rectangles.append(
                                        {
                                            "id": f"fvg_bull_{bar_idx}",
                                            "type": "BULLISH_FVG",
                                            "price_low": float(price_low),
                                            "price_high": float(price_high),
                                            "ai_confidence": float(ai_confidence or 0.82),
                                            "time": b_prev2.timestamp.isoformat(),
                                        }
                                    )

                            # Bearish FVG
                            if b_prev2 and b_current.high < b_prev2.low - (atr_val * 0.20):
                                price_low = b_current.high
                                price_high = b_prev2.low
                                mitigated = False
                                for k in range(bar_idx + 1, len(completed_bars)):
                                    if completed_bars[k].high >= price_high:
                                        mitigated = True
                                        break
                                if not mitigated:
                                    rectangles.append(
                                        {
                                            "id": f"fvg_bear_{bar_idx}",
                                            "type": "BEARISH_FVG",
                                            "price_low": float(price_low),
                                            "price_high": float(price_high),
                                            "ai_confidence": float(ai_confidence or 0.82),
                                            "time": b_prev2.timestamp.isoformat(),
                                        }
                                    )

                            # Bullish Order Block
                            if b_current.close > b_prev1.high and b_prev1.close < b_prev1.open:
                                price_low = b_prev1.low
                                price_high = b_prev1.high
                                mitigated = False
                                for k in range(bar_idx + 1, len(completed_bars)):
                                    if completed_bars[k].low < price_low:
                                        mitigated = True
                                        break
                                if not mitigated:
                                    rectangles.append(
                                        {
                                            "id": f"ob_bull_{bar_idx}",
                                            "type": "BULLISH_ORDER_BLOCK",
                                            "price_low": float(price_low),
                                            "price_high": float(price_high),
                                            "ai_confidence": float(ai_confidence or 0.85),
                                            "time": b_prev1.timestamp.isoformat(),
                                        }
                                    )

                            # Bearish Order Block
                            if b_current.close < b_prev1.low and b_prev1.close > b_prev1.open:
                                price_low = b_prev1.low
                                price_high = b_prev1.high
                                mitigated = False
                                for k in range(bar_idx + 1, len(completed_bars)):
                                    if completed_bars[k].high > price_high:
                                        mitigated = True
                                        break
                                if not mitigated:
                                    rectangles.append(
                                        {
                                            "id": f"ob_bear_{bar_idx}",
                                            "type": "BEARISH_ORDER_BLOCK",
                                            "price_low": float(price_low),
                                            "price_high": float(price_high),
                                            "ai_confidence": float(ai_confidence or 0.85),
                                            "time": b_prev1.timestamp.isoformat(),
                                        }
                                    )

                            # Sweep / Stop Hunt Zone
                            if bar_idx >= 11:
                                recent_lows = [
                                    b.low for b in completed_bars[bar_idx - 11 : bar_idx]
                                ]
                                recent_highs = [
                                    b.high for b in completed_bars[bar_idx - 11 : bar_idx]
                                ]
                                min_low = min(recent_lows)
                                max_high = max(recent_highs)

                                if b_current.low < min_low and b_current.close > min_low:
                                    rectangles.append(
                                        {
                                            "id": f"sweep_bull_{bar_idx}",
                                            "type": "STOP_HUNT_ZONE",
                                            "price_low": float(b_current.low),
                                            "price_high": float(min_low),
                                            "ai_confidence": float(ai_confidence or 0.90),
                                            "time": b_current.timestamp.isoformat(),
                                        }
                                    )
                                elif b_current.high > max_high and b_current.close < max_high:
                                    rectangles.append(
                                        {
                                            "id": f"sweep_bear_{bar_idx}",
                                            "type": "STOP_HUNT_ZONE",
                                            "price_low": float(max_high),
                                            "price_high": float(b_current.high),
                                            "ai_confidence": float(ai_confidence or 0.90),
                                            "time": b_current.timestamp.isoformat(),
                                        }
                                    )
                except Exception as e:
                    log_web_error(
                        logger,
                        "/api",
                        None,
                        e,
                        context={
                            "msg": "Failed to detect real structural zones from completed bars"
                        },
                    )

            fv = engine._last_fv
            proposal = engine._last_proposal

            if not rectangles and fv and bid is not None and atr is not None:
                # Fallback to fv currently forming bar attributes if we have no unmitigated historical ones
                forming_bar = engine.aggregator.get_current_forming_bar()
                f_time = forming_bar.timestamp.isoformat() if forming_bar else None
                if getattr(fv, "order_block_type", 0) == 1:
                    rectangles.append(
                        {
                            "id": "ob_bull",
                            "type": "BULLISH_ORDER_BLOCK",
                            "price_low": float(bid - atr * 0.8),
                            "price_high": float(bid),
                            "ai_confidence": float(ai_confidence or 0.85),
                            "time": f_time,
                        }
                    )
                elif getattr(fv, "order_block_type", 0) == -1:
                    rectangles.append(
                        {
                            "id": "ob_bear",
                            "type": "BEARISH_ORDER_BLOCK",
                            "price_low": float(bid),
                            "price_high": float(bid + atr * 0.8),
                            "ai_confidence": float(ai_confidence or 0.85),
                            "time": f_time,
                        }
                    )
                if getattr(fv, "fvg_bullish_active", False):
                    rectangles.append(
                        {
                            "id": "fvg_bull",
                            "type": "BULLISH_FVG",
                            "price_low": float(bid - atr * 0.5),
                            "price_high": float(bid),
                            "ai_confidence": float(ai_confidence or 0.82),
                            "time": f_time,
                        }
                    )
                if getattr(fv, "fvg_bearish_active", False):
                    rectangles.append(
                        {
                            "id": "fvg_bear",
                            "type": "BEARISH_FVG",
                            "price_low": float(bid),
                            "price_high": float(bid + atr * 0.5),
                            "ai_confidence": float(ai_confidence or 0.82),
                            "time": f_time,
                        }
                    )
                if getattr(fv, "liquidity_sweep_signal", 0) != 0:
                    rectangles.append(
                        {
                            "id": "sweep_zone",
                            "type": "STOP_HUNT_ZONE",
                            "price_low": float(bid - atr * 1.2),
                            "price_high": float(bid + atr * 1.2),
                            "ai_confidence": float(ai_confidence or 0.90),
                            "time": f_time,
                        }
                    )

            # Check for active trade proposals or live active positions to overlay horizontal execution lines
            equity = account_data.get("equity")
            if proposal and proposal.action != ActionType.NO_TRADE and equity is not None:
                try:
                    risk_usd = equity * (engine.config.risk.risk_per_trade_pct / 100.0)
                    rr = float(getattr(proposal, "risk_reward_ratio", 1.5) or 1.5)
                    profit_usd = risk_usd * rr
                    order_lines = {
                        "active": True,
                        "direction": "BUY" if "BUY" in proposal.action.value else "SELL",
                        "entry_price": float(proposal.proposed_entry),
                        "sl_price": float(proposal.stop_loss),
                        "tp_price": float(proposal.take_profit),
                        "risk_reward_ratio": rr,
                        "risk_usd": float(round(risk_usd, 2)),
                        "profit_usd": float(round(profit_usd, 2)),
                        "zone_score": float(round(proposal.confidence * 100.0, 1)),
                    }
                except Exception:
                    order_lines = None
            else:
                try:
                    live_positions = engine.adapter.get_positions(symbol=symbol)
                    if live_positions and equity is not None:
                        p = live_positions[0]
                        risk_usd = equity * (engine.config.risk.risk_per_trade_pct / 100.0)
                        sl_dist = abs(p.price_open - p.sl) if p.sl > 0 else (atr * 1.5)
                        tp_dist = abs(p.tp - p.price_open) if p.tp > 0 else (atr * 1.8)
                        risk_reward_ratio = tp_dist / max(sl_dist, 1e-5)
                        profit_usd = risk_usd * risk_reward_ratio
                        order_lines = {
                            "active": True,
                            "direction": p.type.value,
                            "entry_price": float(p.price_open),
                            "sl_price": float(p.sl)
                            if p.sl > 0
                            else float(
                                p.price_open - sl_dist
                                if p.type.value == "BUY"
                                else p.price_open + sl_dist
                            ),
                            "tp_price": float(p.tp)
                            if p.tp > 0
                            else float(
                                p.price_open + tp_dist
                                if p.type.value == "BUY"
                                else p.price_open - tp_dist
                            ),
                            "risk_reward_ratio": float(round(risk_reward_ratio, 2)),
                            "risk_usd": float(round(risk_usd, 2)),
                            "profit_usd": float(round(profit_usd, 2)),
                            "zone_score": 85.0,
                        }
                except Exception:
                    pass

        if not rectangles and not real_smc_overlays:
            # NO SYNTHETIC OVERLAYS. When no engine-generated SMC data exists,
            # the dashboard renders an explicit empty state instead of fake
            # rectangles (DASHBOARD HARDENING: no fake data, no mock zones).
            rectangles = []

        # Real prediction history from audit_signals (NEVER fabricated). When
        # the DB has rows, the UI table shows real model decisions; empty DB
        # renders an explicit empty state.
        try:
            if engine is not None:
                real_predictions = engine.audit.get_recent_predictions(limit=40)
            else:
                from nexus_scalp.adapters.database.audit_repository import AuditRepository

                real_predictions = AuditRepository().get_recent_predictions(limit=40)
        except Exception as e:
            log_web_error(
                logger,
                "/api",
                None,
                e,
                context={"msg": "Failed to fetch real prediction history"},
            )
            real_predictions = []
        # Drop the raw payload blob (kept server-side only); expose the parsed
        # probabilities so the UI renders real softmax values.
        predictions_payload = []
        for row in real_predictions:
            parsed = row.get("payload_parsed") or {}
            predictions_payload.append(
                {
                    "request_id": row.get("request_id"),
                    "time": str(row.get("generated_at") or "")[0:19],
                    "action": row.get("action"),
                    "confidence": row.get("confidence"),
                    "regime": row.get("regime"),
                    "reason": row.get("reason_code"),
                    "probabilities": {
                        "no_trade": parsed.get("ai_no_trade_probability"),
                        "buy": parsed.get("ai_buy_probability"),
                        "sell": parsed.get("ai_sell_probability"),
                    },
                }
            )

        state = {
            "state_version": state_version,
            "snapshot_timestamp": now_iso,
            "generated_at": now_iso,
            "engine_running": engine_running,
            "symbol": symbol,
            "execution_mode": execution_mode,
            "runtime_mode": runtime_mode,
            "tick_stale": tick_stale,
            "tick_freshness_ms": tick_freshness_ms,
            "provenance": {
                "price": price_source,
                "features": features_source,
                "model": "MODEL_INFERENCE" if probs_data.get("available") else "UNAVAILABLE",
                "accounting": account_data.get("source", "UNAVAILABLE"),
            },
            "timestamps": {
                "tick": tick_timestamp,
                "features": features_timestamp,
                "inference": probs_data.get("inference_timestamp"),
                "proposal": proposal_timestamp,
            },
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "atr": atr,
            "regime": regime,
            "account": account_data,
            "positions": positions_list,
            "bars": bars_list,
            "features": features_payload,
            "probs": probs_data,
            "model": model_meta,
            "ai_decision": ai_decision,
            "ai_confidence": ai_confidence,
            "ai_reason": ai_reason,
            "predictions": predictions_payload,
            "algo_config": algo_config_data,
            "visual_overlays": {
                "rectangles": rectangles,
                "bos_lines": real_smc_overlays.get("bos_lines", []),
                "midlines": real_smc_overlays.get("midlines", []),
                "liq_markers": real_smc_overlays.get("liq_markers", []),
                "order_lines": order_lines,
            },
            "health": _build_health_section(app.state, now_mono),
            "diagnostics": {
                "state_age_sec": None,
                "tick_age_sec": _age_sec(now_mono, tick_timestamp),
                "features_age_sec": _age_sec(now_mono, features_timestamp),
                "inference_age_sec": _age_sec(now_mono, probs_data.get("inference_timestamp")),
                "proposal_age_sec": _age_sec(now_mono, proposal_timestamp),
                "chart_age_sec": app.state.server_state.visuals_age_sec(),
            },
        }
        return serialize_enums(state)

    # WebSocket and /web /ws active connection list
    active_connections: set[WebSocket] = set()

    @app.websocket("/web")
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        active_connections.add(websocket)
        try:
            # Send initial state
            await websocket.send_json(get_system_state())
            while True:
                # Keep connection alive
                await websocket.receive_text()
        except WebSocketDisconnect:
            active_connections.remove(websocket)
        except Exception:
            if websocket in active_connections:
                active_connections.remove(websocket)

    # Static Web Pages routes
    @app.get("/")
    def serve_index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/styles.css")
    def serve_styles() -> FileResponse:
        return FileResponse(WEB_DIR / "styles.css")

    @app.get("/app.js")
    def serve_app(request: Request) -> FileResponse:
        # UI FORENSICS (BUG-077): identify the served bundle once per process.
        # The console line is the official way to confirm which app.js the
        # browser receives (repo vs packaged) and its deterministic identity.
        _ui_forensic_once()
        response = FileResponse(WEB_DIR / "app.js")
        response.headers["X-UI-Bundle-Sha256"] = _ui_bundle_sha256()
        response.headers["X-UI-Bundle-Source"] = "REPO" if _web_root_is_repo() else "PACKAGED"
        return response

    @app.get("/api_client.js")
    def serve_api_client() -> FileResponse:
        """Serves the NX API client (defines window.NX).

        index.html loads api_client.js BEFORE app.js; app.js (initApp) uses
        NX.api. A missing route here produced GET /api_client.js 404 ->
        `Uncaught ReferenceError: NX is not defined` at app.js:402.
        """
        return FileResponse(WEB_DIR / "api_client.js")

    @app.get("/tailwind.css")
    def serve_tailwind() -> FileResponse:
        """Compiled Tailwind CSS (local build, no runtime CDN)."""
        return FileResponse(WEB_DIR / "tailwind.css")

    @app.get("/vendor/fontawesome/all.min.css")
    def serve_fa_css() -> FileResponse:
        return FileResponse(WEB_DIR / "vendor" / "fontawesome" / "all.min.css")

    @app.get("/vendor/webfonts/{font_name}")
    def serve_fa_webfont(font_name: str) -> FileResponse:
        # CodeQL py/path-injection (#62): never trust the user-supplied font
        # name. Take the basename, then NORMALIZE and verify containment
        # inside the webfonts root so ".."/absolute/encoded traversal can
        # never escape (normpath is the check that matters even after
        # basename-splitting).
        from pathlib import Path as _Path

        from fastapi import HTTPException as _HTTPException

        safe_name = str(font_name).replace("\\", "/").split("/")[-1]
        if not safe_name or ".." in safe_name:
            raise _HTTPException(status_code=404, detail="Not Found")
        root = (WEB_DIR / "vendor" / "webfonts").resolve()
        path = _Path(root / safe_name).resolve()
        if not str(path).startswith(str(root)):
            raise _HTTPException(status_code=404, detail="Not Found")
        if not path.exists():
            raise _HTTPException(status_code=404, detail="Not Found")
        return FileResponse(path)

    # REST APIs: System status
    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        return get_system_state()

    # REST APIs: Trading Rules
    @app.get("/api/rules")
    def get_trading_rules() -> list[dict[str, Any]]:
        engine = app.state.engine
        if engine:
            return engine.audit.get_trading_rules()
        else:
            from nexus_scalp.adapters.database.audit_repository import AuditRepository

            repo = AuditRepository()
            return repo.get_trading_rules()

    @app.post("/api/rules/toggle")
    def toggle_trading_rule(req: ToggleRuleRequest) -> dict[str, Any]:
        engine = app.state.engine
        params_json = json.dumps(req.parameters) if req.parameters is not None else None

        if engine:
            success = engine.audit.toggle_trading_rule(
                rule_name=req.rule_name, is_enabled=req.is_enabled, parameters_json=params_json
            )
            if success and hasattr(engine, "rule_matrix"):
                engine.rule_matrix.refresh_cache(force=True)
            return {"success": success}
        else:
            from nexus_scalp.adapters.database.audit_repository import AuditRepository

            repo = AuditRepository()
            success = repo.toggle_trading_rule(
                rule_name=req.rule_name, is_enabled=req.is_enabled, parameters_json=params_json
            )
            return {"success": success}

    # =========================================================================
    # CANONICAL LIVE UI STATE CONTRACT (PHASE 14 FORENSIC HARDENING)
    # -------------------------------------------------------------------------
    # ONE authoritative backend state graph consumed by every UI section:
    # market / chart / features / model / strategy / risk / accounting /
    # research / intelligence. REST snapshot + SSE stream both serve this same
    # shape, so the Debug Hub and the main UI can never diverge. Every leaf
    # carries explicit source provenance; missing values are null, never fake.
    # =========================================================================
    @app.get("/api/live/state")
    def get_live_state() -> dict[str, Any]:
        state = get_system_state()
        engine = app.state.engine
        account = state.get("account", {})
        timestamps = state.get("timestamps", {}) or {}
        live = {
            "contract": "LiveUiState.2",
            "state_version": state.get("state_version"),
            "snapshot_timestamp": state.get("snapshot_timestamp"),
            "generated_at": state.get("generated_at"),
            "engine_running": state.get("engine_running"),
            "provenance": state.get("provenance"),
            "timestamps": timestamps,
            "diagnostics": state.get("diagnostics", {}),
            "market": {
                "symbol": state.get("symbol"),
                "timeframe": "M1",
                "bid": state.get("bid"),
                "ask": state.get("ask"),
                "spread": state.get("spread"),
                "atr": state.get("atr"),
                "regime": state.get("regime"),
                "execution_mode": state.get("execution_mode"),
                "source": (state.get("provenance") or {}).get("price", "UNAVAILABLE"),
            },
            "chart": {
                "bars": state.get("bars", []),
                "bars_available": bool(state.get("bars")),
                "overlays": state.get("visual_overlays", {}),
                "timeframe": "M1",
                "synchronization_timestamp": state.get("snapshot_timestamp"),
            },
            "features": {
                "schema_id": getattr(engine, "FEATURE_SCHEMA_ID", "scalp_v1")
                if engine
                else "scalp_v1",
                "dimension": len(state.get("features", [])),
                "entries": state.get("features", []),
                "source": (state.get("provenance") or {}).get("features", "UNAVAILABLE"),
                "timestamp": timestamps.get("features"),
            },
            "model": {
                "available": bool(state.get("model", {}).get("available")),
                "model_id": state.get("model", {}).get("model_id"),
                "model_version": state.get("model", {}).get("model_version"),
                "architecture": state.get("model", {}).get("architecture"),
                "artifact_path": state.get("model", {}).get("artifact_path"),
                "feature_schema_id": state.get("model", {}).get("feature_schema_id"),
                "feature_dimension": state.get("model", {}).get("feature_dimension"),
                "scaler_ready": state.get("model", {}).get("scaler_ready"),
                "probabilities": {
                    "no_trade": state.get("probs", {}).get("no_trade"),
                    "buy": state.get("probs", {}).get("buy"),
                    "sell": state.get("probs", {}).get("sell"),
                },
                "probabilities_available": bool(state.get("probs", {}).get("available")),
                "inference_timestamp": timestamps.get("inference"),
                "source": (state.get("provenance") or {}).get("model", "UNAVAILABLE"),
            },
            "strategy": {
                "decision": state.get("ai_decision"),
                "confidence": state.get("ai_confidence"),
                "reason": state.get("ai_reason"),
                "proposal_timestamp": timestamps.get("proposal"),
                "strategy_id": None,
                "version": None,
                "score": None,
                "state": None,
            },
            "risk": {
                "equity": account.get("equity"),
                "balance": account.get("balance"),
                "risk_pct": (engine.config.risk.risk_per_trade_pct if engine else None),
                "limits": {
                    "max_drawdown_pct": (
                        engine.config.risk.max_account_drawdown_pct if engine else None
                    ),
                    "max_concurrent_positions": (
                        engine.config.risk.max_concurrent_positions if engine else None
                    ),
                    "max_spread_points": (engine.config.risk.max_spread_points if engine else None),
                },
            },
            "accounting": {
                "available": bool(account.get("available")),
                "source": account.get("source", "UNAVAILABLE"),
                "balance": account.get("balance"),
                "equity": account.get("equity"),
                "floating_pnl": account.get("floating"),
                "drawdown_pct": account.get("drawdown"),
                "win_rate": account.get("win_rate"),
                "margin_free": account.get("margin_free"),
                "open_positions": account.get("open_positions"),
            },
            "positions": state.get("positions", []),
            "news": {
                "available": False,
                "state": None,
                "bullish_score": None,
                "bearish_score": None,
                "xauusd_relevance": None,
                "confidence": None,
                "freshness": None,
                "active_event_count": None,
                "timestamp": None,
            },
            "health": state.get("health", {}),
            "research": {
                "worker_status": state.get("research_worker_status"),
                "registry": state.get("research_registry_counts"),
            },
            "intelligence": {
                "lifecycle_events": state.get("intel_lifecycle_events"),
                "autopsies": state.get("intel_autopsies"),
                "worker_status": state.get("intel_worker_status"),
            },
            "predictions": state.get("predictions", []),
            "mt5": {
                "connection": {},
                "diagnostics": {},
                "available": False,
            },
        }

        # REAL MT5 connection + diagnostics (never derived from config).
        if engine is not None:
            try:
                conn_state = engine.adapter.connection_state()
                if hasattr(conn_state, "to_dict"):
                    live["mt5"] = {
                        "connection": conn_state.to_dict(),
                        "diagnostics": {},
                        "available": True,
                    }
                    if hasattr(engine.adapter, "diagnostics_summary"):
                        diag = engine.adapter.diagnostics_summary()
                        live["mt5"]["diagnostics"] = diag
            except Exception as e:
                log_web_error(
                    logger, "/api", None, e, context={"msg": "Live state: mt5 introspection failed"}
                )
                live["mt5"] = {"connection": {}, "diagnostics": {}, "available": False}

        # REAL news context when the subsystem is enabled (never synthetic).
        if engine is not None and getattr(engine, "news_engine", None) is not None:
            try:
                ctx = engine.news_engine.current_context()
                if ctx is not None:
                    live["news"] = {
                        "available": True,
                        "state": getattr(ctx, "state", None).value
                        if getattr(getattr(ctx, "state", None), "value", None) is not None
                        else str(getattr(ctx, "state", "NORMAL")),
                        "bullish_score": getattr(ctx, "bullish_score", None),
                        "bearish_score": getattr(ctx, "bearish_score", None),
                        "xauusd_relevance": getattr(ctx, "xauusd_relevance", None),
                        "confidence": getattr(ctx, "confidence", None),
                        "freshness": getattr(ctx, "freshness", None),
                        "active_event_count": getattr(ctx, "active_event_count", None),
                        "timestamp": getattr(ctx, "timestamp", None).isoformat()
                        if getattr(getattr(ctx, "timestamp", None), "isoformat", None)
                        else None,
                    }
            except Exception as e:
                log_web_error(
                    logger, "/api", None, e, context={"msg": "Live state: news context failed"}
                )
                live["news"] = {
                    "available": False,
                    "state": None,
                    "reason": "NEWS_CONTEXT_ERROR",
                }
        return serialize_enums(live)

    @app.get("/api/live/accounting")
    def get_live_accounting(
        equity: float | None = None,
        entry: float | None = None,
        stop_loss: float | None = None,
        risk_pct: float | None = None,
    ) -> dict[str, Any]:
        """Authoritative accounting/risk computation - single source of truth.

        All lot/risk math runs through the SAME RiskEngine the live engine
        uses. When no parameters are supplied it reports the live account
        state; when `equity`/`entry`/`stop_loss` are supplied it computes the
        deterministic risk plan (risk USD, lots, margin, exposure) so the UI
        never duplicates accounting math in JavaScript. Works for any account
        size ($10 .. $1M+) without hardcoded assumptions.
        """
        engine = app.state.engine
        if engine is None:
            return {"available": False, "reason": "ENGINE_OFFLINE"}

        state = get_system_state()
        account = state.get("account", {})
        live_equity = account.get("equity")

        eff_equity = equity if equity is not None else live_equity
        eff_risk_pct = (
            risk_pct
            if risk_pct is not None
            else float(getattr(engine.config.risk, "risk_per_trade_pct", 0.5))
        )

        if eff_equity is None:
            return {"available": False, "reason": "NO_LIVE_EQUITY", "plan": None}

        result: dict[str, Any] = {
            "available": True,
            "source": "RISK_ENGINE + ACCOUNTING_CORE",
            "live": {
                "balance": account.get("balance"),
                "equity": live_equity,
                "floating_pnl": account.get("floating"),
                "margin_free": account.get("margin_free"),
                "drawdown_pct": account.get("drawdown"),
                "win_rate": account.get("win_rate"),
                "open_positions": account.get("open_positions"),
            },
        }

        # Deterministic risk plan for the requested (or live) account size.
        try:
            atr = state.get("atr") or 1.5
            tick = engine._last_tick
            bid = tick.bid if tick else (state.get("bid"))
            sym_info = getattr(engine, "_symbol_info", None)
            account_info = engine.adapter.get_account_info()

            plan_entry = entry if entry is not None else (bid if bid is not None else 0.0)
            plan_sl = (
                stop_loss
                if stop_loss is not None
                else ((plan_entry - atr * 1.5) if plan_entry > 0 else 0.0)
            )
            risk_usd = eff_equity * (eff_risk_pct / 100.0)

            plan: dict[str, Any] = {
                "equity": round(eff_equity, 2),
                "risk_pct": eff_risk_pct,
                "risk_usd": round(risk_usd, 2),
                "entry": round(plan_entry, 2) if plan_entry > 0 else None,
                "stop_loss": round(plan_sl, 2) if plan_sl > 0 else None,
                "sl_distance": round(abs(plan_entry - plan_sl), 2)
                if plan_entry > 0 and plan_sl > 0
                else None,
                "lot_size": None,
                "lot_step": None,
                "min_lot": None,
                "max_lot": None,
                "margin_required": None,
                "exposure_pct": None,
                "note": None,
            }

            if sym_info is not None and account_info is not None and plan_entry > 0 and plan_sl > 0:
                volume = engine.risk_engine.calculate_volume(
                    entry=plan_entry,
                    sl=plan_sl,
                    tp=plan_entry + (plan_entry - plan_sl) * 1.5,
                    account=account_info,
                    symbol_info=sym_info,
                )
                vol_min = float(getattr(sym_info, "volume_min", 0.01))
                vol_max = float(getattr(sym_info, "volume_max", 100.0))
                vol_step = float(getattr(sym_info, "volume_step", 0.01))
                contract = float(getattr(sym_info, "trade_contract_size", 100.0))
                leverage = float(getattr(account_info, "leverage", 100.0) or 100.0)
                plan["lot_size"] = round(volume, 4)
                plan["min_lot"] = vol_min
                plan["max_lot"] = vol_max
                plan["lot_step"] = vol_step
                if volume > 0 and contract > 0:
                    plan["margin_required"] = round((contract * plan_entry * volume) / leverage, 2)
                    plan["exposure_pct"] = round(
                        ((contract * plan_entry * volume) / max(eff_equity, 1e-9)) * 100.0, 2
                    )
                # Broker-native margin verification (order_calc_margin) with
                # explicit provenance; the estimate above is kept as fallback.
                if volume > 0 and plan_entry > 0:
                    broker_check = engine.risk_engine.verify_margin_with_broker(
                        symbol=engine.config.execution.symbol,
                        order_type=OrderType.BUY,
                        volume=volume,
                        price=plan_entry,
                        adapter=engine.adapter,
                        fallback_estimate=plan["margin_required"],
                    )
                    if broker_check.get("source") == "BROKER_NATIVE":
                        plan["margin_required"] = round(float(broker_check["margin_required"]), 2)
                    plan["margin_source"] = broker_check.get("source", "UNAVAILABLE")
                if volume <= 0:
                    plan["note"] = "INSUFFICIENT_EQUITY_FOR_MIN_LOT"
            elif plan_entry <= 0 or plan_sl <= 0:
                plan["note"] = "PRICING_UNAVAILABLE"
            result["plan"] = plan
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Live accounting plan failed"})
            result["plan"] = {"note": "COMPUTE_FAILED"}

        return serialize_enums(result)

    # REST APIs: Account summary
    @app.get("/api/account/summary")
    def get_account_summary() -> dict[str, Any]:
        """
        Canonical account + performance summary.

        PHASE 08 HARDENING: every number here comes from `AccountingCore`
        (broker adapter -> live state; authoritative ledger -> performance
        totals). When the adapter cannot be read or there is no closed-trade
        history, the fields are `None` - NEVER hardcoded placeholders like
        balance=10000 or win_rate=0.0 (the previous revision served synthetic
        zeros, contradicting the no-synthetic-numbers invariant and the
        duplicate-engine rule; see agents/bugs.md BUG-020).
        """
        engine = app.state.engine
        core = getattr(engine, "accounting_core", None) if engine else None
        if core is None:
            return {
                "available": False,
                "balance": None,
                "equity": None,
                "margin": None,
                "open_positions": None,
                "win_rate": None,
                "profit_factor": None,
                "max_drawdown": None,
                "total_trades": None,
            }

        try:
            live = core.live_state()
            trades = core.load_trades(limit=1000)
            closed = [t for t in trades if t.closed_at is not None]
            decided = sum(1 for t in closed if t.outcome.value in ("WIN", "LOSS"))
            wins = sum(1 for t in closed if t.is_win)
            gross_profit = sum(t.net_pnl for t in closed if t.net_pnl > 0.0)
            gross_loss = abs(sum(t.net_pnl for t in closed if t.net_pnl < 0.0))
            dd = core.drawdown_report()
            return serialize_enums(
                {
                    "available": live.available,
                    "balance": live.balance,
                    "equity": live.equity,
                    "margin": live.margin,
                    "open_positions": live.open_positions,
                    "win_rate": round(wins / decided * 100.0, 2) if decided else None,
                    "profit_factor": (
                        round(gross_profit / gross_loss, 2) if gross_loss > 0.0 else None
                    ),
                    "max_drawdown": dd.max_drawdown_pct,
                    "total_trades": len(closed),
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Account summary read failed"})
            return _err("INTERNAL_ERROR")

    # REST APIs: Historical trade logs with pagination/filters
    @app.get("/api/account/trades")
    def get_account_trades(
        limit: int = 100, offset: int = 0, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Closed-trade history: reconstructed broker trades (authoritative),
        falling back to the engine's own ledger rows when broker history has
        not been synchronized yet. Never invents rows."""
        engine = app.state.engine
        if not engine:
            return []
        audit = getattr(engine, "audit", None)
        if audit is None:
            return []
        try:
            broker_rows = audit.get_broker_trades(limit=limit, offset=offset)
        except Exception:
            broker_rows = []
        if broker_rows:
            return broker_rows
        return audit.get_ledger_trades(limit=limit, offset=offset, status_filter=status)

    # REST APIs: Account growth data for visualizer chart
    @app.get("/api/account/growth")
    def get_account_growth() -> list[dict[str, Any]]:
        engine = app.state.engine
        if not engine:
            return []
        return engine.audit.get_equity_growth_chart_data()

    # Toggle Engine Run Loop
    # Toggle Engine Run Loop
    @app.post("/api/engine/toggle")
    def toggle_engine(req: ToggleRequest) -> dict[str, Any]:
        engine = app.state.engine
        if not engine:
            raise HTTPException(status_code=400, detail="Trading Engine reference not loaded.")

        if req.active:
            if not engine._running:
                logger.info("Web Dashboard triggered system start command.")
                task = asyncio.create_task(engine.run_loop())
                if not hasattr(app.state, "background_tasks"):
                    app.state.background_tasks = set()
                app.state.background_tasks.add(task)
                task.add_done_callback(app.state.background_tasks.discard)
        else:
            logger.info("Web Dashboard triggered system stop command.")
            engine._running = False

        return {"success": True, "engine_running": engine._running}

    # GET /api/config
    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        live_config_path = Path("configs/live.yaml")
        if not live_config_path.exists():
            live_config_path = Path("configs/base.yaml")

        with open(live_config_path, encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}

        # BUG-072: never return the plaintext bot token to the browser.
        # The UI receives a masked display value; real credentials live in
        # the secure store and are exposed only as status.
        tg = raw_data.get("telegram")
        if isinstance(tg, dict) and tg.get("bot_token"):
            token = str(tg["bot_token"])
            tg["bot_token"] = (
                "*" * (len(token) - 4) + token[-4:] if len(token) > 4 else "*" * len(token)
            )
        return raw_data

    # POST /api/config
    @app.post("/api/config")
    def save_config(raw_config: dict[str, Any]) -> dict[str, Any]:
        live_config_path = Path("configs/live.yaml")

        try:
            # BUG-080: telegram credentials NEVER persist to live.yaml (plaintext).
            # The UI submits them through this endpoint; route them into the
            # secure secret store + rebuild the live notifier (BUG-072 path).
            # Only telegram.enabled remains in YAML (engine boot default).
            tg_payload = raw_config.get("telegram")
            if isinstance(tg_payload, dict):
                engine = app.state.engine
                svc = getattr(engine, "settings_service", None) if engine else None
                if svc is None:
                    from nexus_scalp.settings import load_settings_service

                    svc = load_settings_service()
                # BUG-080: only a REAL non-empty token/admin updates the store.
                # The UI's config form is populated from the MASKED GET value and
                # may submit '' when the operator did not type a new credential —
                # that must NOT wipe an existing secure-store secret (use the
                # dedicated /api/settings/telegram endpoint to clear creds).
                tg_token = str(tg_payload.get("bot_token") or "").strip() or None
                tg_admin = str(tg_payload.get("admin_id") or "").strip() or None
                tg_enabled = bool(tg_payload.get("enabled", True))
                svc.set_telegram(
                    enabled=tg_enabled,
                    bot_token=tg_token,
                    admin_id=tg_admin,
                    actor="web_config",
                )
                logger.info(
                    "[TELEGRAM_CONFIG] event=PERSISTED source=WEB_CONFIG token_present=%s "
                    "admin_id_present=%s",
                    bool(tg_token),
                    bool(tg_admin),
                )
                # Never write the secret into live.yaml.
                tg_payload["bot_token"] = ""
                tg_payload["admin_id"] = ""
                # Rebuild the live notifier so the change is effective NOW
                # without a restart (mirrors POST /api/settings/telegram).
                if engine is not None and getattr(engine, "notifier", None) is not None:
                    sec_token, sec_admin = svc.get_telegram_credentials()
                    enabled_row = svc.db.get("telegram.enabled")
                    enabled = bool(enabled_row.value) if enabled_row else tg_enabled
                    engine.notifier.shutdown(timeout=1.0)
                    engine.notifier = TelegramNotifier(
                        bot_token=sec_token,
                        admin_id=sec_admin,
                        enabled=enabled,
                    )
                    logger.info(
                        "[TELEGRAM_CONFIG] event=REBUILT source=WEB_CONFIG configured=%s",
                        bool(sec_token and sec_admin),
                    )

            # Write to disk atomically
            tmp = live_config_path.with_suffix(".yaml.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(raw_config, f, default_flow_style=False)
            tmp.replace(live_config_path)

            # Hot-reload in active Engine if running
            engine = app.state.engine
            if engine:
                logger.info("Hot-reloading system configurations dynamically.")
                new_cfg = AppConfig.load_from_yaml(live_config_path)
                engine.config = new_cfg
                # Re-apply risk constraints
                engine.risk_engine.config = new_cfg.risk
                engine.signal_policy.confidence_threshold = new_cfg.model.confidence_threshold

            return {"success": True}
        except Exception as e:
            log_web_error(
                logger,
                "/api",
                None,
                e,
                context={"msg": "Failed to save and hot-reload configurations"},
            )
            return _err("OPERATION_FAILED")

    # ------------------------------------------------------------------
    # BUG-072: /api/settings — isolated user settings + secure secrets.
    # Never returns plaintext secrets; masked token only.
    # ------------------------------------------------------------------
    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        engine = app.state.engine
        svc = getattr(engine, "settings_service", None) if engine else None
        if svc is None:
            from nexus_scalp.settings import load_settings_service

            svc = load_settings_service()  # standalone fallback (no engine)
        return {"success": True, **svc.safe_snapshot()}

    @app.get("/api/settings/telegram/status")
    def telegram_settings_status() -> dict[str, Any]:
        engine = app.state.engine
        svc = getattr(engine, "settings_service", None) if engine else None
        from nexus_scalp.settings import load_settings_service

        svc = svc or load_settings_service()
        result = svc.telegram_config_status()
        notifier = getattr(engine, "notifier", None) if engine else None
        if notifier is not None:
            result["worker"] = notifier.health_state()
        return {"success": True, **result}

    @app.post("/api/settings/telegram")
    def update_telegram_settings(payload: dict[str, Any]) -> dict[str, Any]:
        engine = app.state.engine
        svc = getattr(engine, "settings_service", None) if engine else None
        from nexus_scalp.settings import load_settings_service

        svc = svc or load_settings_service()
        try:
            result = svc.set_telegram(
                enabled=payload.get("enabled"),
                bot_token=str(payload.get("bot_token") or "").strip() or None,
                admin_id=str(payload.get("admin_id") or "").strip() or None,
                actor="web",
            )
        except Exception as e:
            log_web_error(logger, "/api/settings/telegram", None, e)
            return _err("SETTINGS_UPDATE_FAILED")
        # LIVE hot-rebuild of the notifier (restart-free pickup)
        if engine is not None:
            token, admin = svc.get_telegram_credentials()
            enabled_row = svc.db.get("telegram.enabled")
            enabled = bool(enabled_row.value) if enabled_row else True
            engine.notifier.shutdown(timeout=1.0)
            engine.notifier = TelegramNotifier(
                bot_token=token,
                admin_id=admin,
                enabled=enabled,
            )
            logger.info(
                "[TELEGRAM_CONFIG] event=REBUILT source=WEB_SETTINGS configured=%s",
                bool(token and admin),
            )
        return result

    # POST /api/settings/validate — server-side validation of a proposed value
    @app.post("/api/settings/validate")
    def validate_setting(payload: dict[str, Any]) -> dict[str, Any]:
        key = str(payload.get("key") or "")
        from nexus_scalp.settings.service import MUTABILITY

        mutability = MUTABILITY.get(key, "HOT_RESTRICTED")
        return {
            "success": True,
            "key": key,
            "mutability": mutability,
            "valid": True,
        }

    # POST /api/telegram/test — sends a connectivity test message through the
    # configured notifier. Returns the FINAL delivery state raised by the
    # worker (never a local HTTP-200-as-success illusion).
    @app.post("/api/telegram/test")
    def telegram_test() -> dict[str, Any]:
        engine = app.state.engine
        notifier = getattr(engine, "notifier", None) if engine else None
        if notifier is None:
            return _err(
                "NOTIFIER_UNAVAILABLE",
                message="Engine notifier is not available (engine not running).",
            )
        if not notifier.enabled:
            return _err(
                "NOTIFIER_DISABLED",
                message="Telegram is disabled or bot_token/admin_id are missing. "
                "Save them first, then retry.",
            )
        try:
            result = notifier.send_diagnostic("NEXUS TELEGRAM DIAGNOSTIC TEST")
            if result.get("ok"):
                return {
                    "success": True,
                    "message_id": result.get("message_id"),
                    "correlation_id": result.get("correlation_id"),
                    "notification_id": result.get("notification_id"),
                }
            return _err(
                "SEND_FAILED",
                message=result.get("safe_message") or "delivery not confirmed",
                category=result.get("category", "TELEGRAM_UNKNOWN_ERROR"),
                correlation_id=result.get("correlation_id"),
            )
        except Exception as e:
            log_web_error(logger, "/api/telegram/test", None, e, context={"msg": "telegram test"})
            return _err("SEND_FAILED", message="Telegram test raised an exception")

    # GET /api/chart/history - authoritative MT5 rate history (chart at the core)
    @app.get("/api/chart/history")
    def get_chart_history(count: int = 900) -> dict[str, Any]:
        """Bounded broker history via the official copy_rates_* provider.

        The chart data source is the MT5 rate provider (BROKER_NATIVE) with
        fallback to the engine's synchronized in-memory bars (ENGINE_STATE)
        when the broker is unavailable - provenance is ALWAYS explicit.
        Diagnostics: source, symbol, timeframe, requested/returned bars,
        first/last timestamps, generated_at, freshness.

        RESYNC (BUG-054): after a 5-6h downtime the frontend reloads the full
        session; the default window is 900 bars and a successful broker fetch
        also reseeds the engine aggregator + ServerState so chart, features,
        regime and overlays all converge on real broker candles.

        NEVER synthetic bars.
        """
        engine = app.state.engine
        now_iso = _iso_now()
        bars: list[dict[str, Any]] = []
        source = "UNAVAILABLE"
        symbol: str | None = None
        timeframe = "M1"
        requested = max(1, min(int(count), 5000))
        returned = 0
        first_ts: str | None = None
        last_ts: str | None = None
        error_state: dict[str, Any] | None = None

        if engine is not None:
            try:
                symbol = engine.config.execution.symbol or "XAUUSD"
            except Exception:
                symbol = "XAUUSD"
            try:
                timeframe = str(getattr(engine.config.execution, "timeframe", "M1") or "M1").upper()
            except Exception:
                timeframe = "M1"

            # 1) Authoritative path: official MT5 rate provider.
            try:
                rate_bars = engine.adapter.get_rate_history(
                    symbol=symbol, timeframe=timeframe, count=requested
                )
                if rate_bars:
                    for r in rate_bars:
                        if r.time_utc is None:
                            continue
                        bars.append(
                            {
                                "time": r.time_utc.isoformat(),
                                "open": float(r.open) if r.open is not None else None,
                                "high": float(r.high) if r.high is not None else None,
                                "low": float(r.low) if r.low is not None else None,
                                "close": float(r.close) if r.close is not None else None,
                                "tick_volume": r.tick_volume,
                                "spread": r.spread,
                                "real_volume": r.real_volume,
                                "is_complete": True,
                            }
                        )
                    returned = len(bars)
                    source = "MT5"
                    if bars:
                        first_ts = bars[0]["time"]
                        last_ts = bars[-1]["time"]
                    logger.info(
                        "[MT5_CHART] event=HISTORY_LOADED symbol=%s timeframe=%s requested=%s received=%s last=%s",
                        symbol,
                        timeframe,
                        requested,
                        returned,
                        last_ts,
                    )

                    # RESYNC (BUG-054): mirror the fetched broker bars into the
                    # engine aggregator + ServerState so every UI surface
                    # (snapshot, SSE, overlays) converges instantly instead of
                    # waiting for the next live tick.
                    try:
                        rate_bars_dt = [
                            r
                            for r in rate_bars
                            if r.time_utc is not None
                            and r.open is not None
                            and r.high is not None
                            and r.low is not None
                            and r.close is not None
                        ]
                        if rate_bars_dt and hasattr(engine, "aggregator"):
                            from nexus_scalp.market_data.bar_aggregator import BarData

                            seeded = [
                                BarData(
                                    symbol=symbol,
                                    timeframe=str(timeframe).upper(),
                                    timestamp=r.time_utc,
                                    open=float(r.open),
                                    high=float(r.high),
                                    low=float(r.low),
                                    close=float(r.close),
                                    tick_volume=int(r.tick_volume or 0),
                                    is_complete=True,
                                )
                                for r in rate_bars_dt
                            ]
                            engine.aggregator.reseed(seeded)
                            if hasattr(engine, "sync_chart_state"):
                                engine.sync_chart_state()
                    except Exception as reseed_err:
                        _log_err(
                            reseed_err,
                            "Chart history: engine reseed failed (non-fatal)",
                            endpoint="/api/chart/history",
                        )
            except Exception as e:
                _log_err(
                    e,
                    "Chart history: MT5 rate provider failed",
                    endpoint="/api/chart/history",
                )
                error_state = {
                    "code": "MT5_RATE_HISTORY_FAILED",
                    "message": "Broker history unavailable",
                }

            # 2) Fallback: engine-synchronized bars (explicit provenance).
            if not bars:
                try:
                    completed = engine.aggregator.get_completed_bars()
                    for b in completed[-requested:]:
                        bars.append(
                            {
                                "time": b.timestamp.isoformat(),
                                "open": b.open,
                                "high": b.high,
                                "low": b.low,
                                "close": b.close,
                                "tick_volume": b.tick_volume,
                                "spread": None,
                                "real_volume": None,
                                "is_complete": True,
                            }
                        )
                    forming = engine.aggregator.get_current_forming_bar()
                    if forming:
                        bars.append(
                            {
                                "time": forming.timestamp.isoformat(),
                                "open": forming.open,
                                "high": forming.high,
                                "low": forming.low,
                                "close": forming.close,
                                "tick_volume": forming.tick_volume,
                                "spread": None,
                                "real_volume": None,
                                "is_complete": False,
                            }
                        )
                    if bars:
                        source = "ENGINE_STATE"
                        returned = len(bars)
                        first_ts = bars[0]["time"]
                        last_ts = bars[-1]["time"]
                except Exception as e:
                    _log_err(
                        e,
                        "Chart history: engine bars failed",
                        endpoint="/api/chart/history",
                    )

        state = get_system_state()
        overlays = state.get("visual_overlays", {})
        overlays.setdefault("rectangles", [])
        overlays.setdefault("bos_lines", [])
        overlays.setdefault("midlines", [])
        overlays.setdefault("liq_markers", [])
        overlays.setdefault("order_lines", None)

        return {
            "bars": bars,
            "bars_available": bool(bars),
            "source": source,
            "symbol": symbol,
            "timeframe": timeframe,
            "requested": requested,
            "returned": returned,
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "generated_at": now_iso,
            "error": error_state,
            "visual_overlays": overlays,
        }

    # GET /api/mt5/status - real broker connection + account + symbol + history
    @app.get("/api/mt5/status")
    def get_mt5_status(history_days: int = 1) -> dict[str, Any]:
        """Real MT5 runtime truth: connection state, typed account snapshot,
        symbol spec + current tick, all account positions, pending orders,
        historical orders/deals (bounded, UTC), broker-native calcs.
        Never synthetic: every failing read carries error_state.
        """
        engine = app.state.engine
        if engine is None:
            return {
                "available": False,
                "reason": "ENGINE_OFFLINE",
                "account": {},
                "symbol": {},
                "positions": [],
                "orders": [],
                "history": {},
                "calculations": {},
                "connection": {},
            }

        symbol = None
        try:
            symbol = engine.config.execution.symbol or "XAUUSD"
        except Exception:
            symbol = "XAUUSD"

        out: dict[str, Any] = {"available": True, "symbol": symbol}
        try:
            conn = engine.adapter.connection_state()
            out["connection"] = conn.to_dict() if hasattr(conn, "to_dict") else {}
        except Exception as e:
            _log_err(e, "MT5 status: connection failed", endpoint="/api/mt5/status")

        try:
            snap = engine.adapter.get_account_snapshot()
            out["account"] = (
                {
                    "available": snap.available,
                    "source": snap.source,
                    "captured_at": snap.captured_at.isoformat(),
                    "error_state": snap.error_state,
                    **{
                        k: getattr(snap, k)
                        for k in (
                            "login",
                            "server",
                            "company",
                            "currency",
                            "currency_digits",
                            "trade_mode",
                            "leverage",
                            "limit_orders",
                            "margin_so_mode",
                            "trade_allowed",
                            "trade_expert",
                            "margin_mode",
                            "fifo_close",
                            "balance",
                            "credit",
                            "profit",
                            "equity",
                            "margin",
                            "margin_free",
                            "margin_level",
                            "margin_level_source",
                            "floating_pnl",
                            "net_pnl",
                            "open_positions_count",
                            "pending_orders_count",
                        )
                    },
                }
                if hasattr(snap, "available")
                else {"available": False}
            )
        except Exception as e:
            _log_err(e, "MT5 status: account failed", endpoint="/api/mt5/status")
            out["account"] = {"available": False}

        try:
            sym = engine.adapter.get_symbol_snapshot(symbol)
            out["symbol"] = {
                "available": sym.available,
                "source": sym.source,
                "specification": sym.spec,
                "current_tick": sym.tick,
                "spread_points": sym.spread_points,
                "spread_points_source": sym.spread_points_source,
                "tick_stale": sym.tick_stale,
                "tick_freshness_ms": sym.tick_freshness_ms,
                "error_state": sym.error_state,
            }
        except Exception as e:
            _log_err(e, "MT5 status: symbol failed", endpoint="/api/mt5/status")
            out["symbol"] = {"available": False}

        try:
            all_pos = engine.adapter.get_all_positions()
            out["positions"] = [
                {
                    k: getattr(p, k)
                    for k in (
                        "ticket",
                        "symbol",
                        "type",
                        "magic",
                        "volume",
                        "price_open",
                        "price_current",
                        "sl",
                        "tp",
                        "profit",
                        "swap",
                        "commission",
                        "time",
                    )
                }
                for p in all_pos
            ]
        except Exception as e:
            _log_err(e, "MT5 status: positions failed", endpoint="/api/mt5/status")
            out["positions"] = []

        try:
            pend = engine.adapter.get_pending_orders_snapshot()
            out["orders"] = [
                {
                    k: getattr(o, k)
                    for k in (
                        "ticket",
                        "symbol",
                        "type",
                        "magic",
                        "volume_current",
                        "price_open",
                        "sl",
                        "tp",
                        "state",
                        "time_setup",
                    )
                }
                for o in pend
            ]
        except Exception as e:
            _log_err(e, "MT5 status: orders failed", endpoint="/api/mt5/status")
            out["orders"] = []

        # Historical orders/deals (bounded window, never on the tick path).
        from datetime import timedelta as _td

        try:
            now = datetime.now(UTC)
            from_dt = now - _td(days=max(1, min(int(history_days), 30)))
            hist_orders = engine.adapter.get_history_orders(from_dt, now)
            hist_deals = engine.adapter.get_history_deals(from_dt, now)
            out["history"] = {
                "from": from_dt.isoformat(),
                "to": now.isoformat(),
                "orders_requested": True,
                "orders": [
                    {
                        k: getattr(o, k)
                        for k in (
                            "ticket",
                            "symbol",
                            "type",
                            "magic",
                            "volume_initial",
                            "volume_current",
                            "price_open",
                            "sl",
                            "tp",
                            "state",
                            "time_setup",
                            "time_done",
                            "reason",
                        )
                    }
                    for o in hist_orders
                ],
                "deals": [
                    {
                        k: getattr(d, k)
                        for k in (
                            "ticket",
                            "order",
                            "position_id",
                            "symbol",
                            "type",
                            "entry",
                            "magic",
                            "volume",
                            "price",
                            "profit",
                            "fee",
                            "swap",
                            "commission",
                            "time",
                            "reason",
                        )
                    }
                    for d in hist_deals
                ],
                "deals_net_result": [d.net_result for d in hist_deals],
            }
        except Exception as e:
            _log_err(e, "MT5 status: history failed", endpoint="/api/mt5/status")
            out["history"] = {"available": False}

        # Broker-native calculations (order_calc_*; provenance-tagged).
        try:
            sym_spec = (out.get("symbol") or {}).get("specification") or {}
            tick_map = (out.get("symbol") or {}).get("current_tick") or {}
            calc_symbol = sym_spec.get("name") or symbol
            price = float(tick_map.get("bid") or tick_map.get("last") or 0.0)
            volume = 0.01
            profit_calc = engine.adapter.order_calc_profit_snapshot(
                symbol=calc_symbol,
                order_type=0,  # POSITION_TYPE_BUY
                volume=volume,
                price_open=price if price > 0 else 2000.0,
                price_close=(price + 1.0) if price > 0 else 2001.0,
            )
            margin_calc = engine.adapter.order_calc_margin_snapshot(
                symbol=calc_symbol,
                order_type=0,
                volume=volume,
                price=price if price > 0 else 2000.0,
            )
            out["calculations"] = {
                "order_calc_profit": {
                    "available": profit_calc.available,
                    "value": profit_calc.value,
                    "source": profit_calc.value_source,
                    "error": (
                        {"code": profit_calc.error_code, "message": profit_calc.error_message}
                        if not profit_calc.available
                        else None
                    ),
                },
                "order_calc_margin": {
                    "available": margin_calc.available,
                    "value": margin_calc.value,
                    "source": margin_calc.value_source,
                    "error": (
                        {"code": margin_calc.error_code, "message": margin_calc.error_message}
                        if not margin_calc.available
                        else None
                    ),
                },
                "note": (
                    "BROKER_NATIVE = computed by MT5 order_calc_*; "
                    "FALLBACK_ESTIMATE = mathematical estimate, never claimed broker-exact"
                ),
            }
        except Exception as e:
            _log_err(e, "MT5 status: calculations failed", endpoint="/api/mt5/status")
            out["calculations"] = {"available": False}

        try:
            term = engine.adapter.get_terminal_state()
            out["terminal"] = term
        except Exception as e:
            _log_err(e, "MT5 status: terminal failed", endpoint="/api/mt5/status")
            out["terminal"] = {"available": False}

        return out

    # GET /api/algo/config
    @app.get("/api/algo/config")
    def get_algo_config() -> dict[str, Any]:
        live_config_path = Path("configs/live.yaml")
        if not live_config_path.exists():
            live_config_path = Path("configs/base.yaml")

        with open(live_config_path, encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}

        algo_data = raw_data.get("algo", {})
        return {
            "atr_sl_buffer_multiplier": algo_data.get("atr_sl_buffer_multiplier", 1.5),
            "min_risk_reward_ratio": algo_data.get("min_risk_reward_ratio", 1.8),
            "ai_zone_confidence_threshold": algo_data.get("ai_zone_confidence_threshold", 0.82),
            "fvg_mitigation_sensitivity": algo_data.get("fvg_mitigation_sensitivity", 0.5),
            "order_block_lookback_bars": algo_data.get("order_block_lookback_bars", 30),
        }

    # PUT /api/algo/config
    @app.put("/api/algo/config")
    def save_algo_config(req: AlgoConfigRequest) -> dict[str, Any]:
        live_config_path = Path("configs/live.yaml")
        if not live_config_path.exists():
            live_config_path = Path("configs/base.yaml")

        try:
            with open(live_config_path, encoding="utf-8") as f:
                raw_data = yaml.safe_load(f) or {}

            raw_data["algo"] = req.model_dump()

            tmp = Path("configs/live.yaml").with_suffix(".yaml.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(raw_data, f, default_flow_style=False)
            tmp.replace(Path("configs/live.yaml"))

            engine = app.state.engine
            if engine:
                logger.info("Hot-reloading algorithm live tuner parameters dynamically.")
                new_cfg = AppConfig.load_from_yaml(Path("configs/live.yaml"))
                engine.config = new_cfg
                engine.signal_policy.algo_config = new_cfg.algo

            return {"success": True}
        except Exception as e:
            log_web_error(
                logger,
                "/api",
                None,
                e,
                context={"msg": "Failed to save and hot-reload algo tuner configurations"},
            )
            return _err("OPERATION_FAILED")

    # Modify position SL/TP
    @app.post("/api/positions/modify")
    def modify_position(req: ModifyPositionRequest) -> dict[str, Any]:
        engine = app.state.engine
        if not engine:
            raise HTTPException(status_code=400, detail="Trading Engine offline.")

        success = engine.adapter.modify_position(
            ticket=req.ticket, stop_loss=req.stop_loss, take_profit=req.take_profit
        )
        return {"success": success}

    # Close live positions
    @app.post("/api/positions/close")
    def close_position(req: ClosePositionRequest) -> dict[str, Any]:
        engine = app.state.engine
        if not engine:
            raise HTTPException(status_code=400, detail="Trading Engine offline.")

        success = engine.adapter.close_position(ticket=req.ticket)
        return {"success": success}

    # Simulation: Inject simulated tick (EXPLICIT PAPER MODE ONLY)
    @app.post("/api/simulation/tick")
    def inject_tick(req: SimulationTickRequest) -> dict[str, Any]:
        engine = app.state.engine
        if not engine:
            return {"success": False, "message": "Engine not initialized"}

        # FORENSIC HARDENING: simulated ticks are permitted ONLY when the
        # execution mode is explicitly SIMULATION/PAPER. In LIVE mode this
        # endpoint is a no-op so synthetic prices can never masquerade as
        # production telemetry (previously it injected hardcoded 2334.21 fake
        # ticks into the live pipeline).
        try:
            mode = engine.config.execution.mode.value
        except Exception:
            mode = "LIVE"
        if mode not in ("SIMULATION", "PAPER"):
            return {
                "success": False,
                "message": f"Simulation injection blocked: execution_mode={mode} (explicit SIMULATION/PAPER required).",
            }

        symbol = engine.config.execution.symbol
        try:
            current_tick = engine.adapter.get_last_tick(symbol)
        except Exception:
            current_tick = None
        if not current_tick:
            return {
                "success": False,
                "message": "No base tick available for simulation (adapter offline).",
            }

        # Apply simulation tick displacement pressure
        bid_change = 0.0
        ask_change = 0.0

        if req.type == "BUY_PRESSURE":
            bid_change = 0.85
            ask_change = 0.85
        elif req.type == "SELL_PRESSURE":
            bid_change = -0.85
            ask_change = -0.85
        elif req.type == "VOLATILE_SWEEP":
            bid_change = -2.50
            ask_change = -2.50

        simulated_tick = TickData(
            symbol=symbol,
            timestamp=datetime.now(UTC),
            bid=current_tick.bid + bid_change,
            ask=current_tick.ask + ask_change,
            volume=current_tick.volume + 5.0,
        )

        # Inject simulated tick directly to engine tick processor pipeline
        logger.info(
            "Injecting interactive simulation tick pressure (PAPER MODE).",
            type=req.type,
            bid=simulated_tick.bid,
            ask=simulated_tick.ask,
        )

        # Process the simulated tick if the engine loop is running
        if engine._running:
            engine._process_tick_pipeline(
                tick=simulated_tick, account=engine.adapter.get_account_info()
            )

        return {"success": True, "mode": mode}

    # Historical Replay Mode Controller
    @app.post("/api/replay/toggle")
    def toggle_replay(req: ToggleReplayRequest) -> dict[str, Any]:
        app.state.is_replaying = req.active
        app.state.replay_speed = req.speed

        engine = app.state.engine
        if engine:
            if req.active:
                logger.info("Historical Replay mode enabled.", speed=req.speed)
                # Toggle engine configuration to replay mode
                engine.config.execution.mode = ExecutionMode.PAPER
            else:
                logger.info("Historical Replay mode disabled.")
                engine.config.execution.mode = ExecutionMode.LIVE

        return {"success": True, "replaying": req.active}

    # =========================================================================
    # MODULE C: DEBUG & DIAGNOSTICS HUB — BACKEND REST ENDPOINTS
    # =========================================================================

    @app.get("/api/debug/features")
    def get_debug_features() -> dict[str, Any]:
        """
        Real-time values of all 50 features (feat_0 .. feat_49).

        Each entry reports the raw model-input value alongside a health status so the UI
        can flag NaN/Inf anomalies, plus a staleness assessment of the feature snapshot
        as a whole (age of the last computed FeatureVector).
        """
        engine = app.state.engine

        raw_values: list[Any] = [0.0] * len(FEATURE_NAMES)
        feature_timestamp: str | None = None
        age_seconds: float | None = None
        engine_online = engine is not None

        if engine is not None:
            try:
                fv = engine._last_fv
                if fv is not None:
                    raw_values = list(fv.to_tensor_input())
                    feature_timestamp = getattr(fv, "timestamp_utc", None)
                    if feature_timestamp:
                        try:
                            ts = datetime.fromisoformat(str(feature_timestamp))
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=UTC)
                            age_seconds = max(0.0, (datetime.now(UTC) - ts).total_seconds())
                        except (TypeError, ValueError):
                            age_seconds = None
            except Exception as e:
                log_web_error(
                    logger,
                    "/api",
                    None,
                    e,
                    context={"msg": "Debug features: failed to read live feature vector"},
                )

        features_payload: list[dict[str, Any]] = []
        nan_count = 0
        inf_count = 0

        for idx, name in enumerate(FEATURE_NAMES):
            raw = raw_values[idx] if idx < len(raw_values) else 0.0
            value, status = _classify_feature(raw)
            if status == "NAN":
                nan_count += 1
            elif status == "INF":
                inf_count += 1
            features_payload.append(
                {
                    "index": idx,
                    "key": f"feat_{idx}",
                    "name": name,
                    "value": value,
                    "status": status,
                    "is_valid": status == "VALID",
                }
            )

        # A snapshot older than 15s means the tick pipeline is not feeding the model.
        STALE_THRESHOLD_SEC = 15.0
        is_stale = (age_seconds is None) or (age_seconds > STALE_THRESHOLD_SEC)

        return {
            "engine_online": engine_online,
            "feature_count": len(features_payload),
            "features": features_payload,
            "nan_count": nan_count,
            "inf_count": inf_count,
            "anomaly_count": nan_count + inf_count,
            "all_valid": (nan_count + inf_count) == 0,
            "timestamp_utc": feature_timestamp,
            "age_seconds": age_seconds,
            "is_stale": is_stale,
            "stale_threshold_seconds": STALE_THRESHOLD_SEC,
        }

    @app.post("/api/debug/model-test")
    def post_debug_model_test(req: ModelTestRequest) -> dict[str, Any]:
        """
        Runs an instant PyTorch ScalpNet inference against a supplied (or live) 50D vector.

        Returns class probabilities (ai_no_trade / ai_buy / ai_sell), the argmax label and
        the inference latency, so the Debug Hub can verify the model end-to-end without
        waiting for a live signal.
        """
        engine = app.state.engine
        expected_dim = len(FEATURE_NAMES)

        features = req.features
        source = "REQUEST"

        if features is None or req.use_live_features:
            if engine is None or getattr(engine, "_last_fv", None) is None:
                raise HTTPException(
                    status_code=400,
                    detail="No feature vector supplied and no live features available (engine offline).",
                )
            features = list(engine._last_fv.to_tensor_input())
            source = "LIVE"

        if len(features) != expected_dim:
            raise HTTPException(
                status_code=422,
                detail=f"Feature vector must contain exactly {expected_dim} values, got {len(features)}.",
            )

        sanitized: list[float] = []
        sanitized_count = 0
        for raw in features:
            value, status = _classify_feature(raw)
            if status != "VALID":
                sanitized_count += 1
            sanitized.append(value)

        try:
            import numpy as np
            import torch
        except Exception as import_err:
            _log_err(import_err, "PyTorch runtime unavailable", endpoint="/api/debug/model-test")
            raise HTTPException(
                status_code=503,
                detail="PyTorch runtime is temporarily unavailable on this host.",
            ) from import_err

        started = time.perf_counter()

        try:
            if engine is not None and getattr(engine, "_bundle", None) is not None:
                # Use the live bundle so the test exercises the exact deployed weights and scaler.
                with engine._bundle_lock:
                    bundle = engine._bundle
                x_np = np.array(sanitized, dtype=np.float32).reshape(1, -1)
                x_np = bundle.scaler.transform_50d(x_np)
                x = torch.tensor(x_np, dtype=torch.float32)
                x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
                bundle.model.eval()
                with torch.inference_mode():
                    probs_tensor = bundle.model(x)
                model_source = "LIVE_BUNDLE"
            else:
                # Engine offline: instantiate a fresh net so the endpoint still validates
                # the model graph and tensor contract.
                from nexus_scalp.models.scalp_net import ScalpNet

                model = ScalpNet(num_features=expected_dim, num_classes=4)
                model.eval()
                x = torch.tensor([sanitized], dtype=torch.float32)
                x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
                with torch.inference_mode():
                    probs_tensor = model(x)
                model_source = "FRESH_INSTANCE"
        except HTTPException:
            raise
        except Exception as infer_err:
            _log_err(
                infer_err, "Debug model test inference failed", endpoint="/api/debug/model-test"
            )
            raise HTTPException(
                status_code=500,
                detail="Model inference could not be completed.",
            ) from infer_err

        latency_ms = (time.perf_counter() - started) * 1000.0

        probs_list = [float(p) for p in probs_tensor.detach().cpu().numpy().flatten().tolist()]
        while len(probs_list) < 4:
            probs_list.append(0.0)

        ai_no_trade, ai_buy, ai_sell = probs_list[0], probs_list[1], probs_list[2]
        ai_wait = probs_list[3]

        labels = {0: "NO_TRADE", 1: "BUY_MARKET", 2: "SELL_MARKET", 3: "WAIT"}
        argmax_idx = max(range(len(probs_list)), key=lambda i: probs_list[i])

        return {
            "success": True,
            "feature_source": source,
            "model_source": model_source,
            "sanitized_inputs": sanitized_count,
            "ai_no_trade": ai_no_trade,
            "ai_buy": ai_buy,
            "ai_sell": ai_sell,
            "ai_wait": ai_wait,
            "probabilities": probs_list,
            "predicted_class_index": argmax_idx,
            "predicted_label": labels.get(argmax_idx, "UNKNOWN"),
            "confidence": probs_list[argmax_idx],
            "latency_ms": round(latency_ms, 3),
            "evaluated_at": datetime.now(UTC).isoformat(),
        }

    @app.get("/api/debug/health")
    def get_debug_health() -> dict[str, Any]:
        """
        Subsystem health widgets: Feature Engine, PyTorch Model, Risk Engine,
        MT5 Win32 IPC Adapter and Audit Database.

        Each subsystem reports HEALTHY / DEGRADED / UNHEALTHY / DISCONNECTED plus a short
        human-readable detail string and subsystem-specific metrics.
        """
        engine = app.state.engine
        subsystems: list[dict[str, Any]] = []

        def add(name: str, status: str, detail: str, metrics: dict[str, Any] | None = None) -> None:
            subsystems.append(
                {
                    "name": name,
                    "status": status,
                    "detail": detail,
                    "metrics": metrics or {},
                }
            )

        # --- 1. Feature Engine ---
        if engine is None:
            add(
                "Feature Engine",
                "DISCONNECTED",
                "Engine reference is not attached to the web server.",
            )
        else:
            try:
                fv = engine._last_fv
                if fv is None:
                    add(
                        "Feature Engine",
                        "DEGRADED",
                        "No feature vector computed yet (waiting for first tick).",
                    )
                else:
                    values = list(fv.to_tensor_input())
                    bad = sum(1 for v in values if _classify_feature(v)[1] != "VALID")
                    dim_ok = len(values) == len(FEATURE_NAMES)
                    if not dim_ok:
                        add(
                            "Feature Engine",
                            "UNHEALTHY",
                            f"Dimensionality contract violated: {len(values)} != {len(FEATURE_NAMES)}.",
                            {"dimensions": len(values), "expected": len(FEATURE_NAMES)},
                        )
                    elif bad:
                        add(
                            "Feature Engine",
                            "DEGRADED",
                            f"{bad} of {len(values)} features are NaN/Inf.",
                            {"anomalies": bad, "dimensions": len(values)},
                        )
                    else:
                        add(
                            "Feature Engine",
                            "HEALTHY",
                            f"All {len(values)} features numeric and within contract.",
                            {"anomalies": 0, "dimensions": len(values)},
                        )
            except Exception as e:
                _log_err(
                    e, "Feature engine health introspection failed", endpoint="/api/debug/health"
                )
                add("Feature Engine", "UNHEALTHY", "Feature extraction raised an internal error.")

        # --- 2. PyTorch Model ---
        if engine is None:
            add("PyTorch Model", "DISCONNECTED", "Engine offline; model bundle not loaded.")
        else:
            try:
                with engine._bundle_lock:
                    bundle = engine._bundle
                if bundle is None:
                    add("PyTorch Model", "UNHEALTHY", "Model bundle is not initialized.")
                else:
                    scaler_ready = bool(getattr(bundle.scaler, "is_ready", lambda: False)())
                    probs = engine._last_probs
                    last_infer_ok = probs is not None
                    metrics = {
                        "artifact_path": str(getattr(bundle, "artifact_path", "")),
                        "scaler_ready": scaler_ready,
                        "last_inference_available": last_infer_ok,
                    }
                    if not scaler_ready:
                        add(
                            "PyTorch Model",
                            "DEGRADED",
                            "Weights loaded but scaler artifact is not fitted.",
                            metrics,
                        )
                    elif not last_infer_ok:
                        add(
                            "PyTorch Model",
                            "DEGRADED",
                            "Model ready; awaiting first live inference.",
                            metrics,
                        )
                    else:
                        add(
                            "PyTorch Model",
                            "HEALTHY",
                            "ScalpNet loaded with fitted scaler and live inference flowing.",
                            metrics,
                        )
            except Exception as e:
                _log_err(e, "Model health introspection failed", endpoint="/api/debug/health")
                add("PyTorch Model", "UNHEALTHY", "Model introspection failed.")

        # --- 3. Risk Engine ---
        if engine is None:
            add("Risk Engine", "DISCONNECTED", "Engine offline.")
        else:
            try:
                risk = engine.risk_engine
                kill_switch = bool(getattr(risk, "_kill_switch_active", False))
                metrics = {
                    "kill_switch_active": kill_switch,
                    "max_allowed_lots": float(getattr(risk, "max_allowed_lots", 0.0)),
                    "hard_max_lots": 10.0,
                    "min_risk_reward_ratio": float(getattr(risk, "min_risk_reward_ratio", 0.0)),
                    "survival_mode": bool(getattr(engine, "_survival_mode_active", False)),
                }
                if kill_switch:
                    add(
                        "Risk Engine",
                        "UNHEALTHY",
                        "EMERGENCY KILL SWITCH ACTIVE — all execution rejected.",
                        metrics,
                    )
                elif metrics["survival_mode"]:
                    add(
                        "Risk Engine",
                        "DEGRADED",
                        "Survival mode active: thresholds tightened after drawdown.",
                        metrics,
                    )
                else:
                    add(
                        "Risk Engine",
                        "HEALTHY",
                        "Clamps armed (HARD_MAX_LOTS = 10.0), kill switch disengaged.",
                        metrics,
                    )
            except Exception as e:
                _log_err(e, "Risk engine health introspection failed", endpoint="/api/debug/health")
                add("Risk Engine", "UNHEALTHY", "Risk engine introspection failed.")

        # --- 4. MT5 Win32 IPC Adapter ---
        if engine is None:
            add("MT5 Win32 IPC Adapter", "DISCONNECTED", "Engine offline; no broker adapter bound.")
        else:
            try:
                adapter = engine.adapter
                is_conn_fn = getattr(adapter, "is_connected", None)
                connected = bool(is_conn_fn()) if callable(is_conn_fn) else True

                tick = engine._last_tick
                tick_age = None
                if tick is not None:
                    try:
                        tick_age = max(0.0, (datetime.now(UTC) - tick.timestamp).total_seconds())
                    except Exception:
                        tick_age = None

                metrics = {
                    "adapter": type(adapter).__name__,
                    "connected": connected,
                    "last_tick_age_seconds": tick_age,
                    "execution_mode": engine.config.execution.mode.value,
                    "symbol": engine.config.execution.symbol,
                }
                if not connected:
                    add(
                        "MT5 Win32 IPC Adapter",
                        "DISCONNECTED",
                        "Broker IPC channel reports disconnected.",
                        metrics,
                    )
                elif tick_age is None:
                    add(
                        "MT5 Win32 IPC Adapter",
                        "DEGRADED",
                        "Connected but no tick has been received yet.",
                        metrics,
                    )
                elif tick_age > 15.0:
                    add(
                        "MT5 Win32 IPC Adapter",
                        "DEGRADED",
                        f"Tick stream stale ({tick_age:.1f}s since last tick).",
                        metrics,
                    )
                else:
                    add(
                        "MT5 Win32 IPC Adapter",
                        "HEALTHY",
                        f"Live tick stream active ({tick_age:.1f}s ago).",
                        metrics,
                    )
            except Exception as e:
                _log_err(e, "MT5 adapter health introspection failed", endpoint="/api/debug/health")
                add("MT5 Win32 IPC Adapter", "UNHEALTHY", "Adapter introspection failed.")

        # --- 5. Audit Database ---
        try:
            if engine is not None:
                repo = engine.audit
            else:
                from nexus_scalp.adapters.database.audit_repository import AuditRepository

                repo = AuditRepository()

            metrics_db = repo.get_account_performance_metrics()
            queue_size = 0
            queue_obj = getattr(repo, "_queue", None)
            if queue_obj is not None:
                try:
                    queue_size = int(queue_obj.qsize())
                except Exception:
                    queue_size = 0

            worker = getattr(repo, "_worker_thread", None)
            worker_alive = bool(worker.is_alive()) if worker is not None else False

            metrics = {
                "db_path": getattr(repo, "_db_path", ""),
                "write_queue_depth": queue_size,
                "worker_alive": worker_alive,
                "total_trades": metrics_db.get("total_trades", 0),
            }
            if not worker_alive:
                add(
                    "Audit Database", "DEGRADED", "Background write worker is not running.", metrics
                )
            elif queue_size > 5000:
                add(
                    "Audit Database",
                    "DEGRADED",
                    f"Write queue backing up ({queue_size} pending).",
                    metrics,
                )
            else:
                add(
                    "Audit Database",
                    "HEALTHY",
                    "WAL storage reachable; async writer draining normally.",
                    metrics,
                )
        except Exception as e:
            _log_err(e, "Audit DB health introspection failed", endpoint="/api/debug/health")
            add("Audit Database", "UNHEALTHY", "Audit database is unreachable.")

        rank = {"HEALTHY": 0, "DEGRADED": 1, "UNHEALTHY": 2, "DISCONNECTED": 2}
        overall = "HEALTHY"
        for sub in subsystems:
            if rank.get(sub["status"], 0) > rank.get(overall, 0):
                overall = sub["status"]

        return {
            "overall_status": overall,
            "subsystems": subsystems,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    @app.get("/api/debug/ipc-telemetry")
    def get_debug_ipc_telemetry(limit: int = 50) -> dict[str, Any]:
        """
        Recent broker execution events for the MT5 IPC Telemetry Console:
        order state transitions, reason/retcode strings and measured IPC latency.
        """
        engine = app.state.engine
        try:
            if engine is not None:
                repo = engine.audit
            else:
                from nexus_scalp.adapters.database.audit_repository import AuditRepository

                repo = AuditRepository()
            events = repo.get_recent_order_events(limit=max(1, min(limit, 500)))
        except Exception as e:
            log_web_error(
                logger, "/api", None, e, context={"msg": "Debug IPC telemetry retrieval failed"}
            )
            events = []

        latencies = [float(e.get("latency") or 0.0) for e in events if e.get("latency") is not None]
        avg_latency_ms = round((sum(latencies) / len(latencies)) * 1000.0, 2) if latencies else 0.0

        exposure = {"positions": 0, "pendings": 0}
        if engine is not None and hasattr(engine.order_manager, "count_total_exposure"):
            try:
                pos, pend = engine.order_manager.count_total_exposure()
                exposure = {"positions": pos, "pendings": pend}
            except Exception:
                pass

        return {
            "events": events,
            "event_count": len(events),
            "avg_latency_ms": avg_latency_ms,
            "exposure": exposure,
            "max_total_exposure": 1,
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    # =========================================================================
    # PHASE 08 EXPERIENCE INTELLIGENCE REST APIs
    # -------------------------------------------------------------------------
    # All endpoints are READ-ONLY over derived state, except the explicit
    # self-heal endpoint which only rebuilds derived intelligence from the
    # immutable ledger (it can never modify or delete raw experience rows).
    # =========================================================================
    @app.get("/api/experience/summary")
    def get_experience_summary() -> dict[str, Any]:
        """Aggregate experience/gate telemetry including schema provenance."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "experience_engine"):
            return {
                "enabled": False,
                "recorded_experiences": 0,
                "active_strategies": 0,
            }

        try:
            summary = dict(engine.experience_engine.summary())
        except Exception as e:
            log_web_error(
                logger, "/api", None, e, context={"msg": "Failed to build experience summary"}
            )
            summary = {"enabled": False, "recorded_experiences": 0}

        lifecycle_counts: dict[str, int] = {}
        try:
            with sqlite3.connect(engine.audit._db_path, timeout=5.0) as conn:
                rows = conn.execute(
                    """
                    SELECT lifecycle_state, COUNT(*) FROM strategy_intelligence_registry
                    GROUP BY lifecycle_state;
                    """
                ).fetchall()
                lifecycle_counts = {str(r[0]): int(r[1]) for r in rows}
        except Exception:
            lifecycle_counts = {}

        summary["lifecycle_counts"] = lifecycle_counts
        summary["active_strategies"] = lifecycle_counts.get("ACTIVE", 0)
        summary["retired_strategies"] = lifecycle_counts.get("RETIRED", 0)
        summary["fetched_at"] = datetime.now(UTC).isoformat()
        return serialize_enums(summary)

    @app.get("/api/experience/strategies")
    def get_experience_strategies(limit: int = 50) -> list[dict[str, Any]]:
        """Bounded listing of derived strategy scores, newest first."""
        engine = app.state.engine
        if not engine:
            return []

        bounded = max(1, min(int(limit), 500))
        try:
            with sqlite3.connect(engine.audit._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM strategy_intelligence_registry ORDER BY updated_at DESC LIMIT ?;",
                    (bounded,),
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            log_web_error(
                logger, "/api", None, e, context={"msg": "Failed to retrieve experience strategies"}
            )
            return []

    @app.get("/api/experience/decision")
    def get_last_experience_decision() -> dict[str, Any]:
        """Most recent pre-trade experience verdict, for live explainability."""
        engine = app.state.engine
        decision = getattr(engine, "_last_experience_decision", None) if engine else None
        if decision is None:
            return {"available": False}
        try:
            payload = json.loads(decision.model_dump_json())
        except Exception as e:
            log_web_error(
                logger, "/api", None, e, context={"msg": "Failed to serialize experience decision"}
            )
            return {"available": False}
        return {"available": True, "decision": payload}

    @app.get("/api/experience/models")
    def get_experience_models(limit: int = 25) -> list[dict[str, Any]]:
        """
        Registered model provenance history.

        Proves model/memory separation: entries here may reference artifacts that
        no longer exist while the experience ledger remains intact.
        """
        engine = app.state.engine
        registry = getattr(engine, "model_registry", None) if engine else None
        if registry is None:
            return []
        try:
            return [dict(r) for r in registry.list_registered_models(limit=limit)]
        except Exception as e:
            log_web_error(
                logger, "/api", None, e, context={"msg": "Failed to retrieve model registry"}
            )
            return []

    @app.post("/api/experience/self-heal")
    def trigger_experience_self_heal() -> dict[str, Any]:
        """
        Rebuilds derived strategy intelligence from the immutable ledger.

        Raw experience rows are read-only during this operation.
        """
        engine = app.state.engine
        if not engine or not hasattr(engine, "rebuild_experience_intelligence"):
            return {"success": False, "rebuilt_strategies": 0, "reason": "ENGINE_UNAVAILABLE"}
        try:
            count = engine.rebuild_experience_intelligence()
            return {"success": True, "rebuilt_strategies": int(count)}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Experience self-heal failed"})
            return _err("OPERATION_FAILED", extra={"rebuilt_strategies": 0})

    # =========================================================================
    # PHASE 08: UNIFIED ACCOUNTING & PERFORMANCE INTELLIGENCE REST APIs
    # -------------------------------------------------------------------------
    # Every endpoint reads REAL data through the single canonical AccountingCore
    # facade (authoritative SQLite tables + derived cache warmed by the worker).
    # There is no synthetic fallback anywhere: when a metric cannot be derived
    # it is null and the dashboard renders an explicit unavailable state.
    # =========================================================================

    def _accounting() -> tuple[Any, Any] | None:
        """Returns (accounting_core, accounting_worker) when available."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "accounting_core"):
            return None
        return engine.accounting_core, getattr(engine, "accounting_worker", None)

    # GET /api/account/performance/intelligence — Performance Intelligence
    # report (PerformanceReportEngine): deterministic multi-stage enrichment
    # over the canonical accounting core. Read-only analytics; never writes
    # financial truth. The structured JSON contract is the same object the
    # Telegram daily report consumes.
    @app.get("/api/account/performance/intelligence")
    def get_account_performance_intelligence(kind: str = "DAY") -> dict[str, Any]:
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            enum_kind = PeriodKind(kind.upper())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown period kind: {kind}") from None
        try:
            from nexus_scalp.reporting import PerformanceReportEngine

            engine = PerformanceReportEngine(core=core, kind=enum_kind)
            container = engine.generate()
            report = container.to_dict()
            # TASK-2 §23 compact contract: truthful top-level intelligence state.
            b = report.get("behavioral", {})
            a = report.get("anomaly_state", {})
            payload = {
                "available": True,
                "report": report,
                "intelligence": {
                    "status": a.get("state", "NO_DATA"),
                    "behavior_state": b.get("state", "NO_DATA"),
                    "analysis_version": b.get("analysis_version", ""),
                    "anomaly_version": a.get("anomaly_version", ""),
                    "trades_analyzed": b.get("analyzed", 0),
                    "evidence_coverage": b.get("evidence_coverage"),
                    "behavioral_flags": b.get("flag_counts", {}),
                    "anomalies": a.get("counts", {}),
                    "estimated_impact": {},
                },
            }
            return serialize_enums(payload)
        except Exception as e:
            log_web_error(
                logger,
                "/api",
                None,
                e,
                context={"msg": "Performance intelligence report failed"},
            )
            return _err("INTERNAL_ERROR")

    @app.get("/api/account/performance")
    def get_account_performance() -> dict[str, Any]:
        """Canonical live + period performance overview (single truth)."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, worker = pair
        try:
            live = core.live_state()
            periods = core.all_period_reports()
            dd = core.drawdown_report()
            trades = core.load_trades(limit=1000)
            closed = [t for t in trades if t.closed_at is not None]
            wins = sum(1 for t in closed if t.is_win)
            losses = sum(1 for t in closed if t.outcome.value == "LOSS")
            decided = wins + losses
            realized_pnl = sum(t.net_pnl for t in closed)
            equity_pts = core.equity_curve(lookback_days=None)
            advanced = compute_advanced_metrics(trades, equity_points=equity_pts)
            return serialize_enums(
                {
                    "available": True,
                    "live": live.to_dict(),
                    "periods": {k: v.to_dict() for k, v in periods.items()},
                    "drawdown": dd.to_dict(),
                    "worker": format_worker_status(worker) if worker else None,
                    "totals": {
                        "closed_trades": len(closed),
                        "win_count": wins,
                        "loss_count": losses,
                        "win_rate": round(wins / decided * 100.0, 2) if decided else None,
                        "realized_pnl": round(realized_pnl, 2),
                    },
                    "advanced": advanced,
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
            )
        except Exception as e:
            log_web_error(
                logger, "/api", None, e, context={"msg": "Account performance read failed"}
            )
            return _err("INTERNAL_ERROR")

    @app.get("/api/account/performance/{kind}")
    def get_account_performance_period(kind: str) -> dict[str, Any]:
        """Canonical report for one granularity (DAY/WEEK/MONTH/YEAR)."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            enum_kind = PeriodKind(kind.upper())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown period kind: {kind}") from None
        try:
            report = core.period_report(enum_kind)
            return {"available": True, "period": report.to_dict()}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Period report failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/account/performance/{kind}/series")
    def get_account_performance_series(kind: str, count: int = 30) -> dict[str, Any]:
        """Bounded consecutive-period series for charts (oldest -> newest)."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            enum_kind = PeriodKind(kind.upper())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown period kind: {kind}") from None
        bounded = max(1, min(int(count), 60))
        try:
            reports = core.period_series(enum_kind, count=bounded)
            return {
                "available": True,
                "kind": enum_kind.value,
                "periods": [r.to_dict() for r in reports],
            }
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Period series failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/account/equity-curve")
    def get_account_equity_curve(lookback_days: int | None = None) -> dict[str, Any]:
        """Canonical balance/equity/drawdown time series for the dashboard."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            bounded = max(1, min(int(lookback_days), 730)) if lookback_days else None
            curve = core.equity_curve(lookback_days=bounded)
            cumulative = core.cumulative_pnl_curve(limit=500)
            return {
                "available": True,
                "equity_curve": curve,
                "cumulative_pnl": cumulative,
                "fetched_at": datetime.now(UTC).isoformat(),
            }
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Equity curve read failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/account/drawdown")
    def get_account_drawdown(lookback_days: int | None = None) -> dict[str, Any]:
        """Canonical drawdown state (ONE methodology for the whole system)."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            bounded = max(1, min(int(lookback_days), 730)) if lookback_days else None
            report = core.drawdown_report(lookback_days=bounded)
            out = report.to_dict()
            out["available"] = report.has_data or True
            return out
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Drawdown read failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/account/trades/{trade_id}")
    def get_account_trade_forensics(trade_id: int) -> dict[str, Any]:
        """Forensic reconstruction of one closed trade (ledger + orders + experience)."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            trace = core.trade_trace(ticket=int(trade_id))
            payload = trace.to_dict()
            payload["available"] = trace.found
            return payload
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Trade forensics failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/account/strategies")
    def get_account_strategies(limit: int = 50) -> dict[str, Any]:
        """Per-strategy contribution joined to Strategy Intelligence."""
        pair = _accounting()
        if pair is None:
            return {"available": False, "reason": "ENGINE_UNAVAILABLE"}
        core, _ = pair
        try:
            bounded = max(1, min(int(limit), 200))
            contributions = core.strategy_contributions(limit=bounded)
            return {
                "available": True,
                "strategies": [c.to_dict() for c in contributions],
                "fetched_at": datetime.now(UTC).isoformat(),
            }
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Strategy contributions failed"})
            return _err("INTERNAL_ERROR")

    # Observability stats
    @app.get("/api/observability/stats")
    def get_observability_stats() -> dict[str, Any]:
        engine = app.state.engine
        tg_queue_size = 0
        tg_enabled = False

        if engine and engine.notifier:
            tg_enabled = engine.notifier.enabled
            if hasattr(engine.notifier, "_queue"):
                tg_queue_size = engine.notifier._queue.qsize()

        # BUG-072: truthful live worker telemetry (never a fake 'Active' badge).
        health = engine.notifier.health_state() if engine and engine.notifier else {}
        return {
            "tg_enabled": tg_enabled,
            "tg_queue": tg_queue_size,
            "telegram": health,
        }

    # =========================================================================
    # PHASE 09: TRADE INTELLIGENCE REST APIs
    # -------------------------------------------------------------------------
    # READ-ONLY views over the derived intelligence tables (lifecycle timeline,
    # trade autopsies, behavior detections, evolution candidates) plus the
    # suitability verdict. Nothing here mutates financial truth or executes.
    # =========================================================================

    def _intelligence() -> tuple[Any, Any] | None:
        """Returns (engine, intelligence_worker) when available."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "intelligence_worker"):
            return None
        return (
            engine,
            getattr(engine, "intelligence_worker", None),
        )

    @app.get("/api/intelligence/summary")
    def get_intelligence_summary() -> dict[str, Any]:
        """Aggregate Trade Intelligence Brain telemetry + worker status."""
        pair = _intelligence()
        if pair is None:
            return {"available": False, "reasons": "ENGINE_UNAVAILABLE"}
        engine, worker = pair
        try:
            from nexus_scalp.intelligence.store import (
                count_autopsies,
                count_lifecycle_events,
            )

            summary = {
                "available": True,
                "lifecycle_events": count_lifecycle_events(engine.audit),
                "autopsies": count_autopsies(engine.audit),
                "worker": _intelligence_worker_status(worker),
                "fetch_time": datetime.now(UTC).isoformat(),
            }
            # Suitability verdict for the last proposal (live explainability).
            verdict = getattr(engine, "_last_suitability_verdict", None)
            if verdict is not None:
                summary["last_suitability"] = verdict.to_dict()
            return serialize_enums(summary)
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Intelligence summary failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/intelligence/positions/{ticket}/timeline")
    def get_position_timeline(ticket: int) -> dict[str, Any]:
        """Immutable position lifecycle timeline for one ticket."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import load_lifecycle_events

            events = load_lifecycle_events(engine.audit, ticket=str(ticket), limit=500)
            return serialize_enums(
                {
                    "available": True,
                    "ticket": str(ticket),
                    "events": [e.model_dump() for e in events],
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Timeline read failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/intelligence/autopsies")
    def get_intelligence_autopsies(
        strategy_id: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Bounded listing of trade autopsies (why did each trade win/lose)."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import list_autopsies

            rows = list_autopsies(engine.audit, strategy_id=strategy_id, limit=limit)
            return serialize_enums({"available": True, "autopsies": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Autopsy list failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/intelligence/autopsies/{ticket}")
    def get_intelligence_autopsy(ticket: str) -> dict[str, Any]:
        """Single forensic autopsy for one ticket."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import load_autopsy

            row = load_autopsy(engine.audit, ticket)
            if row is None:
                return {"available": False, "reason": "NO_AUTOPSY"}
            return serialize_enums({"available": True, "autopsy": row})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Autopsy read failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/intelligence/behavior")
    def get_intelligence_behavior(ticket: int | None = None, limit: int = 100) -> dict[str, Any]:
        """Measurable behavioral-pattern detections."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import list_behavior_detections

            rows = list_behavior_detections(engine.audit, ticket=ticket, limit=limit)
            return serialize_enums({"available": True, "detections": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Behavior list failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/intelligence/anomalies")
    def get_intelligence_anomalies(ticket: int | None = None, limit: int = 100) -> dict[str, Any]:
        """Evidence-based anomaly events (TASK-2)."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import list_anomaly_events

            rows = list_anomaly_events(engine.audit, ticket=ticket, limit=limit)
            return serialize_enums({"available": True, "anomalies": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Anomaly list failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/intelligence/evolution")
    def get_intelligence_evolution(status: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Discovered-but-unvalidated strategy evolution candidates."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import load_evolution_candidates

            rows = load_evolution_candidates(engine.audit, status=status, limit=limit)
            return serialize_enums({"available": True, "candidates": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Evolution list failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/intelligence/evolution/scan")
    def trigger_evolution_scan() -> dict[str, Any]:
        """Runs a bounded evolution discovery pass; candidates are never live."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            candidates = engine.intelligence_evolution.scan()
            return serialize_enums(
                {"available": True, "candidates": [c.model_dump() for c in candidates]}
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Evolution scan failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/intelligence/evolution/validate")
    def validate_evolution_candidate(
        candidate_id: str, backtest_expectancy_r: float, backtest_sample_count: int
    ) -> dict[str, Any]:
        """Records a backtest result; a candidate becomes VALIDATED only on positive
        evidence over a sample floor, and even then is never live until promoted."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            candidate = engine.intelligence_evolution.validate_candidate(
                candidate_id=candidate_id,
                backtest_expectancy_r=backtest_expectancy_r,
                backtest_sample_count=backtest_sample_count,
            )
            if candidate is None:
                return {"available": False, "reason": "CANDIDATE_NOT_FOUND"}
            return serialize_enums({"available": True, "candidate": candidate.model_dump()})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Evolution validate failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/intelligence/self-heal")
    def trigger_intelligence_self_heal() -> dict[str, Any]:
        """Rebuilds all derived strategy intelligence from the immutable ledger."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            count = engine.rebuild_experience_intelligence()
            return {"available": True, "rebuilt_strategies": int(count)}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Intelligence self-heal failed"})
            return _err("INTERNAL_ERROR")

    # =========================================================================
    # PHASE 09B: STRATEGY RESEARCH, BACKTEST & VALIDATION ENGINE (read + gates)
    # -------------------------------------------------------------------------
    # Research consumes the immutable experience ledger ONLY. Every endpoint is
    # bounded; validation runs live in the background worker or are triggered
    # explicitly by an operator. Research NEVER places, modifies or closes an
    # order, and a candidate can NEVER become ACTIVE automatically.
    # =========================================================================

    def _research() -> Any:
        """Returns the research pipeline when available."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "research_pipeline"):
            return None
        return engine

    @app.get("/api/research/summary")
    def get_research_summary() -> dict[str, Any]:
        """Candidate count, validation status, lifecycle distribution."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import outcome_quality_summary, registry_summary

            summary = registry_summary(engine.audit)
            summary["outcome_quality"] = outcome_quality_summary(engine.audit)
            worker = getattr(engine, "research_worker", None)
            if worker is not None:
                from nexus_scalp.research.worker import format_research_worker_status

                summary["worker"] = format_research_worker_status(worker)
            return serialize_enums({"available": True, "summary": summary})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research summary failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/registry")
    def get_research_registry(lifecycle: str | None = None, limit: int = 200) -> dict[str, Any]:
        """Bounded registry listing (validation lineage, results, score)."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import list_registry

            rows = list_registry(engine.audit, lifecycle=lifecycle, limit=limit)
            return serialize_enums({"available": True, "registry": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research registry failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/registry/{strategy_id}")
    def get_research_registry_entry(strategy_id: str) -> dict[str, Any]:
        """Single registry entry for a strategy (latest version)."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import get_registry_entry

            row = get_registry_entry(engine.audit, strategy_id)
            if row is None:
                return {"available": False, "reason": "NOT_IN_REGISTRY"}
            return serialize_enums({"available": True, "entry": row})
        except Exception as e:
            log_web_error(
                logger, "/api", None, e, context={"msg": "Research registry entry failed"}
            )
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/runs")
    def get_research_runs(strategy_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Append-only validation run records (reproducibility lineage)."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import list_research_runs

            rows = list_research_runs(engine.audit, strategy_id=strategy_id, limit=limit)
            return serialize_enums({"available": True, "runs": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research runs failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/health")
    def get_research_health() -> dict[str, Any]:
        """RESEARCH DATA HEALTH diagnostics (TASK-4).

        Explains WHY the registry is empty / populated with structured
        evidence: source trades, eligible/rejected samples, rejection reasons,
        family distribution, candidates, validation attempts, OOS/robustness
        failures, registry count, worker cycle state. Never fabricates rows.
        """
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import research_health_summary

            health = research_health_summary(
                engine.audit,
                dataset_builder=getattr(engine, "research_dataset_builder", None),
                registry=getattr(engine, "strategy_registry", None),
            )
            worker = getattr(engine, "research_worker", None)
            if worker is not None:
                from nexus_scalp.research.worker import format_research_worker_status

                health["worker"] = format_research_worker_status(worker)
            return serialize_enums({"available": True, "health": health})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research health failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/research/discover")
    def trigger_research_discovery() -> dict[str, Any]:
        """Builds the dataset + runs bounded candidate discovery.

        Candidates are NEVER live; they enter the validation pipeline only.
        """
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            dataset = engine.research_dataset_builder.build()
            candidates = engine.research_pipeline.discover(dataset)
            return serialize_enums(
                {
                    "available": True,
                    "dataset_id": dataset.dataset_id,
                    "samples": len(dataset.samples),
                    "candidates": [c.model_dump() for c in candidates],
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research discovery failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/research/validate")
    def trigger_research_validate(strategy_id: str) -> dict[str, Any]:
        """Runs the full validation gate chain for one candidate by strategy_id.

        Pipeline: backtest -> walk-forward -> OOS -> robustness -> score ->
        registry. The result can be VALIDATED or REJECTED - NEVER ACTIVE.
        """
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            dataset = engine.research_dataset_builder.build()
            candidates = engine.research_pipeline.discover(dataset)
            target = next((c for c in candidates if c.strategy_id == strategy_id), None)
            if target is None:
                # Try the registry: validate the recorded definition.
                entry = engine.strategy_registry.get(strategy_id)
                if entry is None:
                    return {"available": False, "reason": "CANDIDATE_NOT_FOUND"}
                from nexus_scalp.research.candidates import StrategyCandidate

                target = StrategyCandidate(
                    strategy_id=entry.strategy_id,
                    strategy_version=entry.strategy_version,
                    feature_schema_id=entry.feature_schema_id,
                    feature_dimension=entry.feature_dimension,
                    context_definition=entry.context_definition,
                )
            result = engine.research_pipeline.validate_candidate(target, dataset)
            return serialize_enums({"available": True, "result": result})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research validate failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/research/self-heal")
    def trigger_research_self_heal() -> dict[str, Any]:
        """Rebuilds derived research state from the immutable ledger."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import self_heal_research

            repaired = self_heal_research(engine.audit, engine.strategy_registry)
            return {"available": True, "repaired": int(repaired)}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research self-heal failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/research/repair-outcomes")
    def trigger_outcome_repair() -> dict[str, Any]:
        """
        BUG-046: repairs historical zero-R closed outcomes from broker deal
        history. Bounded, idempotent, observable. Never touches the immutable
        decision rows; only the derived outcome layer is corrected.
        """
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.experience.outcome_repair import OutcomeRepairJob

            ledger = engine.experience_ledger
            adapter = engine.adapter
            job = OutcomeRepairJob(
                ledger=ledger,
                broker_deals_fn=lambda ticket, hours_back: adapter.get_closed_deals_history(
                    symbol="XAUUSD", hours_back=hours_back
                ),
            )
            result = job.run()
            return {"available": True, "result": result.to_dict()}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Outcome repair failed"})
            return _err("INTERNAL_ERROR")

    # =========================================================================
    # PHASE 10: CONTROLLED MODEL TRAINING & CHALLENGER ENGINE (read + trigger)
    # -------------------------------------------------------------------------
    # Exposes real model-training state. Training is OFFLINE/BACKGROUND; the
    # production Champion is never touched by candidate training, and a
    # validated Challenger is never auto-promoted.
    # =========================================================================

    def _model_lifecycle() -> Any:
        """Returns the engine when the model-lifecycle subsystem is available."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "model_lifecycle_orchestrator"):
            return None
        return engine

    @app.get("/api/models/summary")
    def get_models_summary() -> dict[str, Any]:
        """Model registry status + training run counts + worker state."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            summary = engine.training_run_store.summary()
            summary["registry"] = engine.model_lifecycle_orchestrator.lifecycle_registry.summary()
            worker = getattr(engine, "training_worker", None)
            if worker is not None:
                from nexus_scalp.model_lifecycle.worker import format_training_worker_status

                summary["worker"] = format_training_worker_status(worker)
            champ = engine.champion_manager.champion_or_none()
            if champ is not None:
                summary["champion"] = champ.summary()
            else:
                summary["champion"] = {"available": False}
            return serialize_enums({"available": True, "summary": summary})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model summary failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models")
    def get_models_list(status: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Bounded model registry listing (champion/challenger/candidate...)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.model_lifecycle_orchestrator.lifecycle_registry.list_models(
                status=status, limit=limit
            )
            return serialize_enums({"available": True, "models": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model list failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/champion")
    def get_models_champion() -> dict[str, Any]:
        """Current production Champion (metadata + integrity)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            champ = engine.champion_manager.champion_or_none()
            if champ is None:
                return {"available": True, "champion": {"available": False}}
            return serialize_enums({"available": True, "champion": champ.summary()})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model champion failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/challengers")
    def get_models_challengers(limit: int = 50) -> dict[str, Any]:
        """Validated Challengers (shadow-eligible, never production)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.model_lifecycle_orchestrator.lifecycle_registry.list_models(
                status="CHALLENGER", limit=limit
            )
            return serialize_enums({"available": True, "challengers": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model challengers failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/runs")
    def get_models_runs(status: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Append-only training-run records."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.training_run_store.list_runs(status=status, limit=limit)
            return serialize_enums({"available": True, "runs": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model runs failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/runs/{run_id}")
    def get_models_run(run_id: str) -> dict[str, Any]:
        """Single training run with gates and artifacts."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            row = engine.training_run_store.get_run(run_id)
            if row is None:
                return {"available": False, "reason": "RUN_NOT_FOUND"}
            return serialize_enums({"available": True, "run": row})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model run failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/comparison/{run_id}")
    def get_models_comparison(run_id: str) -> dict[str, Any]:
        """Champion vs Challenger comparison for a training run."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            row = engine.training_run_store.get_comparison(run_id)
            if row is None:
                return {"available": False, "reason": "NO_COMPARISON"}
            return serialize_enums({"available": True, "comparison": row})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model comparison failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/models/train")
    def trigger_model_training(num_epochs: int = 10) -> dict[str, Any]:
        """Runs ONE controlled training pass (candidate only, never Champion).

        Triggers the pipeline synchronously for operator use; the background
        worker handles scheduled training. Heavy CPU work is off the event loop.
        """
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            orchestrator = engine.model_lifecycle_orchestrator
            dataset = orchestrator.build_training_dataset(
                include_no_trade=True, weight_no_trade=0.25, only_executed=True
            )
            if dataset.sample_count < 50:
                return {
                    "available": False,
                    "reason": "INSUFFICIENT_SAMPLES",
                    "samples": dataset.sample_count,
                }
            import asyncio

            result = asyncio.run(_run_training_async(orchestrator, dataset, num_epochs))
            return serialize_enums({"available": True, "result": result})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Model training trigger failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/models/worker/start")
    def start_training_worker() -> dict[str, Any]:
        """Starts the background training worker (idempotent, isolated)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            engine._start_training_worker()
            return {"available": True, "started": engine._training_worker_started}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Training worker start failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/models/worker/stop")
    def stop_training_worker() -> dict[str, Any]:
        """Stops the background training worker (idempotent)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            import asyncio

            asyncio.run(engine._stop_training_worker())
            return {"available": True, "stopped": not engine._training_worker_started}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Training worker stop failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/models/worker/cancel")
    def cancel_training_worker() -> dict[str, Any]:
        """Requests cancellation of any in-flight training (bounded, safe)."""
        engine = _model_lifecycle()
        if engine is None:
            return {"available": False}
        try:
            engine.training_worker.request_cancel()
            return {"available": True}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Training worker cancel failed"})
            return _err("INTERNAL_ERROR")

    # =========================================================================
    # PHASE 11: CHALLENGER SHADOW TRADING & CHAMPION EVALUATION (read + control)
    # -------------------------------------------------------------------------
    # Shadow evaluation is SHADOW-ONLY: the Challenger has zero order authority,
    # every result is marked SHADOW/SIMULATED, and the production Champion is
    # never modified. A Challenger can never be auto-promoted here.
    # =========================================================================

    def _shadow() -> Any:
        """Returns the engine when the shadow subsystem is available."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "shadow_engine"):
            return None
        return engine

    @app.get("/api/models/shadow/summary")
    def get_shadow_summary() -> dict[str, Any]:
        """Shadow runs + decisions + promotions + worker + active challenger."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            summary = engine.shadow_store.summary()
            worker = getattr(engine, "shadow_worker", None)
            if worker is not None:
                from nexus_scalp.shadow.worker import format_shadow_worker_status

                summary["worker"] = format_shadow_worker_status(worker)
            summary["active_challenger"] = (
                engine._shadow_challenger.summary() if engine._shadow_challenger else None
            )
            summary["active_run"] = engine.shadow_engine.current_evidence()
            return serialize_enums({"available": True, "summary": summary})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow summary failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/shadow/runs")
    def get_shadow_runs(limit: int = 50) -> dict[str, Any]:
        """Append-only shadow run history."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.shadow_store.list_runs(limit=limit)
            return serialize_enums({"available": True, "runs": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow runs failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/shadow/decisions")
    def get_shadow_decisions(run_id: str | None = None, limit: int = 200) -> dict[str, Any]:
        """Shadow decision records (all marked simulated)."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.shadow_store.list_decisions(run_id=run_id, limit=limit)
            return serialize_enums({"available": True, "decisions": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow decisions failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/shadow/compare/{run_id}")
    def get_shadow_compare(run_id: str) -> dict[str, Any]:
        """Multi-dimension Champion vs Challenger comparison for a shadow run."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            row = engine.shadow_store.get_comparison(run_id)
            if row is None:
                return {"available": False, "reason": "NO_COMPARISON"}
            return serialize_enums({"available": True, "comparison": row})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow compare failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/shadow/promotion/{run_id}")
    def get_shadow_promotion(run_id: str) -> dict[str, Any]:
        """Promotion evaluation (eligibility + vetoes) for a shadow run."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            row = engine.shadow_store.get_promotion(run_id)
            if row is None:
                return {"available": False, "reason": "NO_PROMOTION"}
            return serialize_enums({"available": True, "promotion": row})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow promotion failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/models/shadow/attach")
    def attach_shadow_challenger() -> dict[str, Any]:
        """Attaches a validated Challenger artifact for shadow evaluation.

        The Challenger is loaded with full integrity checks; an invalid or
        schema-incompatible artifact is SHADOW_LOAD_FAILED and never used.
        """
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.governance.shadow_runtime import GovernanceShadowRuntime
            from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry
            from nexus_scalp.shadow.challenger import load_challenger

            # Find the most recent CHALLENGER registry row.
            lifecycle = ModelLifecycleRegistry(
                audit_repo=engine.audit,
                model_registry=engine.model_registry,
            )
            challengers = lifecycle.list_models(status="CHALLENGER", limit=5)
            if not challengers:
                return {"available": False, "reason": "NO_VALIDATED_CHALLENGER"}
            row = challengers[0]
            artifact_path = row.get("artifact_path", "")
            model_id = row.get("model_id", "")
            model_version = row.get("model_version", "")
            if not artifact_path:
                return {"available": False, "reason": "CHALLENGER_ARTIFACT_MISSING"}
            from pathlib import Path

            path = Path(artifact_path)
            if not path.exists():
                return {"available": False, "reason": "CHALLENGER_ARTIFACT_NOT_FOUND"}
            scaler = Path(str(path) + ".scaler.npz")
            # TASK-6: the deterministic 10-gate load gate MUST pass before
            # any Challenger enters the shadow runtime (spec 4). A
            # rejected model is never loaded; the failing gate is reported.
            from nexus_scalp.governance.load_gate import ModelLoadGate, read_manifest_file

            manifest = read_manifest_file(Path(artifact_path).parent / "model.json") or {}
            gate = ModelLoadGate(db_path=engine.audit._db_path if engine.audit else None).evaluate(
                artifact_path=path,
                scaler_path=scaler,
                model_id=model_id,
                model_version=model_version,
                manifest=manifest,
                lifecycle_state=row.get("lifecycle_status", ""),
            )
            if not gate.passed:
                return {
                    "available": False,
                    "reason": "MODEL_LOAD_REJECTED",
                    "failing_gate": gate.failing_gate.value if gate.failing_gate else "",
                }
            runtime = load_challenger(
                artifact_path=path,
                scaler_path=scaler,
                model_id=model_id,
                model_version=model_version,
                live_schema_id=engine.FEATURE_SCHEMA_ID,
                live_dimension=engine.FEATURE_DIM,
            )
            engine._shadow_challenger = runtime
            engine.shadow_engine.attach_challenger(runtime)
            # TASK-6: wire the governance shadow runtime (same-input
            # alignment + parity + latency + failure isolation).
            engine._governance_shadow = GovernanceShadowRuntime(
                runtime=runtime,
                store=engine.governance_store,
            )
            # Start a fresh shadow run bound to this challenger.
            from nexus_scalp.shadow.models import ShadowModelRef

            champ = engine.champion_manager.champion_or_none()
            champ_ref = (
                ShadowModelRef(
                    model_id=champ.model_id,
                    model_version=champ.model_version,
                    feature_schema_id=champ.feature_schema_id,
                    feature_dimension=champ.feature_dimension,
                    artifact_hash=champ.artifact_hash,
                    is_champion=True,
                )
                if champ
                else None
            )
            run_id = engine.shadow_engine.start_run(
                run_id=None,
                champion=champ_ref or ShadowModelRef(model_id="none", model_version=""),
                challenger_ref=runtime.ref or ShadowModelRef(model_id="none", model_version=""),
            )
            return serialize_enums(
                {
                    "available": True,
                    "challenger": runtime.summary(),
                    "run_id": run_id,
                }
            )
        except Exception as e:
            _log_err(e, "Shadow attach failed", endpoint="/api/models/shadow/attach")
            return _err("OPERATION_FAILED", extra={"reason": "SHADOW_LOAD_FAILED"})

    @app.post("/api/models/shadow/evaluate-promotion")
    def evaluate_shadow_promotion(run_id: str) -> dict[str, Any]:
        """Computes the explainable promotion evaluation + vetoes for a run."""
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            comparison_row = engine.shadow_store.get_comparison(run_id)
            if comparison_row is None:
                return {"available": False, "reason": "NO_COMPARISON"}
            import json as _json

            payload = _json.loads(comparison_row.get("payload") or "{}")
            from nexus_scalp.shadow.models import ShadowComparison

            comparison = ShadowComparison.model_validate(payload)
            evaluation = engine.shadow_engine.comparer.evaluate_promotion(comparison)
            engine.shadow_store.save_promotion(evaluation)
            return serialize_enums(
                {"available": True, "evaluation": evaluation.model_dump(mode="json")}
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow promotion eval failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/models/shadow/worker/start")
    def start_shadow_worker() -> dict[str, Any]:
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            engine._start_shadow_worker()
            return {"available": True, "started": engine._shadow_worker_started}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow worker start failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/models/shadow/worker/stop")
    def stop_shadow_worker() -> dict[str, Any]:
        engine = _shadow()
        if engine is None:
            return {"available": False}
        try:
            import asyncio

            asyncio.run(engine._stop_shadow_worker())
            return {"available": True, "stopped": not engine._shadow_worker_started}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow worker stop failed"})
            return _err("INTERNAL_ERROR")

    def _governance() -> Any:
        """Returns the governance engine or None (safe)."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "governance_engine"):
            return None
        return engine

    @app.get("/api/models/governance/health")
    def get_governance_health() -> dict[str, Any]:
        """Truthful model-governance runtime health (spec 27)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            health = engine._governance_snapshot_health()
            return serialize_enums({"available": True, "health": health})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Governance health failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/governance/registry")
    def get_governance_registry() -> dict[str, Any]:
        """Truthful registry reconciliation (spec 3). Read-only."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            snapshot = engine.governance_engine.registry_snapshot(
                audit_db=engine.audit._db_path if engine.audit else "",
                champion_id=engine.champion_manager.model_id,
                champion_artifact=engine.config.model.model_artifact_path,
            )
            return serialize_enums({"available": True, "registry": snapshot})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Governance registry failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/models/registry/reconcile")
    def reconcile_registry() -> dict[str, Any]:
        """Makes the registry truthful about the CURRENT Champion (spec 3)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            engine._sync_champion_registry_state()
            snapshot = engine.governance_engine.registry_snapshot(
                audit_db=engine.audit._db_path if engine.audit else "",
                champion_id=engine.champion_manager.model_id,
                champion_artifact=engine.config.model.model_artifact_path,
            )
            return serialize_enums({"available": True, "registry": snapshot})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Registry reconcile failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/governance/events")
    def get_governance_events(limit: int = 200, event: str = "") -> dict[str, Any]:
        """Append-only governance event ledger (spec 30 / 31)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.governance_store.list_events(limit=limit, event=event)
            return serialize_enums({"available": True, "events": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Governance events failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/governance/comparisons")
    def get_governance_comparisons(limit: int = 200, run_id: str = "") -> dict[str, Any]:
        """Canonical shadow comparison rows (spec 9 / 14)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            rows = engine.governance_store.list_comparisons(limit=limit, run_id=run_id)
            return serialize_enums({"available": True, "comparisons": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Governance comparisons failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/models/shadow/outcomes")
    def link_shadow_outcomes(run_id: str = "", horizon_bars: int = 15) -> dict[str, Any]:
        """Links shadow decisions to eventual outcomes (spec 16)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.governance.evidence import outcome_for_decision

            ids = []
            rows = engine.shadow_store.list_decisions(run_id=run_id, limit=2000)
            for r in rows:
                payload = {}
                try:
                    import json as _j

                    payload = _j.loads(r.get("payload") or "{}")
                except Exception:
                    payload = {}
                decision = dict(r)
                entry = payload.get("hypothetical_entry", 0.0) if isinstance(payload, dict) else 0.0
                decision["entry_price"] = entry or 0.0
                decision["decision_id"] = r.get("decision_id", "")
                outcome = outcome_for_decision(
                    decision=decision,
                    audit_db=engine.audit._db_path if engine.audit else None,
                    horizon_bars=max(1, min(int(horizon_bars), 60)),
                )
                ids.append(
                    {"shadow_decision_id": r.get("shadow_decision_id", ""), "outcome": outcome}
                )
            linked_count = sum(1 for x in ids if x["outcome"].get("linkage_state") == "LINKED")
            return serialize_enums(
                {"available": True, "linked": linked_count, "total": len(ids), "outcomes": ids}
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Shadow outcomes failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/models/governance/review")
    def get_governance_review() -> dict[str, Any]:
        """Live calibration + drift + backtest-vs-live divergence evidence."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.governance.evidence import (
                backtest_live_divergence,
                brier_score,
                calibration_buckets,
                detect_drift,
                ece_score,
            )

            rows = engine.governance_store.list_comparisons(limit=3000)
            cal_rows = []
            probs_window = []
            actions = []
            for r in rows:
                try:
                    import json as _j2

                    cp = _j2.loads(r.get("champion_probabilities") or "[]")
                    chp = _j2.loads(r.get("challenger_probabilities") or "[]")
                except Exception:
                    cp, chp = [], []
                if cp:
                    probs_window.append(cp)
                    cal_rows.append({"confidence": max(cp), "correct": True})
                if chp:
                    probs_window.append(chp)
                    cal_rows.append({"confidence": max(chp), "correct": True})
                actions.append(str(r.get("champion_action", "NO_TRADE")))
                actions.append(str(r.get("challenger_action", "NO_TRADE")))

            buckets = calibration_buckets(cal_rows)
            drift = detect_drift(
                probs_window=probs_window[:300],
                actions=actions[:300],
                model_id="shadow",
            )
            divergence = backtest_live_divergence(
                backtest_accuracy=None,
                backtest_expectancy_r=None,
                live_samples=len(rows),
            )
            return serialize_enums(
                {
                    "available": True,
                    "calibration": {
                        "buckets": [b.model_dump(mode="json") for b in buckets],
                        "brier": brier_score(cal_rows),
                        "ece": ece_score(buckets),
                    },
                    "drift": [a.model_dump(mode="json") for a in drift],
                    "divergence": divergence,
                    "samples": len(rows),
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Governance review failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/models/promotion/approve")
    def approve_promotion(payload: dict[str, Any]) -> dict[str, Any]:
        """Operator approval for READY_FOR_REVIEW -> APPROVED (spec 21/22)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.governance.engine import PromotionGateError

            actor = str(payload.get("actor", "") or "").strip()
            model_id = str(payload.get("model_id", "") or "")
            model_version = str(payload.get("model_version", "") or "")
            reason = str(payload.get("reason", "") or "")
            if not actor or not model_id:
                return _err("PROMOTION_BLOCKED", extra={"reason": "actor and model_id required"})
            transition = engine.governance_engine.approve(
                model_id=model_id,
                model_version=model_version,
                actor=actor,
                reason=reason or "operator approval",
                evidence={"operator": actor, "source": "api"},
            )
            return serialize_enums(
                {"available": True, "transition": transition.model_dump(mode="json")}
            )
        except PromotionGateError as e:
            # BUG-040: never leak raw exception text to clients; log full
            # detail server-side only.
            log_web_error(
                logger,
                "/api",
                None,
                e,
                context={"msg": "Promotion gate blocked"},
            )
            return _err("PROMOTION_BLOCKED", extra={"reason": "promotion gate blocked"})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Promotion approve failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/models/promotion/rollback")
    def rollback_promotion(payload: dict[str, Any]) -> dict[str, Any]:
        """Operator rollback to the previous Champion (spec 23)."""
        engine = _governance()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.governance.engine import PromotionGateError

            actor = str(payload.get("actor", "") or "").strip()
            failed_id = str(payload.get("failed_model_id", "") or "")
            failed_version = str(payload.get("failed_version", "") or "")
            previous_id = str(payload.get("previous_model_id", "") or "")
            previous_version = str(payload.get("previous_version", "") or "")
            reason = str(payload.get("reason", "") or "")
            if not actor or not failed_id or not previous_id:
                return _err(
                    "PROMOTION_BLOCKED",
                    extra={"reason": "actor, failed_model_id and previous_model_id required"},
                )
            transition = engine.governance_engine.rollback(
                failed_model_id=failed_id,
                failed_version=failed_version,
                previous_model_id=previous_id,
                previous_version=previous_version,
                actor=actor,
                reason=reason or "operator rollback",
                previous_artifact=engine.config.model.model_artifact_path,
            )
            return serialize_enums(
                {"available": True, "transition": transition.model_dump(mode="json")}
            )
        except PromotionGateError as e:
            # BUG-040: never leak raw exception text to clients; log full
            # detail server-side only.
            log_web_error(
                logger,
                "/api",
                None,
                e,
                context={"msg": "Promotion gate blocked"},
            )
            return _err("PROMOTION_BLOCKED", extra={"reason": "promotion gate blocked"})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Promotion rollback failed"})
            return _err("INTERNAL_ERROR")

    def _intelligence_worker_status(worker: Any) -> dict[str, Any]:
        from nexus_scalp.intelligence.worker import format_intelligence_worker_status

        if worker is None:
            return {}
        try:
            return format_intelligence_worker_status(worker)
        except Exception as e:
            log_web_error(
                logger, "/api", None, e, context={"msg": "Intelligence worker status failed"}
            )
            return {}

    # =========================================================================
    # PHASE 12: NEWS INTELLIGENCE API (read + control, isolated subsystem)
    # -------------------------------------------------------------------------
    # Every route reads the dedicated news.db via the NewsEngine. When the
    # news subsystem is disabled/unavailable, routes return available=False;
    # they never fabricate data and never affect trading.
    # =========================================================================

    def _news() -> Any:
        engine = app.state.engine
        if not engine or not getattr(engine, "news_engine", None):
            return None
        return engine.news_engine

    @app.get("/api/news")
    def get_news(limit: int = 50, include_duplicates: bool = False) -> dict[str, Any]:
        """Live news feed (canonical articles)."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            rows = news.db.list_articles(limit=limit, include_duplicates=include_duplicates)
            from nexus_scalp.news.analysis.keywords import keyword_hits_for_article

            out = []
            for r in rows:
                analysis = news.db.get_analysis(r["article_id"])
                consensus = news.db.get_consensus(r["article_id"])
                out.append(
                    {
                        "article_id": r["article_id"],
                        "title": r["title"],
                        "summary": r["summary"],
                        "source_id": r["source_id"],
                        "source_name": r["source_name"],
                        "published_at": r["published_at"],
                        "importance": r["importance"],
                        "importance_score": r["importance_score"],
                        "is_duplicate": bool(r["is_duplicate"]),
                        "evidence_sources": r["evidence_sources"],
                        "analysis": analysis,
                        "consensus": consensus,
                        "keyword_hits": keyword_hits_for_article(r),
                    }
                )
            return {"available": True, "articles": out}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "News feed failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/latest")
    def get_news_latest(limit: int = 10) -> dict[str, Any]:
        """Latest canonical news articles."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            rows = news.db.list_articles(limit=limit, include_duplicates=False)
            return {
                "available": True,
                "articles": [
                    {
                        "article_id": r["article_id"],
                        "title": r["title"],
                        "source_name": r["source_name"],
                        "published_at": r["published_at"],
                        "importance": r["importance"],
                        "importance_score": r["importance_score"],
                    }
                    for r in rows
                ],
            }
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/impact")
    def get_news_impact(asset: str = "XAUUSD", limit: int = 50) -> dict[str, Any]:
        """Recent impact records for an asset (XAUUSD default)."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            rows = news.db.list_recent_impacts(asset=asset, limit=limit)
            return {"available": True, "impacts": rows}
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/timeline")
    def get_news_timeline(
        bucket_sec: int = 900, hours_back: int = 24, asset: str = "XAUUSD"
    ) -> dict[str, Any]:
        """Impact timeline aggregated into time buckets for the chart.

        bucket_sec map: 900 = 15m, 3600 = 1h, 14400 = 4h, 86400 = 1d.
        Returns buckets with bullish/bearish/neutral impact sums per bucket.
        """
        news = _news()
        if news is None:
            return {"available": False}
        try:
            buckets = news.db.impact_timeline(
                bucket_sec=bucket_sec, hours_back=hours_back, asset=asset
            )
            return {"available": True, "asset": asset, "buckets": buckets}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "News timeline failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/state")
    def get_news_state() -> dict[str, Any]:
        """Current news state (NORMAL/ELEVATED/HIGH_IMPACT/CONFLICTED/
        BREAKING/STALE) from the cached live context."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            ctx = news.current_context(force=True)
            return {
                "available": True,
                "state": ctx.state.value,
                "timestamp": ctx.timestamp.isoformat(),
                "bullish_score": ctx.bullish_score,
                "bearish_score": ctx.bearish_score,
                "confidence": ctx.confidence,
                "conflict_score": ctx.conflict_score,
                "freshness": ctx.freshness,
                "xauusd_relevance": ctx.xauusd_relevance,
                "usd_relevance": ctx.usd_relevance,
                "active_event_count": ctx.active_event_count,
                "stale": ctx.stale,
                "news_adjustment": ctx.news_adjustment,
                "active_high_impact": ctx.active_high_impact,
            }
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/sources")
    def get_news_sources(enabled_only: bool = False) -> dict[str, Any]:
        """Source registry + health."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            sources = news.db.list_sources(enabled_only=enabled_only)
            health = news.db.list_health()
            health_by_id = {h["source_id"]: h for h in health}
            for s in sources:
                s["health"] = health_by_id.get(s["source_id"])
            return {"available": True, "sources": sources}
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/health")
    def get_news_health() -> dict[str, Any]:
        """News subsystem health + worker telemetry."""
        news = _news()
        if news is None:
            return {"available": False, "enabled": False}
        try:
            health = news.health()
            engine = app.state.engine
            worker_status = None
            if engine and getattr(engine, "news_worker", None) is not None:
                from nexus_scalp.news.worker import format_news_worker_status

                worker_status = format_news_worker_status(engine.news_worker)
            return {"available": True, "enabled": True, "health": health, "worker": worker_status}
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/analysis/{article_id}")
    def get_news_analysis(article_id: str) -> dict[str, Any]:
        """Single article analysis."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            analysis = news.db.get_analysis(article_id)
            run = None
            if analysis:
                run = news.db.get_run(analysis["run_id"])
            return {"available": True, "analysis": analysis, "run": run}
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/trades/{trade_id}")
    def get_news_trade_links(trade_id: str) -> dict[str, Any]:
        """News links for one trade."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            links = news.db.list_trade_links(trade_id=trade_id)
            return {"available": True, "trade_id": trade_id, "links": links}
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.post("/api/news/analyze/{article_id}")
    def post_news_analyze(article_id: str) -> dict[str, Any]:
        """AI Analyze: enqueue a background analysis job (never blocks)."""
        news = _news()
        if news is None:
            return {"available": False}
        engine = app.state.engine
        try:
            if engine and getattr(engine, "news_worker", None) is not None:
                job = engine.news_worker.enqueue_analysis(article_id, priority=0.9)
                return {"available": True, **job}
            result = news.analyze_article_id(article_id)
            return {"available": True, **result}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "News analyze failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/news/refresh")
    def post_news_refresh() -> dict[str, Any]:
        """Trigger one ingestion + analysis pass (bounded).

        BANDWIDTH GUARD (2026-08-18): rapid clicks on "Fetch News" used to
        trigger a full multi-source re-fetch EVERY time (the fetcher has no
        shared cooldown). A per-server minimum interval is enforced here so
        repeated clicks within 60s return the cached result instead of
        hammering the RSS endpoints.
        """
        news = _news()
        if news is None:
            return {"available": False}
        try:
            now = time.monotonic()
            with app.state.news_refresh_lock:
                last = app.state.news_refresh_ts
                if now - last < 60.0:
                    remaining = int(60.0 - (now - last))
                    return {
                        "available": True,
                        "cooldown": remaining,
                        "ingested": {"sources_polled": 0, "new": 0, "duplicate": 0, "merged": 0},
                        "analyzed_count": 0,
                        "skipped": f"refresh cooldown active ({remaining}s)",
                    }
                app.state.news_refresh_ts = now
            ingest = news.ingest_cycle(max_sources=8)
            analyzed = news.analysis_cycle(limit=10)
            return {
                "available": True,
                "ingested": ingest,
                "analyzed_count": len(analyzed),
                "cooldown": 0,
            }
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.post("/api/news/self-heal")
    def post_news_self_heal() -> dict[str, Any]:
        """Rebuild derived news state from raw articles."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            return {"available": True, **news.self_heal()}
        except Exception:
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/keywords")
    def get_news_keywords(top_n: int = 25, category: str = "", q: str = "") -> dict[str, Any]:
        """Keyword analysis dataset: full library + live corpus coverage.

        The dataset is the deterministic keyword backbone of the local news
        analysis pipeline (200+ keywords across currencies, assets,
        institutions, macro topics, XAUUSD drivers, directional phrases,
        geopolitics, energy and FX pairs). Returns:
            * dataset meta (version, total_keywords, categories),
            * corpus coverage (articles scanned, total mentions, active
              keywords, direction distribution),
            * top keyword coverage (hits, share, category, bias),
            * optional filterable full listing (category / q).
        """
        news = _news()
        if news is None:
            return {"available": False}
        try:
            from nexus_scalp.news.analysis.keywords import (
                analyze_keyword_coverage,
                categories,
                get_keyword_dataset,
                keyword_count,
            )

            articles = news.db.list_articles(limit=500, include_duplicates=False)
            coverage = analyze_keyword_coverage(articles, top_n=top_n)

            listing = []
            for k in get_keyword_dataset():
                if category and k.category != category:
                    continue
                if q and q.lower() not in k.keyword.lower():
                    continue
                listing.append(
                    {
                        "keyword": k.keyword,
                        "category": k.category,
                        "topics": [t.value for t in k.topics],
                        "direction_bias": k.direction_bias.value,
                        "weight": k.weight,
                        "aliases": list(k.aliases),
                    }
                )

            return {
                "available": True,
                "dataset": {
                    "version": coverage.dataset_version,
                    "total_keywords": keyword_count(),
                    "categories": categories(),
                },
                "coverage": {
                    "articles_scanned": coverage.total_articles_scanned,
                    "total_mentions": coverage.total_mentions,
                    "active_keywords": coverage.active_keywords,
                    "direction_distribution": coverage.direction_distribution,
                    "top_keywords": [
                        {
                            "keyword": c.keyword,
                            "category": c.category,
                            "direction_bias": c.direction_bias.value,
                            "weight": c.weight,
                            "article_hits": c.article_hits,
                            "mention_count": c.mention_count,
                            "share": c.share,
                        }
                        for c in coverage.top_keywords
                    ],
                },
                "keywords": listing,
            }
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "News keywords failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/news/{article_id}")
    def get_news_detail(article_id: str) -> dict[str, Any]:
        """News detail view: article + analysis + impacts + consensus +
        related + trade links + post-event records."""
        news = _news()
        if news is None:
            return {"available": False}
        try:
            art = news.db.get_article(article_id)
            if not art:
                return {"available": False, "error": "ARTICLE_NOT_FOUND"}
            analysis = news.db.get_analysis(article_id)
            impacts = news.db.get_impacts(article_id)
            consensus = news.db.get_consensus(article_id)
            entities = news.db.get_entities(article_id)
            topics = news.db.get_topics(article_id)
            related = news.db.list_related(article_id, limit=10)
            trade_links = news.db.list_article_trade_links(article_id)
            versions = news.db.latest_version(article_id)
            post_events = news.validator.list_records(article_id=article_id, limit=10)
            return {
                "available": True,
                "article": art,
                "analysis": analysis,
                "impacts": impacts,
                "consensus": consensus,
                "entities": entities,
                "topics": topics,
                "related": related,
                "trade_links": trade_links,
                "versions": [versions] if versions else [],
                "post_event_validation": post_events,
            }
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "News detail failed"})
            return _err("INTERNAL_ERROR")

    # Server-Sent Events (SSE) telemetry stream
    @app.get("/api/ticks/stream")
    async def sse_telemetry_stream(request: Request) -> StreamingResponse:
        """Asynchronous SSE streamer providing zero-latency live telemetry.

        Protocol (LiveUiState.2):
          - `event: state`  -> full canonical snapshot (used on connect and
            every 30 heartbeats so a paused engine still refreshes the UI).
          - `event: tick`   -> incremental update carrying state_version +
            snapshot_timestamp + the changed top-level sections; the UI MERGES
            it into its current snapshot instead of replacing the whole DOM.
          - `event: heartbeat` -> `{}` keepalive every 5s.
        Every payload carries the monotonic state_version; the UI drops any
        version <= the last seen one (out-of-order guard).
        """

        async def event_generator():
            last_full: int = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = get_system_state()
                    version = int(payload.get("state_version") or 0)
                    # Emit a full-state event at connect, on first tick after
                    # an idle gap, and every 30 cycles (heartbeat cadence).
                    is_full = version <= last_full or last_full == 0 or (version - last_full) > 30
                    if is_full:
                        event_name = "state"
                        last_full = version
                    else:
                        event_name = "tick"
                        # Incremental: drop the heavyweight lists the UI keeps
                        # between full snapshots (bars/features/predictions).
                        payload = dict(payload)
                        payload.pop("bars", None)
                        payload.pop("features", None)
                        payload.pop("predictions", None)

                    frame = json.dumps(payload)
                    yield f"event: {event_name}\ndata: {frame}\n\n"
                    # Keep a bounded replay ring for reconnect resynchronization.
                    app.state.stream_history.append(
                        {"event": event_name, "version": version, "frame": frame}
                    )

                    # Broadcast to active WebSocket clients too.
                    for ws in list(active_connections):
                        try:
                            await ws.send_json(
                                {"event": event_name, **payload}
                                if event_name == "tick"
                                else payload
                            )
                        except Exception:
                            active_connections.discard(ws)
                except Exception as e:
                    log_web_error(
                        logger, "/api", None, e, context={"msg": "SSE stream serialization warning"}
                    )

                await asyncio.sleep(0.2)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return app
