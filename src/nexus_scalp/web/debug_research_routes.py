"""Debug / experience / accounting-performance / research — REST routes.

Extracted VERBATIM from the former monolith ``server.py`` (CHG-0032 Step 3E,
behavior-preserving; Agent-5 modularization pass). Clusters:
    /api/debug/* (features, model-test, health, ipc-telemetry, state,
    freshness, snapshots, trace, compare), /api/experience/* (summary,
    strategies, decision, models, self-heal), /api/account/performance*
    (intelligence, kind, series, equity-curve, drawdown, trades/{id},
    strategies), /api/observability/stats, /api/research/* (23 surfaces incl.
    discover/validate/promote/self-heal/recover/repair POSTs), /api/db/status,
    /api/forensics/{health,deploy-gate}.

BOUNDARY: read-mostly views over app.state + the research/forensics engines;
research POST actions keep their exact guards and audit semantics verbatim.
Body models ModelTestRequest/OutcomeRecoveryRequest are single-sourced HERE
(moved verbatim from server.py; re-exported from the facade for compat) so
FastAPI resolves body-model ForwardRefs in this module's globals.

USED BY: server.create_app (registered at the same position as 3A-3D).
DO-NOT-PUT-HERE: chart/mt5/algo/positions/simulation/replay (server.py),
SSE stream (server.py), diagnostics/dbmanage/settings (Step-3D module).
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from nexus_scalp.accounting import PeriodKind
from nexus_scalp.accounting.aggregation import compute_advanced_metrics
from nexus_scalp.accounting.market_calendar import (
    current_trading_day,
    market_state,
    probe_server_time,
)
from nexus_scalp.accounting.worker import format_worker_status
from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.web.errors import log_web_error, new_request_id

logger = get_logger("nexus_scalp.web.debug_research_routes")


class ModelTestRequest(BaseModel):
    """
    Debug Hub model-test payload.

    `features` accepts the 50-dimensional vector directly. When omitted, the live
    feature vector is used, which makes the endpoint a one-click "what does the net
    think right now" probe.
    """

    features: list[float] | None = None
    use_live_features: bool = False


class OutcomeRecoveryRequest(BaseModel):
    dry_run: bool = False


def register_debug_research_routes(
    app: Any, _err: Any, _log_err: Any, serialize_enums: Any
) -> None:
    """Attach debug/experience/accounting/research routes (closures)."""
    from nexus_scalp.web.server import (  # late import: cycle-safe
        _classify_feature,
        _default_audit_config,
    )

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
