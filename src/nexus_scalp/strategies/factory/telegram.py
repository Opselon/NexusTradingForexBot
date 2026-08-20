"""
Strategy Factory — Telegram Lifecycle Reports
==============================================
STRATEGY FACTORY (2026-08-20).

Meaningful lifecycle events (spec 46 / 47) through the existing
TelegramNotifier queue — NEVER spamming hundreds of individual messages:
one Generation Started, one Generation Progress, one Generation Completed,
one per Important Strategy Found / Elite Promotion / Rejection cause-class /
Research Failure.

Formats are structured (emoji sections, code blocks) and compact. The same
`build_*` functions power the UI event stream.
"""

from __future__ import annotations

import html
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.strategies.factory.telegram")

#: Event types the factory may send (bounded set — no per-candidate spam).
FACTORY_EVENT_TYPES = frozenset(
    {
        "GENERATION_STARTED",
        "GENERATION_COMPLETED",
        "GENERATION_PROGRESS",
        "IMPORTANT_STRATEGY_FOUND",
        "ELITE_PROMOTED",
        "STRATEGY_REJECTED",
        "RESEARCH_FAILURE",
        "SYSTEM_FAILURE",
        "LOOP_PAUSED",
        "LOOP_RESUMED",
        "DEPLOYMENT_GATE",
    }
)


def _esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def build_generation_started(gen: dict[str, Any]) -> str:
    return (
        "🤖 <b>STRATEGY FACTORY — GENERATION STARTED</b>\n\n"
        f"Generation: <b>{_esc(gen.get('generation_id', ''))}</b>\n"
        f"Population: <b>{_esc(gen.get('population_target', 0))}</b>\n"
        f"Mode: {_esc(gen.get('mode', 'MANUAL'))}"
    )


def build_generation_completed(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    best = summary.get("best_score", 0.0) or 0.0
    elite = summary.get("elite", 0) or 0
    lines = [
        "✅ <b>STRATEGY GENERATION COMPLETE</b>\n",
        f"Generation: <b>{_esc(payload.get('generation_id', ''))}</b>\n",
        f"Generated: {summary.get('population', 0)}",
        f"Structurally Valid: {summary.get('structurally_valid', 0)}",
        f"Evaluated: {summary.get('evaluated', 0)}",
        f"Validated: {summary.get('validated', 0)}",
        f"Rejected: {summary.get('rejected', 0)}",
        f"Elite: {elite}\n",
        f"Best Score: {best:.2f}",
        f"Median Score: {summary.get('median_score', 0.0):.2f}",
        f"Diversity: {summary.get('diversity', 0.0):.2f}",
        f"Runtime: {summary.get('runtime_ms', 0.0):.0f} ms",
    ]
    failures = summary.get("failure_distribution") or {}
    if failures:
        top = sorted(failures.items(), key=lambda kv: kv[1], reverse=True)[:3]
        lines.append("Main failure modes: " + ", ".join(f"{k}={v}" for k, v in top))
    return "\n".join(lines)


def build_strategy_rejected(payload: dict[str, Any]) -> str:
    """Failure report (spec 47): ID, stage, reason, diagnostic context."""
    lines = [
        "❌ <b>STRATEGY REJECTED</b>\n",
        f"ID: <b>{_esc(payload.get('candidate_id', ''))}</b>",
        f"Generation: {_esc(payload.get('generation_id', ''))}",
        f"Stage: {_esc(payload.get('stage', 'UNKNOWN'))}",
        f"Reason: {_esc(payload.get('reason', ''))}",
        f"Detail: {_esc(payload.get('detail', '') or '')}",
    ]
    return "\n".join(lines)


def build_elite_promoted(payload: dict[str, Any]) -> str:
    return (
        "🏆 <b>ELITE STRATEGY PROMOTED</b>\n\n"
        f"ID: <b>{_esc(payload.get('strategy_id', ''))}</b>\n"
        f"Generation: {_esc(payload.get('generation_id', ''))}\n"
        f"Score: {payload.get('score', 0.0):.2f}\n"
        f"Rank: #{payload.get('rank', 0)}"
    )


def build_failure_alert(payload: dict[str, Any]) -> str:
    return (
        "🚨 <b>STRATEGY FACTORY FAILURE</b>\n\n"
        f"Stage: {_esc(payload.get('stage', 'UNKNOWN'))}\n"
        f"Error: {_esc(payload.get('error', '') or '')[:200]}"
    )


def build_generation_progress(payload: dict[str, Any]) -> str:
    return (
        "⏳ <b>GENERATION PROGRESS</b>\n\n"
        f"Generation: {_esc(payload.get('generation_id', ''))}\n"
        f"Done: {payload.get('done', 0)}/{payload.get('total', 0)} "
        f"({payload.get('pct', 0.0):.0f}%)"
    )


def send_factory_event(
    notifier: Any,
    event_type: str,
    payload: dict[str, Any],
    severity: str = "INFO",
) -> bool:
    """Routes a factory event to Telegram through the existing notifier queue.

    Never raises; a missing/disabled notifier is a no-op (mirrors
    TelegramNotifier.send semantics). Returns True when enqueued.
    """
    if notifier is None or not getattr(notifier, "enabled", False):
        return False
    if event_type not in FACTORY_EVENT_TYPES:
        return False
    text = ""
    if event_type == "GENERATION_STARTED":
        text = build_generation_started(payload)
    elif event_type == "GENERATION_COMPLETED":
        text = build_generation_completed(payload)
    elif event_type == "STRATEGY_REJECTED":
        text = build_strategy_rejected(payload)
    elif event_type == "ELITE_PROMOTED":
        text = build_elite_promoted(payload)
    elif event_type == "GENERATION_PROGRESS":
        text = build_generation_progress(payload)
    elif event_type in ("RESEARCH_FAILURE", "SYSTEM_FAILURE"):
        text = build_failure_alert(payload)
        severity = "ERROR"
    elif event_type in ("LOOP_PAUSED", "LOOP_RESUMED"):
        text = "⏸️ <b>Autonomous loop paused</b>" if event_type == "LOOP_PAUSED" else "▶️ <b>Autonomous loop resumed</b>"
    else:
        # DEPLOYMENT_GATE and others: generic structured line.
        text = f"⚠️ <b>{_esc(event_type)}</b>\n" + "\n".join(
            f"{k}: {_esc(v)}" for k, v in payload.items() if isinstance(v, (str, int, float))
        )
    try:
        notifier.send(text, severity=severity, event_type="STRATEGY_FACTORY")
        return True
    except Exception as e:
        logger.warning("[STRATEGY_FACTORY] telegram send failed (isolated)", error=str(e))
        return False


__all__ = [
    "FACTORY_EVENT_TYPES",
    "build_elite_promoted",
    "build_failure_alert",
    "build_generation_completed",
    "build_generation_progress",
    "build_generation_started",
    "build_strategy_rejected",
    "send_factory_event",
]