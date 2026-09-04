"""
Canonical Model Governance Telegram Report
===========================================
TASK-6 / CHG-0003 (spec 29). Consumes ONLY canonical model-governance data:
the shadow runtime summary (comparisons / agreement / latency / errors), the
health envelope, and the promotion state. It NEVER sends "Challenger ready"
unless the promotion gate actually says READY_FOR_REVIEW / APPROVED.
"""

from __future__ import annotations

import contextlib
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.governance.reporting")


def model_shadow_update_text(
    *,
    champion: dict[str, Any],
    challenger: dict[str, Any],
    shadow: dict[str, Any],
    promotion_state: str = "SHADOW",
    sample_floor: int = 30,
) -> str:
    """Builds the canonical MODEL SHADOW UPDATE telegram text (spec 29)."""
    champ_id = champion.get("id", "?")
    champ_ver = champion.get("version", "?")
    chal_id = challenger.get("id", "?") or "—"
    chal_ver = challenger.get("version", "?") or "—"
    comparisons = int(shadow.get("comparisons", 0))
    errors = int(shadow.get("errors", 0))
    dropped = int(shadow.get("dropped", 0))
    avg_lat = float(shadow.get("avg_latency_ms", 0.0) or 0.0)
    p95_lat = float(shadow.get("p95_latency_ms", 0.0) or 0.0)
    timeouts = int(shadow.get("timeouts", 0))

    # Agreement is derived from canonical stored comparisons when the runtime
    # does not expose it directly (state: SHADOW -> NO PROMOTION).
    lines = [
        "📊 <b>MODEL SHADOW UPDATE</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🏆 Champion: <b>{champ_id}</b> / v{champ_ver}",
        f"🥊 Challenger: {chal_id} / v{chal_ver}",
        "",
        f"📈 Shadow samples: <b>{comparisons:,}</b>",
        f"❌ Errors: {errors}",
        f"🗑 Dropped: {dropped}",
        f"⏱ Timeouts: {timeouts}",
        f"⚡ Latency: avg {avg_lat:.1f}ms / p95 {p95_lat:.1f}ms",
        "",
        f"🚦 Status: <b>{promotion_state.upper()}</b>",
    ]
    if str(promotion_state).upper() in ("READY_FOR_REVIEW", "APPROVED", "CHAMPION"):
        lines.append("⚠️ <b>PROMOTION REVIEW OPEN — OPERATOR ACTION REQUIRED</b>")
    else:
        lines.append("⛔ NO PROMOTION")
    if comparisons < sample_floor:
        lines.append(f"* Evidence floor: {comparisons}/{sample_floor} samples")
    return "\n".join(lines)


def build_governance_report(engine: Any) -> dict[str, Any] | None:
    """Collects the canonical governance snapshot from a LiveEngine.

    Returns None when the engine has no governance wiring (offline/safe).
    """
    try:
        if engine is None or not hasattr(engine, "governance_engine"):
            return None
        shadow_s = engine._governance_shadow.summary() if engine._governance_shadow else {}
        champ: dict[str, Any] = {"id": "?", "version": "?", "healthy": False}
        with contextlib.suppress(Exception):
            c = engine.champion_manager.champion_or_none()
            if c is not None:
                champ = {
                    "id": c.model_id,
                    "version": c.model_version,
                    "schema": c.feature_schema_id,
                    "healthy": c.available,
                }
        chal = {
            "id": shadow_s.get("model_id", ""),
            "version": shadow_s.get("model_version", ""),
            "schema": shadow_s.get("schema_id", ""),
            "state": "SHADOW" if engine._governance_shadow else "NONE",
        }
        shad = {
            "running": bool(engine._governance_shadow and engine.shadow_engine.active_run_id),
            "comparisons": shadow_s.get("comparisons", 0),
            "errors": shadow_s.get("errors", 0),
            "dropped": shadow_s.get("dropped", 0),
            "timeouts": shadow_s.get("timeouts", 0),
            "avg_latency_ms": shadow_s.get("avg_latency_ms", 0.0),
            "p95_latency_ms": shadow_s.get("p95_latency_ms", 0.0),
            "last_update": shadow_s.get("last_update", ""),
        }
        state_row = engine.governance_store.get_state(chal.get("id", ""), chal.get("version", ""))
        promotion_state = str(state_row.get("lifecycle_state", "SHADOW")) if state_row else "SHADOW"
        return {
            "champion": champ,
            "challenger": chal,
            "shadow": shad,
            "promotion_state": promotion_state,
        }
    except Exception as e:
        logger.error("[MODEL_GOVERNANCE] telegram report build failed", error=str(e))
        return None
