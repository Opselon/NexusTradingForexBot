"""LiveEngine.apply_command_intent — Telegram-issued operator intents.

INV-010 boundary: the Telegram layer NEVER mutates canonical state. This
method is the ENGINE-side authority boundary for authenticated intents
arriving from observability.tg_command_bus. It routes each intent through
the EXISTING governance/execution surfaces:

    halt    -> RiskEngine.enable_kill_switch()      (risk-layer authority)
    resume  -> RiskEngine.disable_kill_switch()     (risk-layer authority)
    rollback-> ModelGovernanceEngine.rollback()     (governance authority,
               load-gate verified, previous artifact restored via the
               engine's own _activate_rollback_model path)

The bus never calls the broker adapter; this method never calls the broker
adapter either — a halt blocks NEW dispatch at the RiskEngine gate while
protective management continues, exactly like the dashboard kill switch.

Auditability: every intent result carries actor/chat/timestamp; halts and
rollbacks land in the governance event ledger / risk logs through their own
call paths. Idempotency: update_id dedup happens in the bus; kill-switch
re-arm is naturally idempotent; rollback re-requests are gated by the
governance engine's own transition rules.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.application.command_intent")


def apply_command_intent(self: Any, intent: dict[str, Any]) -> dict[str, Any]:
    """Handles one authenticated operator intent (engine-side authority)."""
    command = str(intent.get("command", ""))
    actor = f"telegram:{intent.get('username', '') or intent.get('chat_id', '?')}"
    at = str(intent.get("at", datetime.now(UTC).isoformat()))

    if command == "status":
        return _intent_status(self)

    if command == "halt":
        return _intent_halt(self, actor=actor, at=at)

    if command == "resume":
        return _intent_resume(self, actor=actor, at=at)

    if command == "rollback":
        return _intent_rollback(self, actor=actor, at=at)

    return {"ok": False, "message": f"unknown intent {command!r}"}


def _intent_status(self: Any) -> dict[str, Any]:
    """Compact operator snapshot assembled from canonical state (read-only)."""
    lines: list[str] = ["🎮 <b>ENGINE STATUS</b>"]
    account = getattr(self, "_account_snapshot", None)
    if account is not None:
        lines.append(
            f"💰 Balance: <code>{account.balance:.2f}</code> | "
            f"Equity: <code>{account.equity:.2f}</code>"
        )
    lines.append(f"🎛 Mode: <code>{getattr(self, '_runtime_mode', '?')}</code>")
    lines.append(f"▶ Running: <code>{bool(getattr(self, '_running', False))}</code>")

    # Risk gates + breakers
    risk = getattr(self, "risk_engine", None)
    if risk is not None:
        lines.append(
            f"🛑 Kill switch: <code>{'ARMED' if getattr(risk, '_kill_switch_active', False) else 'off'}</code>"
        )
        breaker = getattr(risk, "_last_breaker", None)
        if breaker is not None:
            lines.append(
                f"⚡ Breakers: <code>{breaker.level}</code> "
                f"(day {breaker.daily_loss_pct if breaker.daily_loss_pct is not None else '—'}%"
                f" / week {breaker.weekly_loss_pct if breaker.weekly_loss_pct is not None else '—'}%"
                f", streak {breaker.consecutive_losses})"
            )

    # Drift state (canonical monitor summary)
    breaker = getattr(self, "_feature_drift_breaker", None)
    if breaker is not None:
        lines.append(f"🌊 Drift: <code>{breaker.state()}</code>")

    # Champion
    champ = None
    with contextlib.suppress(Exception):
        cm = getattr(self, "champion_manager", None)
        champ = cm.champion_or_none() if cm is not None else None
    if champ is not None and getattr(champ, "available", False):
        lines.append(f"🏆 Champion: <code>{champ.model_id}@{champ.model_version}</code>")
    else:
        lines.append("🏆 Champion: <code>unavailable</code>")

    return {"ok": True, "message": "\n".join(lines)}


def _intent_halt(self: Any, *, actor: str, at: str) -> dict[str, Any]:
    risk = getattr(self, "risk_engine", None)
    if risk is None:
        return {"ok": False, "message": "risk engine unavailable — halt NOT armed"}
    already = bool(getattr(risk, "_kill_switch_active", False))
    risk.enable_kill_switch()
    logger.critical(
        "[COMMAND_INTENT] event=HALT actor=%s at=%s already_armed=%s",
        actor,
        at,
        already,
    )
    msg = (
        "🛑 HALT already armed — all new entries remain blocked."
        if already
        else "🛑 HALT ARMED — RiskEngine now blocks ALL new entries. "
        "Open positions keep protective management (SL/TP/trailing)."
    )
    with contextlib.suppress(Exception):
        self.notifier.notify_kill_switch_activated(f"Operator halt via Telegram ({actor})")
    return {"ok": True, "message": msg, "actor": actor, "at": at}


def _intent_resume(self: Any, *, actor: str, at: str) -> dict[str, Any]:
    risk = getattr(self, "risk_engine", None)
    if risk is None:
        return {"ok": False, "message": "risk engine unavailable — resume NOT applied"}
    was = bool(getattr(risk, "_kill_switch_active", False))
    risk.disable_kill_switch()
    logger.warning("[COMMAND_INTENT] event=RESUME actor=%s at=%s was_armed=%s", actor, at, was)
    msg = (
        "▶ HALT LIFTED — new entries flow through the normal gates again."
        if was
        else "ℹ️ Kill switch was not armed; nothing changed."  # noqa: RUF001
    )
    return {"ok": True, "message": msg, "actor": actor, "at": at}


def _intent_rollback(self: Any, *, actor: str, at: str) -> dict[str, Any]:
    """Rolls the champion back through the EXISTING governance engine.

    The previous artifact is the newest *.bak* sibling of the current
    artifact (the trainer's own staged backups); the load gate re-verifies
    it before activation. Without a staged previous artifact the intent is
    refused honestly — never a blind overwrite.
    """
    gov = getattr(self, "governance_engine", None)
    champ_mgr = getattr(self, "champion_manager", None)
    if gov is None or champ_mgr is None:
        return {"ok": False, "message": "governance engine unavailable"}

    champ = champ_mgr.champion_or_none()
    if champ is None or not getattr(champ, "available", False):
        return {"ok": False, "message": "no verified champion loaded — nothing to roll back"}

    current_path = Path(self.config.model.model_artifact_path)
    candidates = sorted(
        current_path.parent.glob(current_path.name + ".bak*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return {
            "ok": False,
            "message": f"no staged previous artifact next to {current_path.name} — rollback refused",
        }
    previous = candidates[0]

    try:
        gov.rollback(
            failed_model_id=str(getattr(champ, "model_id", "") or "current"),
            failed_version=str(getattr(champ, "model_version", "") or ""),
            previous_model_id=str(getattr(champ, "model_id", "") or "") + ".previous",
            previous_version="",
            actor=actor,
            reason=f"operator rollback via Telegram at {at}",
            previous_artifact=previous,
            previous_scaler=Path(str(previous) + ".scaler.npz")
            if Path(str(previous) + ".scaler.npz").exists()
            else "",
        )
    except Exception as exc:
        logger.error("[COMMAND_INTENT] rollback rejected (isolated)", error=str(exc))
        return {"ok": False, "message": f"rollback rejected: {type(exc).__name__}"}

    return {
        "ok": True,
        "message": f"⏪ ROLLBACK EXECUTED — restored {previous.name} (governance-audited)",
        "actor": actor,
        "at": at,
    }
