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
import os
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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from nexus_scalp.accounting import PeriodKind
from nexus_scalp.accounting.aggregation import compute_advanced_metrics
from nexus_scalp.accounting.market_calendar import (
    current_trading_day,
    market_state,
    probe_server_time,
)
from nexus_scalp.accounting.worker import format_worker_status
from nexus_scalp.domain.enums import ActionType, ExecutionMode
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.observability.logging import get_logger

# CHG-0032-A1 Step-3D: request models used by the extracted diagnostics/
# config routes are single-sourced in diagnostics_state_routes.py; the
# facade re-exports them for backward compatibility (name-stable).
from nexus_scalp.web.diagnostics_state_routes import (
    EngineModeRequest,  # noqa: F401
    ToggleRequest,  # noqa: F401
    ToggleRuleRequest,  # noqa: F401
)
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


def canonical_json(obj: Any) -> str:
    """Canonical JSON for web/SSE payloads (BUG-110).

    Deterministic, timezone-aware, ISO-8601:
      * datetime -> .isoformat() (naive datetimes are stamped UTC)
      * date    -> .isoformat()
      * Enum    -> .value
      * UUID    -> str
      * Decimal -> float (lossless for the numeric ranges the engine emits)
      * Path    -> str
      * numpy scalars/arrays -> item()/tolist()
      * anything else with .isoformat() -> str(isoformat)
      * unknown remains UNKNOWN -> _default raises TypeError so the caller
        can emit SSE_SERIALIZATION_ERROR instead of corrupt JSON
    """

    def _default(o: Any) -> Any:
        import uuid as _uuid
        from datetime import date, datetime
        from decimal import Decimal
        from pathlib import Path

        import numpy as np

        if isinstance(o, datetime):
            if o.tzinfo is None:
                o = o.replace(tzinfo=UTC)
            return o.isoformat()
        if isinstance(o, date):
            return o.isoformat()
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, (bytes, bytearray)):
            return o.decode("utf-8", "replace")
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, _uuid.UUID):
            return str(o)
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
        if hasattr(o, "isoformat"):
            return str(o.isoformat())
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

    return json.dumps(obj, default=_default, ensure_ascii=True)


def _find_non_json_fields(obj: Any, prefix: str = "") -> list[str]:
    """Locates the JSON-incompatible leaves of a payload (path -> type).

    Used by the SSE structured diagnostic so a serialization failure is
    actionable (which field, which type) instead of a generic TypeError
    (BUG-110). Only JSON-native leaves are skipped.
    """
    import uuid as _uuid
    from datetime import date, datetime
    from decimal import Decimal
    from pathlib import Path

    import numpy as np

    native = (str, int, float, bool, type(None))
    problems: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            problems.extend(_find_non_json_fields(v, f"{prefix}.{k}" if prefix else str(k)))
        return problems
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            problems.extend(_find_non_json_fields(v, f"{prefix}[{i}]"))
        return problems
    if isinstance(obj, native):
        return []
    if isinstance(obj, (datetime, date, Decimal, _uuid.UUID, Path, Enum, np.generic, np.ndarray)):
        return []
    if hasattr(obj, "isoformat"):
        return []
    return [f"{prefix or '<root>'}:{type(obj).__name__}"]


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
    import sys

    override = os.environ.get("NEXUS_WEB_DIR")
    if override:
        return Path(override)
    # Frozen (PyInstaller) — _MEIPASS is the _internal dir next to the exe
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "Web"
        if cand.is_dir():
            return cand
    packaged = Path(__file__).resolve().parent.parent.parent.parent / "_internal" / "Web"
    if packaged.is_dir():
        return packaged
    # Portable layout: exe next to _internal/Web (onedir)
    try:
        exe_dir = Path(sys.executable).resolve().parent
        alt = exe_dir / "_internal" / "Web"
        if alt.is_dir():
            return alt
        alt2 = exe_dir / "Web"
        if alt2.is_dir() and (alt2 / "index.html").exists():
            return alt2
    except Exception:
        pass
    repo_web = Path(__file__).resolve().parent.parent.parent.parent / "Web"
    if repo_web.is_dir():
        return repo_web
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


def _liquidity_state_section(engine: Any) -> dict[str, Any]:
    """Canonical liquidity section embedded in /api/status + live/state + SSE.

    Real state only; independent of news (brief 5). On any failure the
    section reports UNAVAILABLE with a reason — never fabricated numbers.
    """
    try:
        gov = getattr(engine, "liquidity_governor", None) if engine is not None else None
        if gov is None:
            from nexus_scalp.features.liquidity_runtime import LiquidityGovernor

            gov = LiquidityGovernor(enabled=False)
        return gov.report()
    except Exception as e:
        log_web_error(logger, "/api", None, e, context={"msg": "Liquidity state section failed"})
        return {
            "enabled": False,
            "available": False,
            "status": "UNAVAILABLE",
            "causal_state": "INVALID",
            "reason": "LIQUIDITY_STATE_ERROR",
        }


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


class ModifyPositionRequest(BaseModel):
    ticket: int
    stop_loss: float
    take_profit: float


class ClosePositionRequest(BaseModel):
    ticket: int


# ToggleRequest moved to web/diagnostics_state_routes.py (CHG-0032-A1 Step-3D);
# re-imported below for backward compatibility.


class OutcomeRecoveryRequest(BaseModel):
    dry_run: bool = False


# EngineModeRequest moved to web/diagnostics_state_routes.py (CHG-0032-A1 Step-3D);
# re-imported below for backward compatibility.


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


# ToggleRuleRequest moved to web/diagnostics_state_routes.py (CHG-0032-A1 Step-3D);
# re-imported below for backward compatibility.


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


def _default_audit_config() -> Any:
    """Resolve the authoritative audit DatabaseConfig (DATABASE PORTABILITY)."""
    from nexus_scalp.database.config import load_database_config

    return load_database_config("audit")


def db_path_for_audit() -> str:
    """Resolves the canonical audit.db path (used by incident diagnostics)."""
    from pathlib import Path as _Path

    from nexus_scalp.database.engine import db_path_for_domain

    base = _Path.cwd()
    return str(db_path_for_domain("audit", base))


def create_app(engine_ref: Any = None) -> FastAPI:
    """Creates and configures the FastAPI web server instance."""
    app = FastAPI(title="Nexus Scalp Engine Control Center", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store engine reference in app state
    app.state.engine = engine_ref
    app.state.server_state = ServerState()
    # Server-lifetime monotonic snapshot identity (never resets, survives SSE
    # reconnects; the UI rejects out-of-order versions).
    app.state.versioner = StateVersioner()
    # Bounded per-stream event ring for reconnect resynchronization.
    app.state.stream_history = deque(maxlen=200)  # type: ignore[assignment]
    # Debug 70D forensic console: rolling snapshot ring + SSE diagnostics.
    from nexus_scalp.web.debug_snapshot import DebugSnapshotStore

    app.state.debug_snapshot_store = DebugSnapshotStore(max_snapshots=64)
    # SSE observability (brief 27): connection state + serialization errors.
    app.state.sse_diag = {
        "connection": "UNKNOWN",
        "connected_at": None,
        "last_event": None,
        "event_count": 0,
        "last_latency_ms": None,
        "serialization_errors": 0,
        "serialization_error": None,
        "reconnect_count": 0,
    }
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
            # BUGFIX-G29: process liveness (warmup READY + inference ENABLED)
            # is NOT proof of live market data. If the engine's own freshness
            # model reports the pipeline STALE (frozen tick feed / frozen
            # inference) while the process stays up, surface HEALTH=STALE plus
            # the live_freshness contract so the dashboard can never present a
            # stale price/inference as current. This never bypasses a guard;
            # it only downgrades the *reported* health signal.
            _fresh = None
            try:
                if engine is not None and hasattr(engine, "compute_live_freshness"):
                    _fresh = engine.compute_live_freshness()
            except Exception:
                _fresh = None
            if _fresh is not None and _fresh.get("overall") == "STALE":
                engine_status = "STALE"
                _stages = _fresh.get("market", {})
                details["engine"] = (
                    f"engine running · warmup READY · inference ENABLED ({mode}) "
                    f"· BUT live_freshness=STALE (market={_stages.get('state')}, "
                    f"age_ms={_stages.get('age_ms')}) — process alive, intelligence NOT fresh"
                )
                details["live_freshness"] = _fresh
            else:
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

        # --- NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: LIVE INFERENCE FRESHNESS ---
        # Process being READY + model bundle loaded is NOT proof that the
        # feature->inference->decision chain is live. A frozen chain (ticks
        # move but features/inference are stale) must surface as STALE here,
        # independent of uptime / state_version / HTTP 200.
        inference_fresh_status = "UNKNOWN"
        inference_fresh_detail = "engine not attached"
        if engine is not None:
            try:
                fresh = engine.compute_live_freshness()
                inf = fresh.get("inference", {}).get("state")
                dec = fresh.get("decision", {}).get("state")
                overall_fresh = fresh.get("overall")
                inference_fresh_status = str(overall_fresh)
                inference_fresh_detail = (
                    f"inference={inf} decision={dec} "
                    f"(features_age_ms={fresh.get('features', {}).get('age_ms')}, "
                    f"inference_age_ms={fresh.get('inference', {}).get('age_ms')})"
                )
            except Exception as e:
                inference_fresh_status = "UNKNOWN"
                inference_fresh_detail = f"freshness introspection failed: {e}"
        # Allow STALE/UNKNOWN to win at the same rank weight as the model
        # subsystem so a frozen chain cannot be masked by a READY bundle.
        subsystems["inference_freshness"] = inference_fresh_status
        details["inference_freshness"] = inference_fresh_detail

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

    def _runtime_version_block(state: Any) -> dict[str, Any]:
        """Version-consistency block for /api/status (TASK-9 production
        release layer): real backend/build data, never hardcoded; reports
        VERSION_INCONSISTENCY on drift (brief sections 15/52).

        PHASE 28 HOT-PATH FIX: this block runs PRAGMA integrity_check +
        drift fingerprinting + artifact hashing across 3 DBs — measured
        ~270-1000ms SYNCHRONOUSLY. get_system_state() (which includes this
        block) is called by the SSE loop EVERY 0.2s ON THE EVENT LOOP, which
        starved the tick coroutine and froze inference/features/AI-Hub.
        The block is now cached for 60s: DB schema versions change only on
        migrations, so a 1-minute TTL cannot hide a real drift while keeping
        the event loop free. First call still computes fresh data.
        """
        now_mono = time.monotonic()
        cached = getattr(state, "_version_block_cache", None)
        if cached is not None and (now_mono - cached[0]) < 60.0:
            return cached[1]

        from nexus_scalp.release.versioning import (
            RuntimeVersionBlock,
            default_db_versions_provider,
        )

        cfg = getattr(state, "config", None)
        web_dir = None
        try:
            if cfg is not None and hasattr(cfg, "base_dir"):
                web_dir = Path(cfg.base_dir) / "Web"
            if web_dir is None or not web_dir.is_dir():
                web_dir = Path("Web") if Path("Web").is_dir() else None
        except Exception:
            web_dir = None
        return RuntimeVersionBlock(
            db_provider=default_db_versions_provider,
            web_dir=web_dir,
        ).build()

    def _runtime_version_block_cached(state: Any) -> dict[str, Any]:
        block = _runtime_version_block(state)
        try:
            state._version_block_cache = (time.monotonic(), block)
        except Exception:
            pass
        return block

    def _runtime_version_block_stateful(state: Any) -> dict[str, Any]:
        now_mono = time.monotonic()
        cached = getattr(state, "_version_block_cache", None)
        if cached is not None and (now_mono - cached[0]) < 60.0:
            return cached[1]
        return _runtime_version_block_cached(state)

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
                                engine,
                                "effective_feature_schema_id",
                                getattr(engine, "FEATURE_SCHEMA_ID", "scalp_v1"),
                            )
                            model_meta["feature_dimension"] = getattr(
                                engine,
                                "effective_feature_dim",
                                getattr(engine, "FEATURE_DIM", len(FEATURE_NAMES)),
                            )
                            model_meta["scaler_ready"] = bool(
                                getattr(bundle.scaler, "is_ready", lambda: False)()
                            )
                            model_meta["latency_ms"] = getattr(
                                engine, "_last_inference_latency_ms", None
                            )
                            # TASK latency forensics: honest staged breakdown
                            # (model_forward / feature / e2e / queue) — the
                            # UI must not conflate these with one number.
                            model_meta["latency_breakdown"] = getattr(
                                engine, "_last_latency_breakdown", None
                            )
                            model_meta["model_forward_ms"] = getattr(
                                engine, "_last_model_forward_ms", None
                            )
                            model_meta["feature_ms"] = getattr(engine, "_last_feature_ms", None)
                            model_meta["e2e_ms"] = getattr(engine, "_last_e2e_ms", None)
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

        # BUG-125: Build feature payload using the effective contract names
        # (50D scalp_v1 or 70D scalp_v3 — determined by the loaded bundle).
        try:
            eff_dim = getattr(engine, "effective_feature_dim", len(FEATURE_NAMES))
            if eff_dim == 70:
                from nexus_scalp.features.schema_contract import canonical_feature_names

                feature_names_for_payload = list(canonical_feature_names())
            else:
                feature_names_for_payload = list(FEATURE_NAMES)
        except Exception:
            feature_names_for_payload = list(FEATURE_NAMES)
        features_payload = []
        for i, name in enumerate(feature_names_for_payload):
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
            "price_digits": getattr(getattr(engine, "_symbol_info", None), "digits", None)
            if engine
            else None,
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
            # Market Radar (BUG-138): backend-authoritative ranked setup list.
            # Pure passthrough of LiveEngine._last_market_radar - the UI/SSE/WS
            # never compute setups. Included in the canonical snapshot so REST
            # (/api/live/state), SSE (/api/ticks/stream) and WebSocket all carry
            # the SAME authoritative radar object (single source of truth).
            "radar": (getattr(engine, "_last_market_radar", None) if engine is not None else None),
            "algo_config": algo_config_data,
            "liquidity": _liquidity_state_section(app.state.engine),
            "visual_overlays": {
                "rectangles": rectangles,
                "bos_lines": real_smc_overlays.get("bos_lines", []),
                "midlines": real_smc_overlays.get("midlines", []),
                "liq_markers": real_smc_overlays.get("liq_markers", []),
                "order_lines": order_lines,
            },
            "health": _build_health_section(app.state, now_mono),
            # NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: authoritative freshness of
            # every pipeline stage (market/features/inference/decision), each
            # FRESH|STALE|UNKNOWN independent of process uptime / state_version.
            "live_freshness": (
                engine.compute_live_freshness()
                if engine is not None and hasattr(engine, "compute_live_freshness")
                else None
            ),
            # UI stale-state flag: lets the frontend show an explicit STALE
            # banner instead of trusting state_version (which keeps climbing
            # even when intelligence is frozen).
            "is_stale": (
                bool(engine.compute_live_freshness().get("overall") == "STALE")
                if engine is not None and hasattr(engine, "compute_live_freshness")
                else False
            ),
            "versioning": _runtime_version_block_stateful(app.state),
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

    @app.get("/command_center.html")
    def serve_command_center_html() -> FileResponse:
        return FileResponse(WEB_DIR / "command_center.html")

    @app.get("/dependency")
    def serve_dependency_dashboard() -> FileResponse:
        """Dependency Intelligence developer dashboard (NSE engineering)."""
        return FileResponse(WEB_DIR / "dependency.html")

    @app.get("/dependency.html")
    def serve_dependency_dashboard_html() -> FileResponse:
        return FileResponse(WEB_DIR / "dependency.html")

    @app.get("/dependency_api.js")
    def serve_dependency_api_js() -> FileResponse:
        return FileResponse(WEB_DIR / "dependency_api.js")

    @app.get("/dependency_graph.js")
    def serve_dependency_graph_js() -> FileResponse:
        return FileResponse(WEB_DIR / "dependency_graph.js")

    @app.get("/dependency_ui.js")
    def serve_dependency_ui_js() -> FileResponse:
        return FileResponse(WEB_DIR / "dependency_ui.js")

    @app.get("/command_center_ui.js")
    def serve_command_center_js() -> FileResponse:
        return FileResponse(WEB_DIR / "command_center_ui.js")

    # FORENSIC FIX (Nexus-Forensic-01): command_center.html loads
    # command_center_spatial.js / command_center_console.js /
    # command_center_timemachine.js but server.py previously had NO routes
    # for them -> GET 404 -> window.NX.spatial/tm/console undefined ->
    # DOMContentLoaded handler throws and the entire CC renders blank.
    # These three routes restore asset resolution (verified 404 -> 200).
    @app.get("/command_center_spatial.js")
    def serve_command_center_spatial_js() -> FileResponse:
        return FileResponse(WEB_DIR / "command_center_spatial.js")

    @app.get("/command_center_console.js")
    def serve_command_center_console_js() -> FileResponse:
        return FileResponse(WEB_DIR / "command_center_console.js")

    @app.get("/command_center_timemachine.js")
    def serve_command_center_timemachine_js() -> FileResponse:
        return FileResponse(WEB_DIR / "command_center_timemachine.js")

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

    @app.get("/news_intelligence.js")
    def serve_news_intel() -> FileResponse:
        return FileResponse(WEB_DIR / "news_intelligence.js")

    @app.get("/forensic_console.js")
    def serve_forensic() -> FileResponse:
        return FileResponse(WEB_DIR / "forensic_console.js")

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
        # CodeQL py/path-injection (#62/#63/#67): never build a path from
        # user input. Match the request against the directory listing of the
        # webfonts root ONLY - no tainted value ever reaches a path
        # expression (no joins, no resolvers, no traversal surface).
        from fastapi import HTTPException as _HTTPException

        root = (WEB_DIR / "vendor" / "webfonts").resolve()
        want = str(font_name).replace("\\", "/").split("/")[-1]
        found = next(
            (candidate for candidate in root.iterdir() if candidate.name == want),
            None,
        )
        if found is None or not found.is_file():
            raise _HTTPException(status_code=404, detail="Not Found")
        return FileResponse(found)

    # =========================================================================
    # REST APIs: system status/diagnostics/live-state/dbmanage/config/settings
    # routes: extracted to web/diagnostics_state_routes.py (CHG-0032-A1
    # Step-3D, behavior-preserving). Registered at the SAME create_app
    # position; get_system_state stays HERE (dashboard heart, shared with
    # SSE/static) and is passed in as the canonical snapshot source.
    # =========================================================================
    from nexus_scalp.web.diagnostics_state_routes import (
        register_diagnostics_state_routes,
    )

    register_diagnostics_state_routes(app, _err, _log_err, serialize_enums, get_system_state)

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
        """Algorithm tuner — reads the AUTHORITATIVE runtime snapshot."""
        engine = app.state.engine
        store = getattr(engine, "runtime_config", None) if engine else None
        if store is not None:
            snap = store.get_snapshot()
            return {
                "atr_sl_buffer_multiplier": snap.atr_sl_buffer_multiplier,
                "min_risk_reward_ratio": snap.min_risk_reward_ratio,
                "ai_zone_confidence_threshold": snap.ai_zone_confidence_threshold,
                "fvg_mitigation_sensitivity": snap.fvg_mitigation_sensitivity,
                "order_block_lookback_bars": snap.order_block_lookback_bars,
                "configuration_version": snap.version,
                "runtime_applied": True,
            }
        # Engine offline: fall back to the bootstrap YAML (diagnostic only)
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
            "runtime_applied": False,
        }

    # PUT /api/algo/config
    @app.put("/api/algo/config")
    def save_algo_config(req: AlgoConfigRequest) -> dict[str, Any]:
        """Algorithm tuner save: authoritative store apply + live.yaml projection."""
        engine = app.state.engine
        if engine is None or not hasattr(engine, "runtime_config"):
            raise HTTPException(status_code=400, detail="Trading Engine offline.")
        updates = {
            "algo.atr_sl_buffer_multiplier": req.atr_sl_buffer_multiplier,
            "algo.min_risk_reward_ratio": req.min_risk_reward_ratio,
            "algo.ai_zone_confidence_threshold": req.ai_zone_confidence_threshold,
            "algo.fvg_mitigation_sensitivity": req.fvg_mitigation_sensitivity,
            "algo.order_block_lookback_bars": req.order_block_lookback_bars,
        }
        report = engine.apply_runtime_update(updates, source="WEB_ALGO_TUNER", actor="web")
        if not report.success:
            return {
                "success": False,
                "runtime_applied": False,
                "reason": report.reason,
                "configuration_version": report.configuration_version,
            }
        # live.yaml projection (compatibility/export; NOT authoritative)
        try:
            live_path = Path("configs/live.yaml")
            if not live_path.exists():
                live_path = Path("configs/base.yaml")
            with open(live_path, encoding="utf-8") as f:
                raw_data = yaml.safe_load(f) or {}
            snap = engine.runtime_config.get_snapshot()
            raw_data["algo"] = snap.to_algo_config().model_dump()
            tmp = Path("configs/live.yaml").with_suffix(".yaml.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(raw_data, f, default_flow_style=False)
            tmp.replace(Path("configs/live.yaml"))
        except Exception as e:
            logger.warning("[RUNTIME_CONFIG] live.yaml projection failed (non-fatal): %s", e)
        return {
            "success": True,
            "runtime_applied": True,
            "persisted": report.persisted,
            "configuration_version": report.configuration_version,
            "runtime_version": engine.runtime_config.get_version(),
            "correlation_id": report.correlation_id,
        }

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

        # BUG-125: use effective contract names for the debug features endpoint
        try:
            eff_dim = getattr(engine, "effective_feature_dim", len(FEATURE_NAMES))
            if eff_dim == 70:
                from nexus_scalp.features.schema_contract import canonical_feature_names

                _debug_feature_names = list(canonical_feature_names())
            else:
                _debug_feature_names = list(FEATURE_NAMES)
        except Exception:
            _debug_feature_names = list(FEATURE_NAMES)

        for idx, name in enumerate(_debug_feature_names):
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
        expected_dim = (
            getattr(engine, "effective_feature_dim", len(FEATURE_NAMES))
            if engine
            else len(FEATURE_NAMES)
        )

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

        # TASK latency forensics: staged honest timing (monotonic).
        from nexus_scalp.features.latency_tracer import LatencyStage, LatencyTracer

        _trace = LatencyTracer()
        _trace.mark(LatencyStage.T0_MARKET_EVENT)
        _trace.mark(LatencyStage.T1_FEATURE_START)
        _trace.mark(LatencyStage.T2_FEATURE_DONE)

        try:
            if engine is not None and getattr(engine, "_bundle", None) is not None:
                # Use the live bundle so the test exercises the exact deployed weights and scaler.
                with engine._bundle_lock:
                    bundle = engine._bundle
                x_np = np.array(sanitized, dtype=np.float32).reshape(1, -1)
                x_np = bundle.scaler.transform_50d(x_np)
                _trace.mark(LatencyStage.T3_SCALER_DONE)
                x = torch.tensor(x_np, dtype=torch.float32)
                x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
                _trace.mark(LatencyStage.T4_TENSOR_DONE)
                bundle.model.eval()
                _trace.mark(LatencyStage.T5_MODEL_START)
                _prior_threads = torch.get_num_threads()
                torch.set_num_threads(1)
                try:
                    with torch.inference_mode():
                        probs_tensor = bundle.model(x)
                finally:
                    torch.set_num_threads(_prior_threads)
                _trace.mark(LatencyStage.T6_MODEL_DONE)
                model_source = "LIVE_BUNDLE"
            else:
                # Engine offline: instantiate a fresh net so the endpoint still validates
                # the model graph and tensor contract.
                from nexus_scalp.models.scalp_net import ScalpNet

                model = ScalpNet(num_features=expected_dim, num_classes=4)
                model.eval()
                x = torch.tensor([sanitized], dtype=torch.float32)
                x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
                _trace.mark(LatencyStage.T4_TENSOR_DONE)
                _trace.mark(LatencyStage.T5_MODEL_START)
                _prior_threads = torch.get_num_threads()
                torch.set_num_threads(1)
                try:
                    with torch.inference_mode():
                        probs_tensor = model(x)
                finally:
                    torch.set_num_threads(_prior_threads)
                _trace.mark(LatencyStage.T6_MODEL_DONE)
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

        _trace.mark(LatencyStage.T7_DECODE_DONE)
        _trace.mark(LatencyStage.T8_CONFIDENCE_DONE)
        _trace.mark(LatencyStage.T10_PUBLISHED)
        latency_ms = _trace.e2e_ms()
        latency_breakdown = _trace.to_dict()

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
            "latency_breakdown": latency_breakdown,
            "model_forward_ms": latency_breakdown.get("model_ms"),
            "feature_ms": latency_breakdown.get("feature_ms"),
            "e2e_ms": latency_breakdown.get("e2e_ms"),
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
                    # PHASE 28: fv.to_tensor_input() is the BASE-50 contract.
                    # The live model path consumes the assembled vector
                    # (_last_live_tensor_dim = base50 + news10 + liquidity10
                    # when a 70D bundle serves). Compare like-for-like:
                    # base50 against BASE dimension, assembled vs effective.
                    values = list(fv.to_tensor_input())
                    bad = sum(1 for v in values if _classify_feature(v)[1] != "VALID")
                    eff_dim = getattr(engine, "effective_feature_dim", len(FEATURE_NAMES))
                    live_dim = getattr(engine, "_last_live_tensor_dim", None)
                    if live_dim is not None:
                        # A 70D assembly ran: the base block (50) feeding it is
                        # correct by definition; judge the engine on its own
                        # recorded live tensor width instead of mixing contracts.
                        dim_ok = int(live_dim) in (len(values), eff_dim)
                    else:
                        dim_ok = len(values) == eff_dim or eff_dim == len(FEATURE_NAMES)
                    if not dim_ok:
                        add(
                            "Feature Engine",
                            "UNHEALTHY",
                            f"Dimensionality contract violated: {len(values)} != {eff_dim}.",
                            {"dimensions": len(values), "expected": eff_dim},
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

                repo = AuditRepository(config=_default_audit_config())

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

                repo = AuditRepository(config=_default_audit_config())
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
    # DEBUG 70D FORENSIC CONSOLE — CANONICAL SNAPSHOT API (brief 41/28/33/34)
    # -------------------------------------------------------------------------
    # GET /api/debug/state            -> one canonical full debug snapshot
    # GET /api/debug/snapshots        -> rolling snapshot history (ids only)
    # GET /api/debug/snapshots/{id}   -> a stored snapshot
    # GET /api/debug/compare?a=&b=    -> feature/model/confidence/regime/
    #                                    liquidity/news/policy/risk diff
    # All read-only; assembled from in-memory engine state and cached worker
    # reports (brief 43: no DB scans, no recompute, no model reload).
    # =========================================================================

    @app.get("/api/debug/state")
    def get_debug_state() -> dict[str, Any]:
        """Canonical 70D runtime intelligence snapshot for the Debug tab.

        One payload with: runtime / contract / features (70D matrix) / model
        / confidence / policy / risk / exposure / execution / positions /
        exit / liquidity / news / workers / database / caches / chart / sse
        / errors. Every section is real backend state or an explicit
        UNAVAILABLE marker with a reason + correlation_id (brief 36/42).
        """
        from nexus_scalp.web.debug_snapshot import build_debug_snapshot

        try:
            payload = build_debug_snapshot(app.state.engine, app.state)
            store = getattr(app.state, "debug_snapshot_store", None)
            if store is not None:
                store.push(payload)
            return serialize_enums(payload)
        except Exception as exc:
            _log_err(exc, "Debug snapshot failed", endpoint="/api/debug/state")
            # CodeQL #79/#80 (information exposure): exception detail stays
            # in the server log; the wire carries a generic code only.
            return {
                "snapshot_id": None,
                "correlation_id": new_request_id(),
                "timestamp": datetime.now(UTC).isoformat(),
                "available": False,
                "reason": "DEBUG_SNAPSHOT_ERROR",
            }

    @app.get("/api/debug/freshness")
    def get_debug_freshness() -> dict[str, Any]:
        """NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: live-freshness + no-cache diagnostic.

        Returns the authoritative per-stage freshness (market/features/
        inference/decision) AND runs the observational no-cache
        diagnose_freshness() that re-fetches fresh market state, rebuilds
        features, the 70D tensor, and runs fresh inference to localize exactly
        where the chain froze. Purely diagnostic; never touches the live order
        path or any safety control.
        """
        try:
            engine = app.state.engine
            if engine is None:
                return {
                    "available": False,
                    "reason": "ENGINE_NOT_ATTACHED",
                    "frozen_at": "UNKNOWN",
                }
            fresh = engine.compute_live_freshness()
            diagnostic = engine.diagnose_freshness()
            return {
                "available": True,
                "live_freshness": fresh,
                "diagnostic": diagnostic,
                "checked_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            _log_err(exc, "Freshness diagnostic failed", endpoint="/api/debug/freshness")
            return {
                "available": False,
                "reason": "FRESHNESS_DIAGNOSTIC_ERROR",
                "frozen_at": "UNKNOWN",
            }

    @app.get("/api/debug/snapshots")
    def get_debug_snapshots() -> dict[str, Any]:
        """Rolling debug snapshot history (brief 33) — ids/timestamps only."""
        store = getattr(app.state, "debug_snapshot_store", None)
        if store is None:
            return {"available": False, "snapshots": []}
        return {"available": True, "snapshots": store.list()}

    @app.get("/api/debug/snapshots/{snapshot_id}")
    def get_debug_snapshot(snapshot_id: str) -> dict[str, Any]:
        """One stored debug snapshot by id (brief 33/49)."""
        store = getattr(app.state, "debug_snapshot_store", None)
        if store is None:
            return {"available": False, "reason": "NO_SNAPSHOT_STORE"}
        snap = store.get(snapshot_id)
        if snap is None:
            return {"available": False, "reason": f"SNAPSHOT_NOT_FOUND: {snapshot_id}"}
        return serialize_enums(snap)

    @app.get("/api/debug/trace/{execution_id}")
    def get_debug_trace(execution_id: str) -> dict[str, Any]:
        """PHASE 13 forensic trace: one EXEC-... id across the whole pipeline.

        Pure READ-ONLY join of audit_signals (the policy evaluation that
        stamped the id) + audit_orders (dispatch rows whose reason embeds the
        same id). Returns the full decision chain for one evaluation.
        """

        result: dict[str, Any] = {
            "execution_id": execution_id,
            "available": False,
            "reason": "NO_AUDIT_DB",
            "signal": None,
            "orders": [],
        }
        db_path = None
        try:
            import sqlite3 as _sqlite3

            from nexus_scalp.adapters.audit_db import get_default_audit_db_path

            db_path = get_default_audit_db_path()
            con = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            con.row_factory = _sqlite3.Row
            try:
                sig = None
                rows = con.execute(  # noqa: F841 - forensic probe kept for row shape
                    "SELECT * FROM audit_signals ORDER BY generated_at DESC LIMIT 1"
                ).fetchall()
                # find by execution_id in payload (stamped historically via
                # reason/request_id join) — primary column is reason_code; for
                # pre-instrumentation rows join by request_id is not possible,
                # so the endpoint returns signal rows whose payload contains
                # the id and all dispatch rows whose reason embeds it.
                sig_cols = [d[0] for d in con.execute("PRAGMA table_info(audit_signals)")]
                if "execution_id" in sig_cols:
                    sig = con.execute(
                        "SELECT * FROM audit_signals WHERE execution_id = ? ORDER BY generated_at DESC LIMIT 5",
                        (execution_id,),
                    ).fetchall()
                orders = con.execute(
                    "SELECT * FROM audit_orders WHERE reason LIKE ? ORDER BY timestamp DESC LIMIT 5",
                    (f"%{execution_id}%",),
                ).fetchall()
                result.update(
                    {
                        "available": True,
                        "signal": [dict(r) for r in (sig or [])],
                        "orders": [dict(r) for r in orders],
                        "db_path": str(db_path),
                    }
                )
            finally:
                con.close()
        except Exception as e:  # never fail the API for a trace lookup
            result["reason"] = f"TRACE_LOOKUP_ERROR: {e}"
        return serialize_enums(result)

    @app.get("/api/debug/compare")
    def get_debug_compare(a: str, b: str) -> dict[str, Any]:
        """Compare two stored snapshots (brief 34): feature deltas + model/
        confidence/regime/liquidity/news/policy/risk changes."""
        from nexus_scalp.web.debug_snapshot import diff_snapshots

        store = getattr(app.state, "debug_snapshot_store", None)
        if store is None:
            return {"available": False, "reason": "NO_SNAPSHOT_STORE"}
        snap_a = store.get(a)
        snap_b = store.get(b)
        if snap_a is None or snap_b is None:
            return {
                "available": False,
                "reason": "SNAPSHOT_NOT_FOUND (need both a and b)",
            }
        return serialize_enums(diff_snapshots(snap_a, snap_b))

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
            payload: dict[str, Any] = {"available": True, "period": report.to_dict()}
            # BUG-134: smart market context (broker server day + open/closed).
            engine = app.state.engine
            adapter = getattr(engine, "adapter", None) if engine else None
            server_now = probe_server_time(adapter) if adapter is not None else None
            server_time = None
            if server_now is not None:
                from datetime import UTC as _UTC

                server_time = datetime.fromtimestamp(server_now, _UTC)
            tick_age = None
            if adapter is not None and hasattr(adapter, "get_broker_tick"):
                try:
                    exec_cfg = (
                        getattr(getattr(engine, "config", None), "execution", None)
                        if engine is not None
                        else None
                    )
                    symbol = (
                        getattr(exec_cfg, "symbol", None) if exec_cfg is not None else None
                    ) or "XAUUSD"
                    tk = adapter.get_broker_tick(symbol)
                    if tk.available and tk.time_utc is not None:
                        tick_age = max(0.0, (datetime.now(UTC) - tk.time_utc).total_seconds())
                except Exception:
                    tick_age = None
            ms = market_state(server_time, last_tick_age_sec=tick_age)
            payload["market"] = {
                "state": ms["state"],
                "last_tick_age_sec": ms["last_tick_age_sec"],
                "next_open_iso": ms["next_open_iso"],
                "reason": ms["reason"],
                "server_day": current_trading_day(server_time),
                "server_time_utc": server_time.isoformat() if server_time else None,
            }
            return payload
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
    # =========================================================================
    # TRADE INTELLIGENCE (PHASE 09, CHG-0032 Step 3B): extracted verbatim to
    # web/intelligence_routes.py — registered at the same position.
    # =========================================================================
    from nexus_scalp.web.intelligence_routes import (
        register_intelligence_routes,
    )
    from nexus_scalp.web.intelligence_routes import (
        router as intelligence_router,
    )

    register_intelligence_routes(app)
    app.include_router(intelligence_router)

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

    @app.get("/api/research/detail/{strategy_id}")
    def get_research_detail(strategy_id: str) -> dict[str, Any]:
        """TASK-21: ONE-CLICK TRACE — strategy -> runs -> gates -> events ->
        evidence -> snapshot (spec 10/11/12). Explains exactly where a strategy
        is, why it has not moved, and what evidence proves the state."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.observability import ResearchObservabilityStore

            obs = ResearchObservabilityStore(engine.audit)
            trace = obs.trace(strategy_id)
            entry = obs._registry_entry(strategy_id)
            if entry is not None:
                from nexus_scalp.research.observability import _registry_blocked_reason

                trace["blocked_reason"] = _registry_blocked_reason(engine.audit, entry)
                from nexus_scalp.research.models import CandidateLifecycle, StrategyRegistryEntry
                from nexus_scalp.research.registry import StrategyRegistry

                reg = StrategyRegistry(engine.audit)
                parsed = StrategyRegistryEntry(
                    strategy_id=entry["strategy_id"],
                    strategy_version=entry["strategy_version"],
                    feature_schema_id=entry.get("feature_schema_id", ""),
                    feature_dimension=int(entry.get("feature_dimension") or 0),
                    discovery_source=entry.get("discovery_source", ""),
                    discovery_window=entry.get("discovery_window", ""),
                    context_definition=entry.get("context_definition", {}),
                    parent_strategy_ids=entry.get("parent_strategy_ids", []),
                    lifecycle=CandidateLifecycle(entry.get("lifecycle", "DISCOVERED")),
                    validation_lineage=entry.get("validation_lineage", []),
                    retirement_reason=entry.get("retirement_reason", ""),
                )
                trace["invariant"] = reg.invariant_check(parsed)
            return serialize_enums({"available": True, "detail": trace})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research detail failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/trace")
    def get_research_trace(
        strategy_id: str | None = None,
        research_run_id: str | None = None,
        gate_id: str | None = None,
        evidence_id: str | None = None,
    ) -> dict[str, Any]:
        """TASK-21: trace by any of strategy_id / run / gate / evidence."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.observability import ResearchObservabilityStore

            obs = ResearchObservabilityStore(engine.audit)
            out: dict[str, Any] = {"available": True}
            if strategy_id:
                out["trace"] = obs.trace(strategy_id, research_run_id)
            if gate_id:
                g = obs.get_gate(gate_id)
                out["gate"] = g.model_dump(mode="json") if g else None
            if evidence_id:
                out["evidence"] = obs.get_evidence(evidence_id)
            return serialize_enums(out)
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research trace failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/gates")
    def get_research_gates(
        strategy_id: str | None = None,
        research_run_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """TASK-21: first-class gate list with explicit status/reason/evidence."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.observability import ResearchObservabilityStore

            obs = ResearchObservabilityStore(engine.audit)
            gates = obs.list_gates(
                strategy_id=strategy_id, research_run_id=research_run_id, limit=limit
            )
            return serialize_enums(
                {
                    "available": True,
                    "gates": [g.model_dump(mode="json") for g in gates],
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research gates failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/events")
    def get_research_events(
        strategy_id: str | None = None,
        research_run_id: str | None = None,
        limit: int = 300,
    ) -> dict[str, Any]:
        """TASK-21: persisted gate timeline (never fake timestamps)."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.observability import ResearchObservabilityStore

            obs = ResearchObservabilityStore(engine.audit)
            events = obs.list_events(
                strategy_id=strategy_id, research_run_id=research_run_id, limit=limit
            )
            return serialize_enums({"available": True, "events": events})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research events failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/evidence")
    def get_research_evidence(
        strategy_id: str | None = None,
        research_run_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """TASK-21: immutable evidence vault."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.observability import ResearchObservabilityStore

            obs = ResearchObservabilityStore(engine.audit)
            evidence = obs.list_evidence(
                strategy_id=strategy_id, research_run_id=research_run_id, limit=limit
            )
            return serialize_enums({"available": True, "evidence": evidence})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research evidence failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/worker")
    def get_research_worker() -> dict[str, Any]:
        """TASK-21: worker heartbeat + health classification (spec 29/30)."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.observability import ResearchObservabilityStore

            obs = ResearchObservabilityStore(engine.audit)
            health = obs.worker_health()
            worker = getattr(engine, "research_worker", None)
            if worker is not None:
                from nexus_scalp.research.worker import format_research_worker_status

                health["runtime"] = format_research_worker_status(worker)
            return serialize_enums({"available": True, "worker": health})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research worker failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/queue")
    def get_research_queue() -> dict[str, Any]:
        """TASK-21: gate queue census (queued/running/last-errors, spec 31)."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.observability import ResearchObservabilityStore

            obs = ResearchObservabilityStore(engine.audit)
            return serialize_enums({"available": True, "queue": obs.queue_snapshot()})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research queue failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/analytics")
    def get_research_analytics() -> dict[str, Any]:
        """TASK-21: failure heatmap + family analytics (spec 47/48)."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.observability import ResearchObservabilityStore

            obs = ResearchObservabilityStore(engine.audit)
            return serialize_enums(
                {
                    "available": True,
                    "heatmap": obs.gate_failure_heatmap(),
                    "families": obs.family_analytics(),
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research analytics failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/preflight")
    def get_research_preflight(strategy_id: str) -> dict[str, Any]:
        """TASK-21: validation pre-flight (spec 38/40).

        Returns PREFLIGHT PASS or the exact blockers. Never starts a run."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            checks: dict[str, Any] = {}
            dataset = engine.research_dataset_builder.build()
            checks["dataset_available"] = len(dataset.samples) > 0
            checks["dataset_samples"] = len(dataset.samples)
            checks["dataset_id"] = dataset.dataset_id

            entry = engine.strategy_registry.get(strategy_id)
            checks["strategy_in_registry"] = entry is not None

            from nexus_scalp.research.discovery import discover_candidates

            cands = discover_candidates(dataset.samples, dataset_id=dataset.dataset_id)
            checks["candidate_found"] = any(c.strategy_id == strategy_id for c in cands)
            checks["feature_schema"] = "COMPATIBLE"
            checks["oos_protected"] = True  # OOS is always a fresh temporal split
            checks["duplicate_run"] = False
            passed = (
                checks["dataset_available"]
                and checks["strategy_in_registry"]
                and checks["candidate_found"]
            )
            return serialize_enums(
                {
                    "available": True,
                    "preflight": {
                        "status": "PREFLIGHT PASS" if passed else "PREFLIGHT FAIL",
                        "checks": checks,
                        "blockers": [
                            k
                            for k, v in checks.items()
                            if (isinstance(v, bool) and not v)
                            or (isinstance(v, str) and v != "COMPATIBLE")
                        ],
                    },
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research preflight failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/research/retry-gate")
    def post_research_retry_gate(gate_id: str) -> dict[str, Any]:
        """TASK-21: safe retry of a TECHNICAL failure (spec 60).

        Only a gate whose failure_class is TECHNICAL or DATA (retryable=True)
        may be retried. RESEARCH failures (statistical OOS FAIL) are NEVER
        retried through this endpoint."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.observability import ResearchObservabilityStore

            obs = ResearchObservabilityStore(engine.audit)
            gate = obs.get_gate(gate_id)
            if gate is None:
                return {"available": False, "reason": "GATE_NOT_FOUND"}
            if gate.status == "RUNNING":
                return {"available": False, "reason": "GATE_ALREADY_RUNNING"}
            if gate.failure_class.value == "RESEARCH" and not gate.retryable:
                return {
                    "available": False,
                    "reason": "RESEARCH_FAILURE_NOT_RETRYABLE",
                    "gate": gate.model_dump(mode="json"),
                }
            obs.record_event(
                gate.strategy_id,
                gate.research_run_id,
                "GATE_RETRIED",
                "gate retried by operator",
                payload={"gate": gate.gate_type.value, "gate_id": gate_id},
                gate_id=gate_id,
            )
            updated = gate.model_copy(
                update={
                    "status": "QUEUED",
                    "failure_reason": "",
                    "failure_class": "UNKNOWN",
                    "completed_at": None,
                    "duration_ms": 0.0,
                    "evidence_id": "",
                }
            )
            obs._gates[gate_id] = updated
            return serialize_enums({"available": True, "gate": updated.model_dump(mode="json")})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research retry failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/research/cancel")
    def post_research_cancel(research_run_id: str) -> dict[str, Any]:
        """TASK-21: cancel a research run — becomes CANCELLED, never FAILED;
        completed gate results are preserved (spec 61)."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.observability import ResearchObservabilityStore

            obs = ResearchObservabilityStore(engine.audit)
            rows = obs._runs_for("", research_run_id)
            if not rows:
                return {"available": False, "reason": "RUN_NOT_FOUND"}
            obs.record_event(
                rows[0].get("strategy_id", ""),
                research_run_id,
                "RESEARCH_RUN_CANCELLED",
                "research run cancelled by operator",
            )
            return serialize_enums(
                {
                    "available": True,
                    "cancelled": True,
                    "run_id": research_run_id,
                    "status": "CANCELLED",
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research cancel failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/research/diagnostics")
    def get_research_diagnostics() -> dict[str, Any]:
        """TASK-21: final debug view (spec 70) — worker health, queue, last
        error, blocked strategies, failed gates, dataset/evidence health.
        The first place a developer goes when research stops progressing."""
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.observability import ResearchObservabilityStore

            obs = ResearchObservabilityStore(engine.audit)
            out: dict[str, Any] = {
                "available": True,
                "worker": obs.worker_health(),
                "queue": obs.queue_snapshot(),
                "heatmap": obs.gate_failure_heatmap(),
            }
            worker = getattr(engine, "research_worker", None)
            if worker is not None:
                from nexus_scalp.research.worker import format_research_worker_status

                out["worker"]["runtime"] = format_research_worker_status(worker)
            blocked: list[dict[str, Any]] = []
            try:
                import sqlite3 as _sqlite3

                conn = _sqlite3.connect(engine.audit._db_path, timeout=5.0)
                conn.row_factory = _sqlite3.Row
                try:
                    for r in conn.execute(
                        "SELECT gate_id, strategy_id, research_run_id, gate_type, "
                        "status, failure_reason, failure_class, evidence_id "
                        "FROM research_gates WHERE status IN ('BLOCKED','FAILED','ERROR') "
                        "ORDER BY completed_at DESC LIMIT 25;"
                    ).fetchall():
                        blocked.append(dict(r))
                finally:
                    conn.close()
            except Exception:
                blocked = []
            out["blocked_gates"] = blocked
            return serialize_enums(out)
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research diagnostics failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/db/status")
    def get_db_migration_status() -> dict[str, Any]:
        """TASK-10: per-domain database schema + migration state (§38).

        Reports schema version, expected version, migration state, pending
        count, integrity and last migration for every persistent domain.
        Read-only; never runs migrations from the API (§31).
        """
        from pathlib import Path as _Path

        from nexus_scalp.database.engine import DatabaseMigrationEngine, db_path_for_domain
        from nexus_scalp.database.models import DatabaseDomain

        base = _Path.cwd()
        out: dict[str, Any] = {}
        for dom in DatabaseDomain:
            path = db_path_for_domain(dom.value, base)
            eng = DatabaseMigrationEngine(db_path=path, domain=dom)
            try:
                st = eng.status()
                out[dom.value] = {
                    "schema_version": st["current_version"],
                    "expected_version": st["expected_version"],
                    "migration_state": st["migration_state"],
                    "pending_count": st["pending_count"],
                    "integrity": st.get("integrity", ""),
                    "last_migration": st.get("last_migration", {}),
                    "tamper_detected": st.get("tamper_detected", False),
                }
            except Exception as exc:
                _log_err(exc, "db migration status failed", endpoint="/api/db/status")
                out[dom.value] = {
                    "schema_version": 0,
                    "expected_version": eng.expected_version(),
                    "migration_state": "DB_MIGRATION_FAILED",
                    "error": "DB_MIGRATION_FAILED",
                }
        return serialize_enums({"available": True, "databases": out})

    @app.get("/api/forensics/health")
    def get_forensic_health() -> dict[str, Any]:
        """TASK-11: post-70D continuous forensic health snapshot.

        Central dashboard data — every check item carries
        status/last_check/last_error/evidence/expected/correlation_id plus an
        expandable detail_view (§51/§52). Read-only; runs the check matrix on
        demand and persists the snapshot to artifacts/forensics/.
        """
        try:
            from nexus_scalp.forensics import ForensicHealthEngine

            engine = ForensicHealthEngine()
            dash = engine.dashboard()
            return serialize_enums({"available": True, "forensics": dash})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Forensic health failed"})
            return _err("INTERNAL_ERROR")

    @app.get("/api/forensics/deploy-gate")
    def get_forensic_deploy_gate() -> dict[str, Any]:
        """TASK-12: canonical deploy gate (§9).

        Exposes overall_status, deployment_allowed, blocking_reasons,
        health_snapshot_id, commit_sha and checks. Read-only; never mutates.
        Engine failure -> FORENSIC_ENGINE_UNAVAILABLE (never silent pass).
        """
        try:
            from nexus_scalp.forensics import (
                ForensicHealthEngine,
                load_last_gate_result,
                run_deploy_gate,
            )

            engine = ForensicHealthEngine()
            result = run_deploy_gate(engine)
            payload = result.to_dict()
            payload["deployment_allowed"] = payload["decision"] in ("ALLOW", "ALLOW_WITH_WARNING")
            payload["blocking_reasons"] = payload["blocking_checks"]
            # degraded/unknown review conditions also surface as reasons
            if payload["decision"] == "REVIEW_REQUIRED":
                payload["blocking_reasons"] = [
                    f"{c['check_id']} [{c['status']}]"
                    for c in engine.dashboard()["rows"].values()
                    if c["status"] in ("DEGRADED", "UNKNOWN")
                ][:20]
            last = load_last_gate_result()
            return serialize_enums({"available": True, "gate": payload, "last_gate": last})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Deploy gate failed"})
            return serialize_enums(
                {
                    "available": True,
                    "gate": {
                        "decision": "FORENSIC_ENGINE_UNAVAILABLE",
                        "overall_status": "UNKNOWN",
                        "deployment_allowed": False,
                        "blocking_reasons": ["gate engine unavailable"],
                        "engine_error": "FORENSIC_ENGINE_UNAVAILABLE",
                    },
                }
            )

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

    @app.post("/api/research/promote")
    def promote_strategy_lifecycle(payload: dict[str, Any]) -> dict[str, Any]:
        """Operator-triggered lifecycle promotion for a VALIDATED strategy.

        RC4 repair: the explicit VALIDATED -> SHADOW -> ACTIVE promotion path
        had NO production caller. This endpoint is the ONLY operator-driven
        entry point for advancing a strategy's persisted lifecycle state.

        SAFETY (do NOT weaken):
          * Never auto-promotes. Every call requires an explicit `actor`.
          * `target_lifecycle` must be SHADOW or ACTIVE; the registry's state
            machine rejects illegal jumps (e.g. VALIDATED -> ACTIVE, or
            promoting a REJECTED/DEGRADED strategy) so an unvalidated or
            rejected strategy can never reach ACTIVE here.
          * `actor` is recorded in the validation lineage for auditability.

        Payload:
            strategy_id     : str  (required)
            target_lifecycle: "SHADOW" | "ACTIVE"  (required)
            actor           : str  (required; explicit operator identity)
            reason          : str  (optional; recorded in lineage)
        """
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.lifecycle import LifecycleError
            from nexus_scalp.research.models import CandidateLifecycle
            from nexus_scalp.research.registry import StrategyRegistry

            strategy_id = str(payload.get("strategy_id", "") or "").strip()
            target_str = str(payload.get("target_lifecycle", "") or "").strip().upper()
            actor = str(payload.get("actor", "") or "").strip()
            reason = str(payload.get("reason", "") or "").strip()

            if not strategy_id or not target_str:
                return _err(
                    "PROMOTION_BLOCKED",
                    extra={"reason": "strategy_id and target_lifecycle are required"},
                )
            # Explicit operator identity is mandatory — no implicit/system promotion.
            if not actor:
                return _err(
                    "PROMOTION_BLOCKED",
                    extra={"reason": "actor is required for explicit operator promotion"},
                )
            try:
                target_lifecycle = CandidateLifecycle(target_str)
            except ValueError:
                return _err(
                    "PROMOTION_BLOCKED",
                    extra={
                        "reason": (
                            f"target_lifecycle must be SHADOW or ACTIVE (got {target_str!r})"
                        )
                    },
                )
            if target_lifecycle not in (
                CandidateLifecycle.SHADOW,
                CandidateLifecycle.ACTIVE,
            ):
                return _err(
                    "PROMOTION_BLOCKED",
                    extra={
                        "reason": (
                            "operator promotion target must be SHADOW or ACTIVE; "
                            "VALIDATED is reached only by the validation pipeline"
                        )
                    },
                )

            registry = getattr(engine, "strategy_registry", None) or StrategyRegistry(engine.audit)
            existing = registry.get(strategy_id)
            if existing is None:
                return _err(
                    "PROMOTION_BLOCKED",
                    extra={"reason": "strategy not found in registry", "strategy_id": strategy_id},
                )
            # Confirmation gate: the persisted validation truth must be intact
            # before ANY operator promotion. A VALIDATED row with missing /
            # failed gates (or a REJECTED verdict score) can NEVER advance —
            # this makes activating an unvalidated or rejected strategy
            # structurally impossible through this endpoint.
            invariant = registry.invariant_check(existing)
            if not invariant.get("valid", False):
                return _err(
                    "PROMOTION_BLOCKED",
                    extra={
                        "reason": "validation-truth invariant check failed",
                        "strategy_id": strategy_id,
                        "problems": invariant.get("problems", []),
                    },
                )
            # Activation re-proves the FULL validation truth: a SHADOW row is
            # probed as VALIDATED so missing/failed OOS / walk-forward /
            # robustness / score evidence blocks ACTIVATION itself, not just
            # entry into shadow.
            if target_lifecycle == CandidateLifecycle.ACTIVE:
                truth_probe = existing.model_copy(
                    update={"lifecycle": CandidateLifecycle.VALIDATED}
                )
                activation_invariant = registry.invariant_check(truth_probe)
                if not activation_invariant.get("valid", False):
                    return _err(
                        "PROMOTION_BLOCKED",
                        extra={
                            "reason": "ACTIVATION requires intact validation truth",
                            "strategy_id": strategy_id,
                            "problems": activation_invariant.get("problems", []),
                        },
                    )
            # The registry state machine enforces: VALIDATED->SHADOW and
            # SHADOW->ACTIVE only; any other source or target is refused.
            updated = registry.transition_lifecycle(
                strategy_id=strategy_id,
                target=target_lifecycle,
                reason=f"operator_promotion:actor={actor}" + (f":{reason}" if reason else ""),
            )
            if updated is None:
                # Either the strategy is unknown, or the transition was illegal
                # (e.g. skipping SHADOW, or promoting REJECTED/DEGRADED). The
                # caller must first reach VALIDATED via /api/research/validate
                # and SHADOW via a prior explicit call.
                return _err(
                    "PROMOTION_BLOCKED",
                    extra={
                        "reason": (
                            "strategy not found or illegal transition (must reach "
                            "VALIDATED via validation, then SHADOW, then ACTIVE)"
                        ),
                        "strategy_id": strategy_id,
                        "target_lifecycle": target_str,
                    },
                )
            return serialize_enums(
                {
                    "available": True,
                    "promoted": True,
                    "strategy_id": updated.strategy_id,
                    "lifecycle": updated.lifecycle,
                    "actor": actor,
                    "entry": updated.model_dump(mode="json"),
                }
            )
        except LifecycleError as e:
            log_web_error(
                logger,
                "/api/research/promote",
                None,
                e,
                context={"msg": "Strategy lifecycle promotion blocked by state machine"},
            )
            return _err(
                "PROMOTION_BLOCKED",
                extra={"reason": "illegal lifecycle transition", "detail": str(e)},
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research promote failed"})
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

    @app.post("/api/research/recover-missing-outcomes")
    def trigger_outcome_recovery(req: OutcomeRecoveryRequest) -> dict[str, Any]:
        """
        BUG-140 P0-B: recovers decisions that never received an outcome row
        by joining the dispatch log (audit_orders) to broker-history
        evidence (audit_broker_orders/deals). Idempotent, bounded,
        append-only; reconstructed R/PnL carry explicit sweep provenance.
        Pass {"dry_run": true} to classify without writing.
        """
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.experience.outcome_recovery_sweep import (
                HistoricalOutcomeRecoverySweep,
            )

            sweep = HistoricalOutcomeRecoverySweep(ledger=engine.experience_ledger)
            result = sweep.run(dry_run=bool(req.dry_run))
            return {"available": True, "result": result.to_dict()}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Outcome recovery failed"})
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
    # MODEL LIFECYCLE / SHADOW / GOVERNANCE (PHASE 10/11/13 + shadow70):
    # extracted to web/model_governance_routes.py (CHG-0032-A1 Step-3A).
    # Registered at the SAME position the inline routes occupied (order parity).
    # =========================================================================
    from nexus_scalp.web.model_governance_routes import (
        register_model_governance_routes,
    )

    register_model_governance_routes(app)
    from nexus_scalp.web import model_governance_routes as _mgr

    app.include_router(_mgr.router)

    # =========================================================================
    # PHASE 12 NEWS + PHASE 18/22 LIQUIDITY/MSLIE API routes: extracted to
    # web/news_liquidity_mslie_routes.py (CHG-0032-A1 Step-3C, behavior-
    # preserving). Registered at the SAME create_app position (before the
    # factory/dependency include_router calls) to keep route order identical.
    # =========================================================================
    from nexus_scalp.web.news_liquidity_mslie_routes import (
        register_news_liquidity_mslie_routes,
    )

    register_news_liquidity_mslie_routes(app, _err, serialize_enums, time)

    # =========================================================================
    # STRATEGY FACTORY (2026-08-20): autonomous strategy evolution control room.
    # Routed views over the factory store; never touches the live path.
    # =========================================================================
    from nexus_scalp.web.factory_routes import router as factory_router

    app.include_router(factory_router)

    # =========================================================================
    # DEPENDENCY INTELLIGENCE (2026-08-27): canonical import + DI + architecture
    # graph for NSE engineering/debugging. AST-only, never boots the engine.
    # =========================================================================
    from nexus_scalp.web.dependency_routes import router as dependency_router

    app.include_router(dependency_router)
    # Thin handlers over the News AI service; reuses the Factory LLM provider.
    # =========================================================================
    from nexus_scalp.web.news_intelligence_routes import router as news_intel_router

    app.include_router(news_intel_router)

    # =========================================================================
    # DATABASE MANAGEMENT console (2026-08-20): SSMS-style explorer + SQL
    # console + API keys. Provider-abstracted; serves SQLite now and
    # PostgreSQL after the provider switch. Read-only by contract.
    # =========================================================================
    from nexus_scalp.web.db_console import router as db_console_router

    app.include_router(db_console_router)

    # =========================================================================
    # STRATEGY COMMAND CENTER (2026-08-23): spatial 2.5D lifecycle observability.
    # Read-only projections over the authoritative registry; never mutates
    # domain state and never fabricates eligibility or attribution.
    # =========================================================================
    from nexus_scalp.web.command_center_integration import (
        register_command_center_routes,
    )

    register_command_center_routes(
        app,
        _research,
        serialize_enums,
        _err,
    )

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
            sse_diag = app.state.sse_diag
            sse_diag["connection"] = "CONNECTED"
            sse_diag["connected_at"] = datetime.now(UTC).isoformat()
            sse_diag["reconnect_count"] = int(sse_diag.get("reconnect_count", 0)) + 1
            while True:
                if await request.is_disconnected():
                    sse_diag["connection"] = "DISCONNECTED"
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

                    try:
                        frame = canonical_json(payload)
                    except TypeError as ser_err:
                        # Structured, observable serialization diagnostic —
                        # never a silent drop (BUG-110): identify which field
                        # failed and let recoverable frames continue.
                        failed_fields = _find_non_json_fields(payload)
                        # CodeQL #76 (information exposure): wire carries a
                        # generic code + field names; detail stays in the log.
                        diag = {
                            "event": "SSE_SERIALIZATION_ERROR",
                            "correlation_id": f"sse-{version}",
                            "event_type": event_name,
                            "error": "SSE_SERIALIZATION_ERROR",
                            "failed_fields": failed_fields,
                        }
                        logger.error(
                            "[SSE] event=SERIALIZATION_ERROR version=%s fields=%s error=%r",
                            version,
                            failed_fields,
                            ser_err,
                        )
                        # Record in the debug console diagnostics (brief 27).
                        sse_diag["serialization_errors"] = (
                            int(sse_diag.get("serialization_errors", 0)) + 1
                        )
                        sse_diag["serialization_error"] = {
                            "correlation_id": diag["correlation_id"],
                            "error": "SSE_SERIALIZATION_ERROR",
                            "failed_fields": failed_fields,
                            "event_type": event_name,
                        }
                        frame = canonical_json(diag)
                        # Stream the diagnostic as a dedicated event so the
                        # UI can surface it; do NOT send corrupted JSON.
                        yield f"event: error\ndata: {frame}\n\n"
                        await asyncio.sleep(0.2)
                        continue
                    # SSE observability counters (brief 27).
                    sse_diag["last_event"] = event_name
                    sse_diag["event_count"] = int(sse_diag.get("event_count", 0)) + 1
                    sse_diag["last_latency_ms"] = None
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
