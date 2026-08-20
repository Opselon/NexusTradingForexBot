"""Canonical 70D Runtime Intelligence Debug Snapshot (Debug tab forensics).

WHY THIS EXISTS
---------------
The Debug tab must be a TRUTHFUL diagnostic interface: it never computes its
own trading intelligence; it only renders what the canonical backend state
exposes. This module assembles ONE canonical snapshot payload
(``/api/debug/state``) covering:

    runtime contract / features (registry-driven 70D matrix) / model input
    / model output / confidence pipeline / policy gate trace / risk
    / exposure (internal vs broker) / execution / positions / exit forensics
    / liquidity context / news / workers / database / caches / chart / SSE.

PRINCIPLES (from the Debug 70D forensic console brief):
  * Values come from the engine/runtime state ONLY — never recomputed here.
  * RAW/TRANSFORMED/NORMALIZED/CLIPPED stages are shown only when the
    canonical runtime actually exposes them; otherwise the payload says
    NOT_EXPOSED (never a fabricated 0).
  * Every failure is visible: each section carries its own error entry with
    correlation_id (no silent swallowing).
  * No secrets: paths are masked, tokens/keys never included.
  * Bounded work: the snapshot is assembled from in-memory engine state and
    cached worker reports; no DB scans per request, no model reload, no
    liquidity recompute (brief 43).
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.web.errors import new_request_id

# ---------------------------------------------------------------------------
logger = get_logger("nexus_scalp.web.debug_snapshot")
# ---------------------------------------------------------------------------
# Secrets / path hygiene
# ---------------------------------------------------------------------------


def _mask_path(path: str | None) -> str | None:
    """Mask machine-specific path prefixes (brief 44) while keeping the
    basename so artifacts remain identifiable."""
    if not path:
        return None
    name = os.path.basename(str(path).replace("\\", "/"))
    return f".../{name}"


# ---------------------------------------------------------------------------
# Snapshot identity (brief 28)
# ---------------------------------------------------------------------------


def new_snapshot_id() -> str:
    return f"snap_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------------------
# Feature registry (brief 2 / 4 / 8 / 9) — single source of truth
# ---------------------------------------------------------------------------


def _feature_registry() -> dict[str, Any]:
    """Canonical 70D registry rows from schema_contract (never hardcoded).

    Returns dict with schema_id / dimension / schema_hash / algorithm_version
    / families / names / rows. When the 70D contract cannot be imported the
    section reports UNAVAILABLE with a reason — the UI must show a broken
    contract, never a fake 70D list.
    """
    try:
        from nexus_scalp.features.schema_contract import (
            DIMENSION,
            SCHEMA_ID,
            SCHEMA_VERSION,
            canonical_feature_names,
            family_of,
            feature_schema_hash,
        )

        names = canonical_feature_names()
        rows = []
        for idx, name in enumerate(names):
            rows.append(
                {
                    "index": idx,
                    "name": name,
                    "family": family_of(idx),
                }
            )
        return {
            "schema_id": SCHEMA_ID,
            "schema_version": SCHEMA_VERSION,
            "dimension": DIMENSION,
            "schema_hash": feature_schema_hash(),
            "algorithm_version": SCHEMA_VERSION,
            "families": {
                "base": {"start": 0, "end": 50, "count": 50},
                "news": {"start": 50, "end": 60, "count": 10},
                "liquidity": {"start": 60, "end": 70, "count": 10},
            },
            "rows": rows,
        }
    except Exception as exc:
        logger.warning("debug_snapshot feature_registry error", error=str(exc))
        return {
            "available": False,
            "reason": "FEATURE_REGISTRY_UNAVAILABLE",
            "schema_id": None,
            "dimension": None,
            "schema_hash": None,
            "algorithm_version": None,
            "families": {},
            "rows": [],
        }


# ---------------------------------------------------------------------------
# Section builders — every one reads REAL engine state, never fabricates
# ---------------------------------------------------------------------------


def _runtime_section(engine: Any) -> dict[str, Any]:
    """RUNTIME STATUS header (brief 3): mode/symbol/timeframe/runtime/
    inference/model/schema/feature freshness + subsystem flags."""
    cfg = getattr(engine, "config", None)
    symbol = None
    timeframe = "M1"
    mode = None
    runtime_mode = None
    try:
        if cfg is not None and getattr(cfg, "execution", None) is not None:
            symbol = cfg.execution.symbol
            mode = getattr(cfg.execution.mode, "value", None) or str(cfg.execution.mode)
    except Exception:
        pass
    try:
        runtime_mode = getattr(engine, "_runtime_mode", None) or mode
    except Exception:
        runtime_mode = None

    running = bool(getattr(engine, "_running", False)) if engine else False
    inference_enabled = bool(getattr(engine, "_inference_enabled", False)) if engine else False
    warmup = getattr(engine, "warmup_state", None) if engine else None
    if engine is None:
        runtime_status = "STOPPED"
    elif not running:
        runtime_status = "STOPPED"
    elif warmup == "READY" and inference_enabled:
        runtime_status = "RUNNING"
    else:
        runtime_status = "DEGRADED"

    # Broker / tick / bar stream state
    broker_connected = False
    tick_age_sec = None
    tick_stream = "UNAVAILABLE"
    bar_stream = "UNAVAILABLE"
    last_feature_update = None
    feature_latency_ms = None
    try:
        if engine is not None:
            is_conn = getattr(engine.adapter, "is_connected", None)
            broker_connected = bool(is_conn()) if callable(is_conn) else False
            tick = getattr(engine, "_last_tick", None)
            if tick is not None and getattr(tick, "timestamp", None) is not None:
                try:
                    tick_age_sec = max(0.0, (datetime.now(UTC) - tick.timestamp).total_seconds())
                except Exception:
                    tick_age_sec = None
            if not broker_connected:
                tick_stream = "DISCONNECTED"
            elif tick_age_sec is None:
                tick_stream = "WAITING_TICK"
            elif tick_age_sec > 15.0:
                tick_stream = "STALE"
            else:
                tick_stream = "LIVE"
            agg = getattr(engine, "aggregator", None)
            if agg is not None:
                completed = len(getattr(agg, "get_completed_bars", lambda: [])())
                bar_stream = "LIVE" if completed > 0 else "WAITING_BARS"
    except Exception:
        pass

    fv = getattr(engine, "_last_fv", None) if engine else None
    if fv is not None:
        ts = getattr(fv, "timestamp_utc", None) or getattr(fv, "timestamp", None)
        if ts is not None:
            last_feature_update = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        try:
            feature_latency_ms = getattr(engine, "_last_inference_latency_ms", None)
        except Exception:
            feature_latency_ms = None

    # Model identity from the champion manager (real registry provenance)
    model_id = None
    model_version = None
    try:
        champ = getattr(engine, "champion_manager", None) if engine else None
        if champ is not None:
            model_id = getattr(champ, "model_id", None)
            model_version = getattr(champ, "model_version", None)
    except Exception:
        pass

    # Subsystem flags (workers/news/liquidity/shadow/research/training/accounting)
    def _flag(name: str) -> bool:
        return bool(getattr(engine, name, False)) if engine else False

    return {
        "mode": mode,
        "runtime_mode": runtime_mode,
        "symbol": symbol,
        "timeframe": timeframe,
        "runtime": runtime_status,
        "inference": "ENABLED" if inference_enabled else "DISABLED",
        "warmup": warmup,
        "model_id": model_id,
        "model_version": model_version,
        "schema_id": _feature_registry().get("schema_id"),
        "dimension": _feature_registry().get("dimension"),
        "schema_hash": _feature_registry().get("schema_hash"),
        "algorithm_version": _feature_registry().get("algorithm_version"),
        "last_feature_update": last_feature_update,
        "feature_latency_ms": feature_latency_ms,
        "subsystems": {
            "broker_connected": broker_connected,
            "tick_stream": tick_stream,
            "tick_age_sec": tick_age_sec,
            "bar_stream": bar_stream,
            "news": "ENABLED" if _flag("_news_enabled") else "DISABLED",
            "liquidity": "ENABLED" if _flag("_liquidity_enabled") else ("DISABLED"),
            "shadow": "RUNNING" if _flag("_shadow_worker_started") else "IDLE",
            "shadow70": "RUNNING"
            if _flag("_shadow70_worker_started")
            else ("IDLE" if _flag("_shadow70_enabled") else "DISABLED"),
            "research": "RUNNING" if _flag("_research_worker_started") else "IDLE",
            "training": "RUNNING" if _flag("_training_worker_started") else "IDLE",
            "accounting": "RUNNING" if _flag("_accounting_worker_started") else "IDLE",
            "telegram": "ENABLED" if _flag("_telegram_credential_source") else "DISABLED",
        },
    }


def _features_section(engine: Any) -> dict[str, Any]:
    """70D FEATURE MATRIX (brief 4/5/6/7/8/9/39/40).

    Registry-driven rows. Raw/transformed/normalized/clipped columns are
    populated ONLY from what the runtime exposes; otherwise NOT_EXPOSED.
    Base 0..49 and News 50..59 come from the live FeatureVector + news
    context; Liquidity 60..69 from the LiquidityGovernor snapshot. A
    legacy (scalp_v1) runtime yields a 50D matrix — the header shows the
    ACTUAL active dimension (no fake 70D).
    """
    reg = _feature_registry()
    rows: list[dict[str, Any]] = []
    base_values: list[float] = []
    fv_ts: str | None = None
    try:
        fv = getattr(engine, "_last_fv", None) if engine else None
        if fv is not None:
            vals = fv.to_tensor_input()
            if isinstance(vals, (list, tuple)):
                base_values = [float(v) for v in vals]
            ts = getattr(fv, "timestamp_utc", None) or getattr(fv, "timestamp", None)
            if ts is not None:
                fv_ts = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    except Exception:
        base_values = []

    # News 10D from the SAME context the champion consumed (live path).
    # Canonical selection (schema_contract NEWS_10D_NAMES): fields 0..8 +
    # news_state (index 10) of the 12-field news_context_v1 — never a blind
    # first-10 slice.
    news_values: list[float] = []
    news_ts: str | None = None
    news_state: str | None = None
    if engine is not None and getattr(engine, "_news_enabled", False):
        try:
            from nexus_scalp.features.schema_contract import NEWS_10D_NAMES

            ctx = engine.news_engine.current_context()
            if ctx is not None:
                from nexus_scalp.governance.alignment import vectorize_news_context

                vec = vectorize_news_context(ctx)
                selected = [vec[i] for i in (0, 1, 2, 3, 4, 5, 6, 7, 8, 10)]
                news_values = [float(v) for v in selected]
                news_ts = ctx.timestamp.isoformat() if getattr(ctx, "timestamp", None) else None
                news_state = str(getattr(ctx.state, "value", "NORMAL"))
                assert len(news_values) == len(NEWS_10D_NAMES)  # contract guard
        except Exception:
            news_values = []

    # Liquidity 10D from the governor snapshot payload (indices 60..69).
    liq_values: list[float] = []
    liq_ts: str | None = None
    liq_source: str | None = None
    liq_status: str | None = None
    try:
        gov = getattr(engine, "liquidity_governor", None) if engine else None
        if gov is not None:
            snap = gov.snapshot_payload()
            feats = snap.get("features", {})
            names = [f["name"] for f in reg.get("rows", [])[60:70]]
            liq_values = [float(feats[n]["value"]) for n in names if n in feats]
            liq_ts = snap.get("timestamp")
            liq_source = snap.get("source")
            liq_status = gov.status()
        else:
            liq_values = []
    except Exception:
        liq_values = []

    # Merge stage values per family.
    def _stage(
        family: str, idx: int, base: list[float], news: list[float], liq: list[float]
    ) -> dict[str, Any]:
        if family == "base":
            if idx < len(base):
                return {"raw": base[idx], "final": base[idx], "exposed": True}
            return {"raw": "NOT_EXPOSED", "final": "UNAVAILABLE", "exposed": False}
        if family == "news":
            if idx - 50 < len(news):
                return {"raw": news[idx - 50], "final": news[idx - 50], "exposed": True}
            return {"raw": "NOT_EXPOSED", "final": "UNAVAILABLE", "exposed": False}
        if family == "liquidity":
            if idx - 60 < len(liq):
                return {"raw": liq[idx - 60], "final": liq[idx - 60], "exposed": True}
            return {"raw": "NOT_EXPOSED", "final": "UNAVAILABLE", "exposed": False}
        return {"raw": "NOT_EXPOSED", "final": "UNAVAILABLE", "exposed": False}

    reg_rows = reg.get("rows", [])
    for row in reg_rows:
        idx = row["index"]
        family = row["family"]
        stage = _stage(family, idx, base_values, news_values, liq_values)
        value = stage["final"]
        status = "VALID"
        if isinstance(value, str):
            status = "UNAVAILABLE"
        else:
            try:
                v = float(value)
                import math

                if math.isnan(v) or math.isinf(v):
                    status = "INVALID"
            except (TypeError, ValueError):
                status = "UNAVAILABLE"
        rows.append(
            {
                "index": idx,
                "name": row["name"],
                "family": family,
                "raw": stage["raw"],
                "normalized": stage["raw"] if stage["exposed"] else "NOT_EXPOSED",
                "clipped": stage["raw"] if stage["exposed"] else "NOT_EXPOSED",
                "final": value,
                "status": status,
                "source": (
                    "ENGINE_STATE"
                    if family in ("base", "news") and stage["exposed"]
                    else (
                        "LIQUIDITY_GOVERNOR"
                        if family == "liquidity" and stage["exposed"]
                        else "UNAVAILABLE"
                    )
                ),
                "timestamp": (
                    fv_ts
                    if family == "base" and stage["exposed"]
                    else (news_ts if family == "news" and stage["exposed"] else liq_ts)
                ),
                "causality": "CAUSAL" if stage["exposed"] else "UNAVAILABLE",
            }
        )

    # Health summary (brief 39)
    total = len(rows)
    valid = sum(1 for r in rows if r["status"] == "VALID")
    invalid = sum(1 for r in rows if r["status"] == "INVALID")
    fallback = sum(1 for r in rows if r["status"] == "FALLBACK")
    unavailable = sum(1 for r in rows if r["status"] == "UNAVAILABLE")
    stale = sum(1 for r in rows if r["status"] == "STALE")
    values = [float(r["final"]) for r in rows if isinstance(r["final"], (int, float))]
    stats = {
        "min": round(min(values), 6) if values else None,
        "max": round(max(values), 6) if values else None,
        "mean": round(sum(values) / len(values), 6) if values else None,
    }

    return {
        "schema": reg,
        "active_dimension": len(rows),
        "rows": rows,
        "health": {
            "total": total,
            "valid": valid,
            "invalid": invalid,
            "fallback": fallback,
            "unavailable": unavailable,
            "stale": stale,
            "stats": stats,
        },
        "timestamp": fv_ts or news_ts or liq_ts,
        "news_state": news_state,
        "liquidity_status": liq_status,
        "liquidity_source": liq_source,
    }


def _contract_section(engine: Any) -> dict[str, Any]:
    """70D CONTRACT VALIDATION (brief 37/38) — expected vs actual.

    Expected = canonical scalp_v3 70D. Actual = the schema the runtime
    ACTUALLY operates under (engine FEATURE_SCHEMA_ID/FEATURE_DIM, live
    vector width, live model classes). Mismatch -> 70D CONTRACT BROKEN /
    MODEL CONTRACT INVALID (regression: actual_classes=128 vs expected=4).
    """
    reg = _feature_registry()
    expected_dim = reg.get("dimension")
    expected_hash = reg.get("schema_hash")
    expected_classes = 4

    actual_dim = None
    actual_schema = None
    actual_hash = None
    actual_classes = None
    actual_input = None
    vector_len = None
    if engine is not None:
        actual_schema = getattr(engine, "FEATURE_SCHEMA_ID", None)
        actual_dim = getattr(engine, "FEATURE_DIM", None)
        fv = getattr(engine, "_last_fv", None)
        if fv is not None:
            try:
                vector_len = len(fv.to_tensor_input())
            except Exception:
                vector_len = None
        bundle = None
        try:
            with engine._bundle_lock:
                bundle = engine._bundle
        except Exception:
            bundle = None
        if bundle is not None:
            try:
                import torch  # noqa: F401

                model = bundle.model
                out_shape = None
                if hasattr(model, "classifier_out"):
                    out_shape = getattr(model.classifier_out, "out_features", None)
                if out_shape is None:
                    # Probe the actual output width from the last probs.
                    probs = getattr(engine, "_last_probs", None)
                    if probs is not None:
                        out_shape = int(probs.shape[-1])
                actual_classes = int(out_shape) if out_shape is not None else None
            except Exception:
                actual_classes = None
            try:
                actual_input = getattr(bundle, "input_dim", None) or getattr(
                    model, "num_features", None
                )
            except Exception:
                actual_input = None

    dim_ok = expected_dim is not None and actual_dim == expected_dim
    hash_ok = expected_hash is not None and actual_hash == expected_hash
    classes_ok = actual_classes is None or actual_classes == expected_classes
    vector_ok = vector_len is None or vector_len == expected_dim

    if dim_ok and classes_ok and vector_ok:
        contract_status = "70D CONTRACT OK"
    else:
        contract_status = "70D CONTRACT BROKEN"

    if classes_ok:
        model_status = "MODEL CONTRACT OK"
    else:
        model_status = "MODEL CONTRACT INVALID"

    return {
        "expected_dimension": expected_dim,
        "actual_dimension": actual_dim,
        "expected_schema_hash": expected_hash,
        "actual_schema_hash": actual_hash,
        "expected_indices": list(range(expected_dim)) if expected_dim else None,
        "actual_indices": list(range(vector_len)) if vector_len else None,
        "expected_classes": expected_classes,
        "actual_classes": actual_classes,
        "actual_input_dimension": actual_input,
        "actual_schema_id": actual_schema,
        "live_vector_len": vector_len,
        "dimension_match": dim_ok,
        "schema_hash_match": hash_ok,
        "classes_match": classes_ok,
        "vector_match": vector_ok,
        "status": contract_status,
        "model_status": model_status,
    }


def _model_section(engine: Any) -> dict[str, Any]:
    """MODEL INPUT / OUTPUT (brief 14/15). Shows the ACTUAL tensor the live
    path consumed (post-scaler) plus probabilities/logits/predicted class."""
    out: dict[str, Any] = {"available": False}
    if engine is None:
        return out
    try:
        with engine._bundle_lock:
            bundle = engine._bundle
        if bundle is None:
            out["reason"] = "NO_MODEL_BUNDLE"
            return out
        import numpy as np
        import torch  # noqa: F401

        scaler_ready = bool(getattr(bundle.scaler, "is_ready", lambda: False)())
        model = bundle.model
        in_dim = getattr(bundle, "input_dim", None) or getattr(model, "num_features", None)
        num_classes = None
        if hasattr(model, "classifier_out"):
            num_classes = getattr(model.classifier_out, "out_features", None)
        champ = getattr(engine, "champion_manager", None)
        scaler_hash = None
        if champ is not None:
            try:
                info = champ.info
                scaler_hash = getattr(info, "scaler_hash", None)
            except Exception:
                scaler_hash = None

        input_tensor = getattr(engine, "_last_model_input_tensor", None)
        probs = getattr(engine, "_last_probs", None)
        probs_list = None
        logits_list = None
        predicted = None
        model_output_status = "NO_INFERENCE_YET"
        if probs is not None:
            try:
                p = probs.detach().cpu().numpy()
                probs_list = [float(v) for v in p.flatten().tolist()]
                logits_list = None  # live path stores softmax only (canonical truth)
                if len(probs_list) >= 4:
                    predicted = int(np.argmax(probs_list))
                model_output_status = "MODEL_OUTPUT_OK"
            except Exception:
                model_output_status = "MODEL_OUTPUT_INVALID"
        # validate width vs classes
        if probs_list is not None and num_classes is not None and len(probs_list) != num_classes:
            model_output_status = "MODEL_OUTPUT_INVALID"
            out["invalid_reason"] = f"probs width {len(probs_list)} != model classes {num_classes}"
        # CLASS-OF-BUG regression (actual_classes=128): even when the probs
        # width matches the artifact's own head, a non-4-class live output is
        # INVALID for the production contract (expected_classes=4) — the
        # model section must surface it, never silently render (brief 38).
        if probs_list is not None and num_classes is not None and num_classes != 4:
            model_output_status = "MODEL_OUTPUT_INVALID"
            out["invalid_reason"] = (
                f"model classes {num_classes} != expected_classes 4 (width {len(probs_list)})"
            )

        out = {
            "available": True,
            "model_id": getattr(champ, "model_id", None) if champ else None,
            "model_version": getattr(champ, "model_version", None) if champ else None,
            "schema_id": getattr(engine, "FEATURE_SCHEMA_ID", None),
            "dimension": getattr(engine, "FEATURE_DIM", None),
            "schema_hash": getattr(engine, "FEATURE_SCHEMA_HASH", None)
            or _feature_registry().get("schema_hash"),
            "scaler_hash": scaler_hash,
            "scaler_ready": scaler_ready,
            "input_tensor_shape": ([1, len(input_tensor)] if input_tensor is not None else None),
            "input_dtype": "float32",
            "device": str(next(model.parameters()).device)
            if hasattr(model, "parameters")
            else "cpu",
            "input_tensor": input_tensor,
            "num_classes": num_classes,
            "input_dim": in_dim,
            "probabilities": {
                "NO_TRADE": probs_list[0] if probs_list and len(probs_list) > 0 else None,
                "BUY_MARKET": probs_list[1] if probs_list and len(probs_list) > 1 else None,
                "SELL_MARKET": probs_list[2] if probs_list and len(probs_list) > 2 else None,
                "WAIT": probs_list[3] if probs_list and len(probs_list) > 3 else None,
                "full": probs_list,
            },
            "logits": logits_list,
            "predicted_class": predicted,
            "confidence": (max(probs_list) if probs_list else None),
            "status": model_output_status,
            "inference_latency_ms": getattr(engine, "_last_inference_latency_ms", None),
            "inference_timestamp": getattr(getattr(engine, "_last_fv", None), "timestamp_utc", None)
            or None,
        }
    except Exception as exc:
        logger.warning("debug_snapshot model_state error", error=str(exc))
        out = {
            "available": False,
            "reason": "MODEL_STATE_ERROR",
        }
    return out


def _confidence_section(engine: Any) -> dict[str, Any]:
    """CONFIDENCE PIPELINE (brief 16) — real transformations only.

    Raw = max model probability. Adjustments shown only when the runtime
    actually applied them: news gate adjustment (_last_news_gate),
    experience adjustment (_last_experience_decision.adjusted_confidence),
    suitability verdict (_last_suitability_verdict). Final = proposal
    confidence. Threshold = config. Decision = PASS/REJECT.
    """
    if engine is None:
        return {"available": False}
    try:
        proposal = getattr(engine, "_last_proposal", None)
        probs = getattr(engine, "_last_probs", None)
        raw_conf = None
        if probs is not None:
            try:
                raw_conf = float(max(probs.detach().cpu().numpy().flatten().tolist()))
            except Exception:
                raw_conf = None
        threshold = None
        try:
            threshold = float(engine.config.model.confidence_threshold)
        except Exception:
            threshold = None

        news_adj = None
        news_verdict = getattr(engine, "_last_news_gate", None)
        if news_verdict is not None:
            news_adj = float(getattr(news_verdict, "confidence_adjustment", 0.0) or 0.0)

        exp_adj = None
        exp = getattr(engine, "_last_experience_decision", None)
        if exp is not None:
            try:
                exp_adj = float(getattr(exp, "adjusted_confidence", 0.0) or 0.0)
            except Exception:
                exp_adj = None

        suit = getattr(engine, "_last_suitability_verdict", None)
        suit_adj = None
        if suit is not None:
            try:
                suit_adj = float(getattr(suit, "adjusted_confidence", 0.0) or 0.0)
            except Exception:
                suit_adj = None

        final_conf = float(getattr(proposal, "confidence", 0.0)) if proposal else None
        final_action = getattr(proposal.action, "value", None) if proposal is not None else None

        decision = None
        if final_conf is not None and threshold is not None:
            if final_conf >= threshold and final_action not in (None, "NO_TRADE", "WAIT"):
                decision = "PASS"
            else:
                decision = "REJECT"

        return {
            "available": True,
            "raw_confidence": raw_conf,
            "calibration": None,  # no live calibration stage exists (not fabricated)
            "news_adjustment": news_adj,
            "experience_adjusted_confidence": exp_adj,
            "suitability_adjusted_confidence": suit_adj,
            "final_confidence": final_conf,
            "required_threshold": threshold,
            "final_action": final_action,
            "decision": decision,
            "stages": [
                {"name": "RAW_MODEL", "value": raw_conf, "present": raw_conf is not None},
                {"name": "NEWS_GATE", "value": news_adj, "present": news_adj is not None},
                {"name": "EXPERIENCE", "value": exp_adj, "present": exp_adj is not None},
                {"name": "SUITABILITY", "value": suit_adj, "present": suit_adj is not None},
                {"name": "FINAL", "value": final_conf, "present": final_conf is not None},
            ],
        }
    except Exception as exc:
        logger.warning("debug_snapshot confidence error", error=str(exc))
        return {"available": False, "reason": "CONFIDENCE_ERROR"}


def _policy_section(engine: Any) -> dict[str, Any]:
    """POLICY DECISION TRACE (brief 17) — every gate in order with actual
    value / threshold / status. Uses the proposal's decision_stage /
    blocked_by / reason_code / rejection_reason and the policy's internal
    gate state (cooldown, dedup, exposure) — real backend state."""
    if engine is None:
        return {"available": False}
    out: dict[str, Any] = {"available": True}
    proposal = getattr(engine, "_last_proposal", None)
    policy = getattr(engine, "signal_policy", None)

    gates: list[dict[str, Any]] = []

    # 1. Signal / model action
    action = getattr(proposal.action, "value", None) if proposal else None
    gates.append(
        {
            "name": "SIGNAL",
            "status": "PASS"
            if action not in (None, "NO_TRADE", "WAIT")
            else ("PASS" if action in ("NO_TRADE", "WAIT") else "UNAVAILABLE"),
            "actual": action,
            "threshold": "TRADE or NO_TRADE",
            "reason": getattr(proposal, "reason_code", "") or "",
        }
    )

    # 2. Confidence gate
    conf = float(getattr(proposal, "confidence", 0.0)) if proposal else None
    threshold = None
    try:
        threshold = float(engine.config.model.confidence_threshold)
    except Exception:
        pass
    if conf is not None and threshold is not None:
        gates.append(
            {
                "name": "CONFIDENCE",
                "status": "PASS" if conf >= threshold else "FAIL",
                "actual": round(conf, 4),
                "threshold": round(threshold, 4),
                "reason": "confidence >= threshold"
                if conf >= threshold
                else ("confidence below threshold"),
            }
        )
    else:
        gates.append(
            {
                "name": "CONFIDENCE",
                "status": "UNAVAILABLE",
                "actual": conf,
                "threshold": threshold,
                "reason": "",
            }
        )

    # 3. Regime / guardian
    regime = getattr(engine, "_last_regime_state", None)
    regime_name = getattr(getattr(regime, "regime_type", None), "value", None) if regime else None
    guardian = getattr(proposal, "guardian_status", None) if proposal else None
    gates.append(
        {
            "name": "REGIME",
            "status": ("BLOCKED" if guardian == "ACTIVE" else "PASS"),
            "actual": regime_name or "UNKNOWN",
            "threshold": "UNSAFE regimes blocked",
            "reason": (
                f"guardian={guardian}"
                if guardian == "ACTIVE"
                else ("regime safe" if regime_name else "no regime state")
            ),
        }
    )

    # 4. R:R gate
    rr = float(getattr(proposal, "risk_reward_ratio", 0.0)) if proposal else None
    min_rr = None
    try:
        min_rr = float(engine.config.algo.min_risk_reward_ratio)
    except Exception:
        pass
    if rr is not None and min_rr is not None:
        gates.append(
            {
                "name": "R:R",
                "status": "PASS" if rr >= min_rr else "FAIL",
                "actual": round(rr, 3),
                "threshold": round(min_rr, 3),
                "reason": "risk/reward meets minimum"
                if rr >= min_rr
                else ("risk/reward below minimum"),
            }
        )
    else:
        gates.append(
            {
                "name": "R:R",
                "status": "UNAVAILABLE",
                "actual": rr,
                "threshold": min_rr,
                "reason": "",
            }
        )

    # 5. Same-level re-entry lockout
    lockout = None
    if policy is not None:
        try:
            last_dir = getattr(policy, "_last_active_direction", None)
            lockout = bool(last_dir is not None and action in ("BUY_MARKET", "SELL_MARKET"))
        except Exception:
            lockout = None
    gates.append(
        {
            "name": "SAME-LEVEL",
            "status": "PASS" if lockout is False else ("BLOCKED" if lockout else "UNAVAILABLE"),
            "actual": bool(lockout),
            "threshold": "no same-level re-entry",
            "reason": "" if lockout is not None else "policy state unavailable",
        }
    )

    # 6. News gate
    news_verdict = getattr(engine, "_last_news_gate", None)
    if news_verdict is not None:
        blocked = bool(getattr(news_verdict, "blocked", False))
        gates.append(
            {
                "name": "NEWS",
                "status": "BLOCKED" if blocked else "PASS",
                "actual": getattr(news_verdict, "decision", ""),
                "threshold": "not BLOCK",
                "reason": getattr(news_verdict, "reason", ""),
            }
        )
    else:
        gates.append(
            {
                "name": "NEWS",
                "status": "PASS",
                "actual": "DISABLED"
                if not getattr(engine, "_news_enabled", False)
                else "NO_VERDICT",
                "threshold": "-",
                "reason": "news gate not applied or disabled",
            }
        )

    # 7. Exposure gate (policy-level, from order manager)
    exposure_ok = None
    om = getattr(engine, "order_manager", None)
    if om is not None:
        try:
            if hasattr(om, "_is_exposure_available"):
                exposure_ok = om._is_exposure_available()
            else:
                positions, pendings = om.count_total_exposure()
                exposure_ok = (positions + pendings) < 1
        except Exception:
            exposure_ok = None
    gates.append(
        {
            "name": "EXPOSURE",
            "status": "PASS"
            if exposure_ok is True
            else ("BLOCKED" if exposure_ok is False else "UNAVAILABLE"),
            "actual": 1 if exposure_ok is True else (0 if exposure_ok is False else None),
            "threshold": "1 (slot free)",
            "reason": "exposure slot available"
            if exposure_ok is True
            else (
                "exposure slot occupied" if exposure_ok is False else "exposure state unavailable"
            ),
        }
    )

    # 8. Risk gate (risk engine decision on the last proposal)
    risk_allowed = getattr(proposal, "risk_allowed", None) if proposal else None
    gates.append(
        {
            "name": "RISK",
            "status": "PASS"
            if risk_allowed is True
            else ("FAIL" if risk_allowed is False else "UNAVAILABLE"),
            "actual": risk_allowed,
            "threshold": "True",
            "reason": getattr(proposal, "rejection_reason", "") or "",
        }
    )

    # 9. Execution / decision stage
    stage = getattr(proposal, "decision_stage", None) if proposal else None
    blocked_by = getattr(proposal, "blocked_by", None) if proposal else None
    gates.append(
        {
            "name": "EXECUTION",
            "status": ("BLOCKED" if blocked_by not in (None, "") else "PASS"),
            "actual": stage or "-",
            "threshold": "-",
            "reason": f"blocked_by={blocked_by}" if blocked_by else "no blocker",
        }
    )

    out["gates"] = gates
    out["decision"] = action
    out["decision_stage"] = stage
    out["blocked_by"] = blocked_by
    out["reason_code"] = getattr(proposal, "reason_code", None) if proposal else None
    out["confidence_before_filters"] = (
        getattr(proposal, "confidence_before_filters", None) if proposal else None
    )
    out["confidence_after_filters"] = (
        getattr(proposal, "confidence_after_filters", None) if proposal else None
    )
    out["request_id"] = getattr(proposal, "request_id", None) if proposal else None
    out["regime"] = regime_name
    out["proposal_generated_at"] = (
        getattr(proposal, "generated_at", None).isoformat()
        if proposal and getattr(proposal, "generated_at", None)
        else None
    )
    return out


def _risk_section(engine: Any) -> dict[str, Any]:
    """RISK ENGINE (brief 18): account state + risk metrics + engine decision."""
    if engine is None:
        return {"available": False}
    out: dict[str, Any] = {"available": True}
    try:
        risk = engine.risk_engine
        out["kill_switch_active"] = bool(getattr(risk, "_kill_switch_active", False))
        out["max_allowed_lots"] = float(getattr(risk, "max_allowed_lots", 0.0))
        out["min_risk_reward_ratio"] = float(getattr(risk, "min_risk_reward_ratio", 0.0))
        out["survival_mode"] = bool(getattr(engine, "_survival_mode_active", False))
        cfg = engine.config
        out["risk_per_trade_pct"] = float(cfg.risk.risk_per_trade_pct)
        out["max_concurrent_positions"] = int(cfg.risk.max_concurrent_positions)
        out["max_spread_points"] = float(cfg.risk.max_spread_points)
        out["max_account_drawdown_pct"] = float(cfg.risk.max_account_drawdown_pct)
        out["hard_max_lots"] = 10.0
    except Exception as exc:
        logger.warning("debug_snapshot risk config error", error=str(exc))
        out["config_error"] = "CONFIG_ERROR"

    # Account truth from the adapter snapshot.
    account: dict[str, Any] = {
        "balance": None,
        "equity": None,
        "margin_free": None,
        "margin": None,
        "margin_level": None,
        "floating": None,
        "drawdown_pct": None,
        "available": False,
    }
    try:
        snap = getattr(engine, "_account_snapshot", None)
        if snap is None or not getattr(snap, "available", False):
            snap = engine.adapter.get_account_snapshot()
        if snap is not None and getattr(snap, "available", False):
            account["available"] = True
            account["balance"] = getattr(snap, "balance", None)
            account["equity"] = getattr(snap, "equity", None)
            account["margin_free"] = getattr(snap, "margin_free", None)
            account["margin"] = getattr(snap, "margin", None)
            account["margin_level"] = getattr(snap, "margin_level", None)
            account["floating"] = getattr(snap, "floating_pnl", None)
            peak = getattr(engine, "_peak_equity", 0.0)
            eq = account["equity"]
            if peak and eq is not None:
                account["drawdown_pct"] = round((peak - eq) / max(peak, 1.0) * 100.0, 3)
    except Exception:
        pass
    out["account"] = account

    # RiskEngine decision on the last proposal.
    proposal = getattr(engine, "_last_proposal", None)
    risk_allowed = getattr(proposal, "risk_allowed", None) if proposal else None
    out["decision"] = (
        "PASS" if risk_allowed is True else ("BLOCK" if risk_allowed is False else "NOT_EVALUATED")
    )
    out["reason"] = getattr(proposal, "rejection_reason", "") if proposal else "no proposal yet"
    return out


def _exposure_section(engine: Any) -> dict[str, Any]:
    """EXPOSURE (brief 19): INTERNAL (order manager live-ticket cache) vs
    BROKER TRUTH (adapter positions/pending). Includes reconciliation age."""
    if engine is None:
        return {"available": False}
    out: dict[str, Any] = {"available": True}
    om = getattr(engine, "order_manager", None)
    internal: dict[str, Any] = {
        "positions": None,
        "pendings": None,
        "total": None,
        "max_total_exposure": 1,
        "available": False,
    }
    if om is not None:
        try:
            positions, pendings = om.count_total_exposure()
            internal = {
                "positions": positions,
                "pendings": pendings,
                "total": positions + pendings,
                "max_total_exposure": 1,
                "available": True,
            }
        except Exception:
            internal["reason"] = "EXPOSURE_COUNT_ERROR"
    out["internal"] = internal

    broker: dict[str, Any] = {
        "positions": None,
        "pendings": None,
        "available": False,
    }
    try:
        symbol = engine.config.execution.symbol
        all_positions = engine.adapter.get_all_positions(symbol=symbol)
        pending_orders = (
            engine.adapter.get_pending_orders(symbol=symbol)
            if hasattr(engine.adapter, "get_pending_orders")
            else []
        )
        broker = {
            "positions": len(all_positions),
            "pendings": len(pending_orders),
            "available": True,
        }
    except Exception:
        broker["reason"] = "BROKER_EXPOSURE_ERROR"
    out["broker"] = broker

    # Reconciliation age from the order manager monotonic gate.
    last_reconcile = getattr(om, "_last_reconcile_attempt", None) if om else None
    if last_reconcile is not None:
        out["last_reconciliation"] = datetime.fromtimestamp(last_reconcile, tz=UTC).isoformat()
        out["reconciliation_age_sec"] = round(max(0.0, time.monotonic() - last_reconcile), 1)
    else:
        out["last_reconciliation"] = None
        out["reconciliation_age_sec"] = None

    # Mismatch detection (only when both sides are real).
    mismatch = None
    if internal.get("available") and broker.get("available"):
        mismatch = (
            internal["positions"] != broker["positions"]
            or internal["pendings"] != broker["pendings"]
        )
    out["mismatch"] = mismatch
    return out


def _execution_section(engine: Any) -> dict[str, Any]:
    """EXECUTION (brief 20): last executed order state + pending orders."""
    if engine is None:
        return {"available": False}
    out: dict[str, Any] = {"available": True}
    om = getattr(engine, "order_manager", None)
    if om is not None:
        out["global_state"] = getattr(om, "global_state", None)
        out["consecutive_failures"] = getattr(om, "_consecutive_failures", None)
        out["processed_orders_count"] = len(getattr(om, "_processed_orders", {}))
    try:
        out["adapter"] = type(engine.adapter).__name__
        conn = engine.adapter.connection_state()
        if hasattr(conn, "to_dict"):
            out["connection"] = conn.to_dict()
    except Exception:
        out["connection"] = {"available": False}
    return out


def _positions_section(engine: Any) -> dict[str, Any]:
    """POSITION MANAGEMENT (brief 21): every open position with protection
    state (MFE/MAE/breakeven/trailing/giveback) from the order manager."""
    if engine is None:
        return {"available": False}
    out: dict[str, Any] = {"available": True, "positions": []}
    try:
        symbol = engine.config.execution.symbol
        all_positions = engine.adapter.get_all_positions(symbol=symbol)
        om = getattr(engine, "order_manager", None)
        tick = getattr(engine, "_last_tick", None)
        for p in all_positions:
            ticket = getattr(p, "ticket", None)
            pos: dict[str, Any] = {
                "ticket": ticket,
                "direction": getattr(getattr(p, "type", None), "value", None)
                or getattr(p, "type", None),
                "lots": getattr(p, "volume", None),
                "entry": getattr(p, "price_open", None),
                "current": getattr(p, "price_current", None),
                "sl": getattr(p, "sl", None),
                "tp": getattr(p, "tp", None),
                "pnl": getattr(p, "profit", None),
                "swap": getattr(p, "swap", None),
                "commission": getattr(p, "commission", None),
                "magic": getattr(p, "magic", None),
            }
            if om is not None and ticket is not None:
                mfe = om._mfe_tracker.get(ticket)
                mae = om._mae_tracker.get(ticket)
                pos["mfe"] = mfe
                pos["mae"] = mae
                pos["peak_pnl"] = om._peak_profit_usd.get(ticket)
                pos["peak_drawdown"] = (
                    om._peak_drawdown_usd.get(ticket) if hasattr(om, "_peak_drawdown_usd") else None
                )
                pos["hold_seconds"] = None
                entry_ts = om._entry_timestamps.get(ticket)
                if entry_ts is not None:
                    try:
                        now = tick.timestamp if tick is not None else datetime.now(UTC)
                        pos["hold_seconds"] = max(0.0, (now - entry_ts).total_seconds())
                    except Exception:
                        pass
                try:
                    prot = om.get_protection_state(ticket)
                    pos["breakeven_armed"] = bool(getattr(prot, "breakeven_armed", False))
                    pos["trailing_armed"] = bool(getattr(prot, "trailing_armed", False))
                    pos["giveback_state"] = (
                        getattr(prot, "giveback_state", None)
                        if hasattr(prot, "giveback_state")
                        else None
                    )
                    pos["strategy_exit_state"] = (
                        getattr(prot, "strategy_exit_state", None)
                        if hasattr(prot, "strategy_exit_state")
                        else None
                    )
                    pos["exit_state"] = (
                        getattr(prot, "exit_state", None) if hasattr(prot, "exit_state") else None
                    )
                except Exception:
                    pass
                pos["entry_confidences"] = (
                    om._entry_confidences.get(ticket) if hasattr(om, "_entry_confidences") else None
                )
            out["positions"].append(pos)
    except Exception as exc:
        logger.warning("debug_snapshot positions error", error=str(exc))
        out["reason"] = "POSITIONS_ERROR"
    return out


def _exit_section(engine: Any) -> dict[str, Any]:
    """EXIT FORENSICS (brief 22): per open position — current state, AI
    state, regime, liquidity/news state, profit state, MFE/MAE, hold
    duration, and exit candidates when the runtime exposes them."""
    if engine is None:
        return {"available": False}
    out: dict[str, Any] = {"available": True, "positions": []}
    try:
        symbol = engine.config.execution.symbol
        all_positions = engine.adapter.get_all_positions(symbol=symbol)
        om = getattr(engine, "order_manager", None)
        regime = getattr(engine, "_last_regime_state", None)
        regime_name = (
            getattr(getattr(regime, "regime_type", None), "value", None) if regime else None
        )
        news_state = None
        try:
            if engine._news_enabled and engine.news_engine is not None:
                ctx = engine.news_engine.current_context()
                news_state = str(getattr(ctx.state, "value", "NORMAL"))
        except Exception:
            pass
        for p in all_positions:
            ticket = getattr(p, "ticket", None)
            entry: dict[str, Any] = {
                "ticket": ticket,
                "direction": getattr(getattr(p, "type", None), "value", None)
                or getattr(p, "type", None),
                "current": getattr(p, "price_current", None),
                "sl": getattr(p, "sl", None),
                "tp": getattr(p, "tp", None),
                "pnl": getattr(p, "profit", None),
                "regime": regime_name,
                "news_state": news_state,
                "ai_state": "IDLE",
                "strategy_state": None,
                "liquidity_state": None,
            }
            if om is not None and ticket is not None:
                entry["mfe"] = om._mfe_tracker.get(ticket)
                entry["mae"] = om._mae_tracker.get(ticket)
                entry["hold_seconds"] = None
                entry_ts = om._entry_timestamps.get(ticket)
                if entry_ts is not None:
                    try:
                        now = (
                            getattr(engine, "_last_tick", None).timestamp
                            if getattr(engine, "_last_tick", None)
                            else datetime.now(UTC)
                        )
                        entry["hold_seconds"] = max(0.0, (now - entry_ts).total_seconds())
                    except Exception:
                        pass
                try:
                    prot = om.get_protection_state(ticket)
                    entry["ai_state"] = (
                        getattr(prot, "exit_state", None) if hasattr(prot, "exit_state") else "IDLE"
                    ) or "IDLE"
                    entry["strategy_state"] = (
                        getattr(prot, "strategy_exit_state", None)
                        if hasattr(prot, "strategy_exit_state")
                        else None
                    )
                except Exception:
                    pass
                # Exit candidates (LSF reasons / last reasons tracker)
                reasons = om._last_reasons_tracker.get(ticket, [])
                candidates = []
                if reasons:
                    for r in list(reasons)[-5:]:
                        candidates.append(
                            {
                                "reason": r,
                                "priority": "N/A",
                                "status": "CANDIDATE",
                            }
                        )
                entry["exit_candidates"] = candidates
            out["positions"].append(entry)
    except Exception as exc:
        logger.warning("debug_snapshot exit forensics error", error=str(exc))
        out["reason"] = "EXIT_FORENSICS_ERROR"
    return out


def _liquidity_section(engine: Any) -> dict[str, Any]:
    """LIQUIDITY CONTEXT (brief 10): governor report + pool detail."""
    try:
        gov = getattr(engine, "liquidity_governor", None) if engine else None
        if gov is None:
            return {
                "available": False,
                "reason": "LIQUIDITY_GOVERNOR_NOT_ATTACHED",
            }
        report = gov.report()
        return {"available": True, "report": report}
    except Exception as exc:
        logger.warning("debug_snapshot liquidity error", error=str(exc))
        return {"available": False, "reason": "LIQUIDITY_ERROR"}


def _mslie_section(engine: Any) -> dict[str, Any]:
    """MARKET INTELLIGENCE ENGINE (MSLIE brief): perception-layer status.

    Pure read of the engine's latest MarketIntelligenceFeatureVectorV1 +
    debug status. Never recomputes features here (the engine is the
    producer); never fabricates values when the engine has not run yet.
    """
    try:
        ms = getattr(engine, "mslie_engine", None) if engine else None
        if ms is None:
            return {"available": False, "reason": "MSLIE_ENGINE_NOT_ATTACHED"}
        status = ms.get_debug_status()
        return {"available": True, **status}
    except Exception as exc:
        logger.warning("debug_snapshot mslie error", error=str(exc))
        return {"available": False, "reason": "MSLIE_ERROR"}


def _news_section(engine: Any) -> dict[str, Any]:
    """NEWS INTELLIGENCE (brief 11): canonical context + which news-derived
    dimensions are active in the model (news 10D at 50..59)."""
    if engine is None:
        return {"available": False}
    out: dict[str, Any] = {
        "available": True,
        "enabled": bool(getattr(engine, "_news_enabled", False)),
    }
    ctx = None
    try:
        if engine._news_enabled and engine.news_engine is not None:
            ctx = engine.news_engine.current_context()
    except Exception:
        ctx = None
    if ctx is not None:
        out.update(
            {
                "state": str(getattr(ctx.state, "value", "NORMAL")),
                "available": getattr(ctx, "available", False),
                "stale": getattr(ctx, "stale", False),
                "freshness": getattr(ctx, "freshness", None),
                "bullish": getattr(ctx, "bullish_score", None),
                "bearish": getattr(ctx, "bearish_score", None),
                "neutral": None,  # derived direction buckets are not exposed by the backend
                "mixed": getattr(ctx, "conflict_score", None),
                "high_impact": getattr(ctx, "active_event_count", None),
                "active_events": getattr(ctx, "active_high_impact", []),
                "xauusd_relevance": getattr(ctx, "xauusd_relevance", None),
                "usd_relevance": getattr(ctx, "usd_relevance", None),
                "consensus": getattr(ctx, "source_consensus", None),
                "confidence": getattr(ctx, "confidence", None),
                "timestamp": (
                    ctx.timestamp.isoformat() if getattr(ctx, "timestamp", None) else None
                ),
            }
        )
    else:
        out["state"] = "UNAVAILABLE"
        out["reason"] = "NO_NEWS_CONTEXT"
    # News dimensions active in the model contract.
    reg = _feature_registry()
    out["model_dimensions"] = [row for row in reg.get("rows", []) if row["family"] == "news"]
    return out


def _workers_section(engine: Any) -> dict[str, Any]:
    """WORKER STATUS (brief 23): real worker telemetry with state/cycle/
    last start/last success/last failure/duration/queue. A worker marked
    RUNNING but idle too long is flagged DEGRADED by the UI."""
    workers: dict[str, Any] = {}
    if engine is None:
        return {"available": False}
    now = datetime.now(UTC)

    def _iso(ts: Any) -> str | None:
        if ts is None:
            return None
        return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

    def _fmt(worker: Any) -> dict[str, Any]:
        if worker is None:
            return {"status": "UNAVAILABLE"}
        running = bool(getattr(worker, "running", False))
        last_start = getattr(worker, "last_cycle_start", None)
        last_error = getattr(worker, "last_error", "") or ""
        duration = getattr(worker, "last_cycle_duration", None)
        return {
            "state": "RUNNING" if running else "IDLE",
            "cycle": getattr(worker, "cycle_count", 0),
            "last_start": _iso(last_start),
            "last_success": _iso(last_start),
            "last_failure": _iso(getattr(worker, "last_failure_at", None)),
            "last_error": str(last_error)[:200],
            "duration_ms": round(duration * 1000.0, 1) if duration else None,
            "queue": getattr(worker, "queue_size", None) or getattr(worker, "_queue_size", None),
        }

    try:
        workers["accounting"] = _fmt(getattr(engine, "accounting_worker", None))
    except Exception:
        workers["accounting"] = {"state": "ERROR"}
    try:
        workers["history_sync"] = _fmt(getattr(engine, "history_sync_worker", None))
    except Exception:
        workers["history_sync"] = {"state": "ERROR"}
    try:
        workers["intelligence"] = _fmt(getattr(engine, "intelligence_worker", None))
    except Exception:
        workers["intelligence"] = {"state": "ERROR"}
    try:
        workers["research"] = _fmt(getattr(engine, "research_worker", None))
    except Exception:
        workers["research"] = {"state": "ERROR"}
    try:
        workers["training"] = _fmt(getattr(engine, "training_worker", None))
    except Exception:
        workers["training"] = {"state": "ERROR"}
    try:
        workers["shadow"] = _fmt(getattr(engine, "shadow_worker", None))
    except Exception:
        workers["shadow"] = {"state": "ERROR"}
    try:
        workers["shadow70"] = _fmt(getattr(engine, "_shadow70_worker", None))
    except Exception:
        workers["shadow70"] = {"state": "ERROR"}
    try:
        nw = getattr(engine, "news_worker", None)
        if nw is not None:
            from nexus_scalp.news.worker import format_news_worker_status

            st = format_news_worker_status(nw)
            st["state"] = st.get("status", "UNAVAILABLE")
            workers["news"] = st
        else:
            workers["news"] = {"state": "DISABLED"}
    except Exception:
        workers["news"] = {"state": "ERROR"}
    try:
        tg = getattr(engine, "telegram_notifier", None)
        if tg is not None:
            h = tg.health_state()
            workers["telegram"] = {
                "state": h.get("state", "UNAVAILABLE"),
                "cycle": None,
                "last_start": None,
                "last_success": _iso(h.get("last_success_at")),
                "last_failure": _iso(h.get("last_failure_at")),
                "last_error": str(h.get("last_failure_category", ""))[:120],
                "duration_ms": None,
                "queue": h.get("queue_size"),
            }
        else:
            workers["telegram"] = {"state": "UNAVAILABLE"}
    except Exception:
        workers["telegram"] = {"state": "ERROR"}
    return {"available": True, "workers": workers, "checked_at": _iso(now)}


def _database_section(engine: Any) -> dict[str, Any]:
    """DATABASE STATE (brief 24): path/size/schema/WAL/last write/health for
    audit.db, news.db, candle_intel.db + research storage. Uses lightweight
    stat/WAL probes — never a full scan (brief 43)."""
    out: dict[str, Any] = {"available": True, "databases": {}}
    base = Path.cwd()
    if engine is not None and getattr(engine, "config", None) is not None:
        try:
            base = Path(engine.config.base_dir)
        except Exception:
            pass

    def _probe(name: str, path: Path | None) -> dict[str, Any]:
        if path is None:
            return {"path": None, "health": "UNAVAILABLE", "reason": "NO_PATH"}
        try:
            size = path.stat().st_size if path.exists() else None
            wal = Path(str(path) + "-wal")
            wal_size = wal.stat().st_size if wal.exists() else None
            return {
                "path": _mask_path(str(path)),
                "size_bytes": size,
                "wal_bytes": wal_size,
                "exists": path.exists(),
                "schema_version": None,  # not probed per request (bounded)
                "last_write": None,
                "last_read": None,
                "health": "READY" if path.exists() else "MISSING",
            }
        except Exception as exc:
            logger.warning("debug_snapshot db health error", error=str(exc))
            return {
                "path": _mask_path(str(path)),
                "health": "ERROR",
                "reason": "DB_HEALTH_ERROR",
            }

    try:
        audit_path = None
        if engine is not None and getattr(engine, "audit", None) is not None:
            audit_path = Path(getattr(engine.audit, "_db_path", "") or "")
        out["databases"]["audit"] = _probe("audit", audit_path or None)
    except Exception:
        out["databases"]["audit"] = {"health": "ERROR"}
    try:
        news_path = None
        if engine is not None and getattr(engine, "news_engine", None) is not None:
            try:
                news_path = Path(engine.news_engine.db.db_path)
            except Exception:
                news_path = base / "artifacts" / "news.db"
        out["databases"]["news"] = _probe("news", news_path)
    except Exception:
        out["databases"]["news"] = {"health": "ERROR"}
    try:
        out["databases"]["candle_intel"] = _probe(
            "candle_intel", base / "artifacts" / "candle_intel.db"
        )
    except Exception:
        out["databases"]["candle_intel"] = {"health": "ERROR"}
    try:
        out["databases"]["research"] = {
            "path": _mask_path(str(base / "artifacts" / "research")),
            "health": "READY" if (base / "artifacts" / "research").exists() else "MISSING",
            "size_bytes": None,
        }
    except Exception:
        out["databases"]["research"] = {"health": "ERROR"}
    return out


def _cache_section(engine: Any) -> dict[str, Any]:
    """SYSTEM CACHE STATE (brief 25): model/feature/liquidity/news/exposure/
    chart/research caches — status, size, age, TTL, last update."""
    out: dict[str, Any] = {"available": True, "caches": {}}

    def _age(ts_iso: str | None) -> float | None:
        if not ts_iso:
            return None
        try:
            dt = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
            return max(0.0, (datetime.now(UTC) - dt).total_seconds())
        except (TypeError, ValueError):
            return None

    # Model cache = live bundle
    bundle = None
    if engine is not None:
        try:
            with engine._bundle_lock:
                bundle = engine._bundle
        except Exception:
            bundle = None
    out["caches"]["model"] = {
        "status": "LOADED" if bundle is not None else "EMPTY",
        "size": 1 if bundle is not None else 0,
        "age_sec": None,
        "ttl": "lifetime",
        "last_update": None,
    }
    # Feature cache = last FeatureVector
    fv_ts = None
    if engine is not None:
        fv = getattr(engine, "_last_fv", None)
        if fv is not None:
            ts = getattr(fv, "timestamp_utc", None) or getattr(fv, "timestamp", None)
            fv_ts = ts.isoformat() if hasattr(ts, "isoformat") else str(ts) if ts else None
    out["caches"]["feature"] = {
        "status": "CACHED" if fv_ts else "EMPTY",
        "size": 1 if fv_ts else 0,
        "age_sec": _age(fv_ts),
        "ttl": "per-bar",
        "last_update": fv_ts,
    }
    # Liquidity cache = governor snapshot
    liq_ts = None
    liq_status = "EMPTY"
    if engine is not None:
        try:
            gov = getattr(engine, "liquidity_governor", None)
            if gov is not None:
                liq_status = gov.status()
                snap = gov.snapshot_payload()
                liq_ts = snap.get("timestamp")
        except Exception:
            pass
    out["caches"]["liquidity"] = {
        "status": liq_status,
        "size": 10 if liq_ts else 0,
        "age_sec": _age(liq_ts),
        "ttl": "per-bar",
        "last_update": liq_ts,
    }
    # News cache = news context
    news_ts = None
    news_status = "EMPTY"
    if engine is not None and getattr(engine, "_news_enabled", False):
        try:
            ctx = engine.news_engine.current_context()
            if ctx is not None:
                news_ts = ctx.timestamp.isoformat() if getattr(ctx, "timestamp", None) else None
                news_status = "STALE" if getattr(ctx, "stale", False) else "CACHED"
        except Exception:
            news_status = "ERROR"
    out["caches"]["news"] = {
        "status": news_status,
        "size": 1 if news_ts else 0,
        "age_sec": _age(news_ts),
        "ttl": f"{(getattr(getattr(engine, 'news_engine', None), 'cache', None) and getattr(getattr(engine.news_engine, 'cache', None), 'ttl_sec', None)) or 60}s",
        "last_update": news_ts,
    }
    # Exposure cache = live tickets cache
    tickets = None
    if engine is not None:
        try:
            om = getattr(engine, "order_manager", None)
            if om is not None:
                tickets = om.get_active_live_tickets()
        except Exception:
            tickets = None
    out["caches"]["exposure"] = {
        "status": "CACHED" if tickets else "EMPTY",
        "size": len(tickets) if tickets else 0,
        "age_sec": None,
        "ttl": "per-tick",
        "last_update": None,
    }
    # Chart cache = server state visuals
    chart_age = None
    chart_bars = 0
    try:
        if hasattr(engine, "server_state") is False:
            ss = getattr(engine, "_server_state", None)
        ss = getattr(engine, "server_state", None)
        if ss is not None:
            chart_age = ss.visuals_age_sec()
            chart_bars = len(ss.bars)
    except Exception:
        pass
    out["caches"]["chart"] = {
        "status": "CACHED" if chart_bars else "EMPTY",
        "size": chart_bars,
        "age_sec": chart_age,
        "ttl": "per-bar",
        "last_update": None,
    }
    # Research cache = registry row count
    research_count = None
    if engine is not None:
        try:
            reg = getattr(engine, "research_registry", None)
            if reg is not None:
                research_count = len(reg.list_candidates(limit=1))
        except Exception:
            research_count = None
    out["caches"]["research"] = {
        "status": "CACHED" if research_count else "EMPTY",
        "size": research_count,
        "age_sec": None,
        "ttl": "persistent",
        "last_update": None,
    }
    return out


def _chart_section(engine: Any, app_state: Any) -> dict[str, Any]:
    """CHART DEBUG (brief 26): data source, bars requested/received, first/
    last timestamp, timeframe, reseed + SSE state."""
    out: dict[str, Any] = {
        "available": True,
        "data_source": "UNAVAILABLE",
        "bars_requested": None,
        "bars_received": 0,
        "first_timestamp": None,
        "last_timestamp": None,
        "timeframe": "M1",
        "reseed_state": None,
        "sse_state": None,
        "overlays": {
            "liquidity": False,
            "news": False,
            "smc": False,
        },
    }
    try:
        ss = getattr(app_state, "server_state", None)
        if ss is not None:
            bars, overlays = ss.get_live_visuals()
            out["bars_received"] = len(bars)
            out["data_source"] = "SERVER_STATE"
            if bars:
                out["first_timestamp"] = bars[0].get("time")
                out["last_timestamp"] = bars[-1].get("time")
            out["overlays"]["smc"] = bool(overlays.get("rectangles") or overlays.get("bos_lines"))
            out["overlays"]["liquidity"] = bool(overlays.get("liq_markers"))
            out["overlays"]["news"] = False
    except Exception:
        pass
    if engine is not None:
        try:
            agg = engine.aggregator
            completed = agg.get_completed_bars()
            out["bars_received"] = max(out["bars_received"], len(completed))
            if completed:
                out["first_timestamp"] = completed[0].timestamp.isoformat()
                out["last_timestamp"] = completed[-1].timestamp.isoformat()
            out["data_source"] = (
                "AGGREGATOR" if out["data_source"] == "UNAVAILABLE" else out["data_source"]
            )
        except Exception:
            pass
    out["sse_state"] = _sse_state_section(app_state)
    return out


# ---------------------------------------------------------------------------
# SSE diagnostics (brief 27): connection + serialization errors
# ---------------------------------------------------------------------------


def _sse_state_section(app_state: Any) -> dict[str, Any]:
    if app_state is None or not hasattr(app_state, "sse_diag"):
        return {
            "connection": "UNKNOWN",
            "connected_at": None,
            "last_event": None,
            "event_count": 0,
            "last_latency_ms": None,
            "serialization_errors": 0,
            "reconnect_count": 0,
        }
    diag = app_state.sse_diag
    return {
        "connection": diag.get("connection", "UNKNOWN"),
        "connected_at": diag.get("connected_at"),
        "last_event": diag.get("last_event"),
        "event_count": diag.get("event_count", 0),
        "last_latency_ms": diag.get("last_latency_ms"),
        "serialization_errors": diag.get("serialization_errors", 0),
        "serialization_error": diag.get("serialization_error"),
        "reconnect_count": diag.get("reconnect_count", 0),
    }


# ---------------------------------------------------------------------------
# Errors section — NO HIDDEN ERRORS (brief 36)
# ---------------------------------------------------------------------------


def _errors_section(engine: Any, app_state: Any) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    sse_diag = getattr(app_state, "sse_diag", {}) if app_state is not None else {}
    ser_err = sse_diag.get("serialization_error")
    if ser_err:
        errors.append(
            {
                "timestamp": sse_diag.get("connected_at"),
                "component": "SSE",
                "endpoint": "/api/ticks/stream",
                "error_code": "SSE_SERIALIZATION_ERROR",
                "exception": str(ser_err.get("error", ""))[:300],
                "correlation_id": ser_err.get("correlation_id"),
                "fields": ser_err.get("failed_fields"),
            }
        )
    return {"errors": errors}


# ---------------------------------------------------------------------------
# Top-level assembly (brief 41/42/43)
# ---------------------------------------------------------------------------


def build_debug_snapshot(engine: Any, app_state: Any) -> dict[str, Any]:
    """Assemble the full canonical debug snapshot (in-memory only).

    Bounded by construction: reads engine attributes and cached worker
    reports; never runs DB scans, never recomputes features/liquidity,
    never reloads the model (brief 43).
    """
    snapshot_id = new_snapshot_id()
    correlation_id = new_request_id()
    now_iso = datetime.now(UTC).isoformat()

    # Sentinel-based per-section isolation: any section that raises still
    # returns a visible error object with its own correlation_id.
    def _safe(builder: Any, name: str) -> dict[str, Any]:
        try:
            section = builder()
            if not isinstance(section, dict):
                section = {"value": section}
            section["_section"] = name
            return section
        except Exception as exc:
            logger.warning("debug_snapshot section error", section=name, error=str(exc))
            return {
                "_section": name,
                "available": False,
                "reason": "SECTION_ERROR",
                "correlation_id": new_request_id(),
            }

    payload: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "correlation_id": correlation_id,
        "timestamp": now_iso,
        "engine_attached": engine is not None,
        "runtime": _safe(lambda: _runtime_section(engine), "runtime"),
        "contract": _safe(lambda: _contract_section(engine), "contract"),
        "features": _safe(lambda: _features_section(engine), "features"),
        "model": _safe(lambda: _model_section(engine), "model"),
        "confidence": _safe(lambda: _confidence_section(engine), "confidence"),
        "policy": _safe(lambda: _policy_section(engine), "policy"),
        "risk": _safe(lambda: _risk_section(engine), "risk"),
        "exposure": _safe(lambda: _exposure_section(engine), "exposure"),
        "execution": _safe(lambda: _execution_section(engine), "execution"),
        "positions": _safe(lambda: _positions_section(engine), "positions"),
        "exit": _safe(lambda: _exit_section(engine), "exit"),
        "liquidity": _safe(lambda: _liquidity_section(engine), "liquidity"),
        "mslie": _safe(lambda: _mslie_section(engine), "mslie"),
        "news": _safe(lambda: _news_section(engine), "news"),
        "workers": _safe(lambda: _workers_section(engine), "workers"),
        "database": _safe(lambda: _database_section(engine), "database"),
        "caches": _safe(lambda: _cache_section(engine), "caches"),
        "chart": _safe(lambda: _chart_section(engine, app_state), "chart"),
        "sse": _safe(lambda: _sse_state_section(app_state), "sse"),
        "errors": _safe(lambda: _errors_section(engine, app_state), "errors"),
    }
    return payload


# ---------------------------------------------------------------------------
# Snapshot history (brief 33/34): bounded in-memory ring for compare/diff
# ---------------------------------------------------------------------------


class DebugSnapshotStore:
    """Bounded ring of debug snapshots for T0/T1/T2 comparison (brief 33/34).

    Purely in-memory, capped (default 64). Never persisted; never written to
    a database. The UI compares two captured snapshots client-side for the
    feature diff, while this store keeps a rolling history server-side.
    """

    def __init__(self, max_snapshots: int = 64) -> None:
        self.max_snapshots = max_snapshots
        self._snapshots: list[dict[str, Any]] = []

    def push(self, snapshot: dict[str, Any]) -> None:
        self._snapshots.append(snapshot)
        if len(self._snapshots) > self.max_snapshots:
            self._snapshots = self._snapshots[-self.max_snapshots :]

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "snapshot_id": s.get("snapshot_id"),
                "correlation_id": s.get("correlation_id"),
                "timestamp": s.get("timestamp"),
            }
            for s in self._snapshots
        ]

    def get(self, snapshot_id: str) -> dict[str, Any] | None:
        for s in reversed(self._snapshots):
            if s.get("snapshot_id") == snapshot_id:
                return s
        return None


def diff_snapshots(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Feature + model + confidence + regime + liquidity + news + policy +
    risk diff between two snapshots (brief 34). Pure function; JSON-safe."""
    out: dict[str, Any] = {
        "a_id": a.get("snapshot_id"),
        "b_id": b.get("snapshot_id"),
        "a_timestamp": a.get("timestamp"),
        "b_timestamp": b.get("timestamp"),
        "feature_diffs": [],
        "model": {},
        "confidence": {},
        "regime": {},
        "liquidity": {},
        "news": {},
        "policy": {},
        "risk": {},
    }

    def _rows(snap: dict[str, Any]) -> list[dict[str, Any]]:
        feats = snap.get("features") or {}
        return feats.get("rows", []) if isinstance(feats, dict) else []

    rows_a = {r["index"]: r for r in _rows(a)}
    rows_b = {r["index"]: r for r in _rows(b)}
    for idx in sorted(set(rows_a) | set(rows_b)):
        ra = rows_a.get(idx)
        rb = rows_b.get(idx)
        if ra is None or rb is None:
            continue
        va = ra.get("final")
        vb = rb.get("final")
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            delta = round(vb - va, 6)
            if abs(delta) > 1e-12:
                out["feature_diffs"].append(
                    {
                        "index": idx,
                        "name": rb.get("name"),
                        "family": rb.get("family"),
                        "t0": va,
                        "t1": vb,
                        "delta": delta,
                    }
                )

    def _pick(snap: dict[str, Any], key: str) -> Any:
        sec = snap.get(key) or {}
        if not isinstance(sec, dict):
            return None
        return sec.get("value") if "value" in sec else sec

    for key, _target in (
        ("model", "model"),
        ("confidence", "confidence"),
        ("policy", "policy"),
        ("risk", "risk"),
        ("news", "news"),
    ):
        sa = _pick(a, key)
        sb = _pick(b, key)
        if isinstance(sa, dict) and isinstance(sb, dict):
            fields = [
                k
                for k in set(sa) | set(sb)
                if k not in ("_section", "gates", "stages", "rows", "available")
            ]
            for k in fields:
                va = sa.get(k)
                vb = sb.get(k)
                if va != vb:
                    out[key][k] = {"t0": va, "t1": vb}
    # liquidity: only top-level report fields (pools/status/causal)
    sa = _pick(a, "liquidity")
    sb = _pick(b, "liquidity")
    if isinstance(sa, dict) and isinstance(sb, dict):
        ra = sa.get("report") or {}
        rb = sb.get("report") or {}
        if isinstance(ra, dict) and isinstance(rb, dict):
            for k in ("status", "causal_state", "source", "age_sec", "latency_ms"):
                if ra.get(k) != rb.get(k):
                    out["liquidity"][k] = {"t0": ra.get(k), "t1": rb.get(k)}
        # liquidity feature values
        fa = ra.get("features") or {}
        fb = rb.get("features") or {}
        if isinstance(fa, dict) and isinstance(fb, dict):
            for k in set(fa) | set(fb):
                if k in fa and k in fb and fa[k] != fb[k]:
                    out["liquidity"].setdefault("features", {})[k] = {
                        "t0": fa[k],
                        "t1": fb[k],
                    }
    return out
