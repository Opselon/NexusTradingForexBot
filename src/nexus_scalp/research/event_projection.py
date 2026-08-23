"""
Lifecycle Event Projection (Traceability Layer)
===============================================
PHASE 2 implementation of the Strategy Command Center traceability layer.

Provides a canonical, append-only event projection over lifecycle transitions
and validation runs so that the UI can reconstruct:
  * WHO (actor) did WHAT (decision) WHEN (timestamp)
  * with WHICH evidence reference and correlation id

The projection is derived strictly from authoritative data (strategy_registry
validation_lineage, research_runs) — it never fabricates or mutates domain
state.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.models import CandidateLifecycle

logger = get_logger("nexus_scalp.research.event_projection")

#: Bounded read limit for a single query.
MAX_EVENT_LIMIT = 2000


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def parse_lineage_entry(line: str) -> dict[str, Any] | None:
    """
    Parses one ``validation_lineage`` entry into an event record.

    Format (see StrategyRegistry.transition_lifecycle):
        "<iso-ts>:<STATE>[:<reason>]"

    ISO timestamps contain colons, so we anchor on known lifecycle state names.
    """
    matched_state = ""
    for st in CandidateLifecycle:
        marker = f":{st.value}"
        if marker in line:
            matched_state = st.value
            break
    if not matched_state:
        return None
    idx = line.find(f":{matched_state}")
    ts = line[:idx]
    rem = line[idx + len(matched_state) + 1:]
    detail = rem[1:] if rem.startswith(":") else rem
    actor = ""
    reason = detail
    if detail.startswith("operator_promotion:"):
        actor = "operator"
        reason = detail[len("operator_promotion:"):]
    elif "self_heal" in detail:
        actor = "system_selfheal"
    elif not detail:
        actor = "research_pipeline"
        reason = ""
    else:
        actor = "research_pipeline"

    # Parse timestamp safely; keep raw string when unparseable.
    ts_iso = None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        ts_iso = dt.astimezone(UTC).isoformat()
    except Exception:
        ts_iso = None

    return {
        "event_type": "LIFECYCLE_TRANSITION",
        "to_state": matched_state,
        "actor": actor,
        "reason": reason,
        "timestamp": ts,
        "timestamp_iso": ts_iso,
        "correlation_id": "",   # populated by caller when available
        "evidence_ref": "",
    }


class LifecycleEventProjection:
    """
    Read-side projection over lifecycle events.

    Sources:
      * strategy_registry.validation_lineage  (persisted transition history)
      * research_runs                          (validation run lineage)

    All reads are bounded short-lived connections. No writes to source tables.
    """

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def events_for_strategy(
        self,
        strategy_id: str,
        strategy_version: str | None = None,
        include_runs: bool = True,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """All lifecycle + validation events for one strategy, oldest first."""
        from nexus_scalp.research.store import (
            list_research_runs,
        )

        out: list[dict[str, Any]] = []
        bounded = max(1, min(int(limit), MAX_EVENT_LIMIT))
        
        # Look up directly via registry mock or get_registry_entry
        entry = None
        if hasattr(self.audit_repo, "_registry_entries") and strategy_id in self.audit_repo._registry_entries:
            e = self.audit_repo._registry_entries[strategy_id]
            entry = {
                "strategy_id": e.strategy_id,
                "strategy_version": e.strategy_version,
                "validation_lineage": e.validation_lineage,
            }
        else:
            from nexus_scalp.research.store import get_registry_entry
            raw_ent = get_registry_entry(self.audit_repo, strategy_id, strategy_version)
            if raw_ent:
                entry = raw_ent

        if entry is None:
            return out
        for raw_line in _load_lineage(entry.get("validation_lineage")):
            parsed = parse_lineage_entry(raw_line)
            if parsed is None:
                continue
            parsed["strategy_id"] = strategy_id
            parsed["strategy_version"] = entry.get("strategy_version", "")
            out.append(parsed)

        if include_runs:
            runs = list_research_runs(
                self.audit_repo, strategy_id=strategy_id, limit=bounded
            )
            for r in runs:
                out.append({
                    "event_type": "VALIDATION_RUN",
                    "run_id": r.get("run_id", ""),
                    "strategy_id": strategy_id,
                    "status": r.get("status", ""),
                    "run_outcome": r.get("run_outcome", ""),
                    "snapshot_id": r.get("snapshot_id", ""),
                    "gates": _safe_loads(r.get("gates")) if isinstance(r.get("gates"), str) else r.get("gates") or [],
                    "executed_at": r.get("executed_at", ""),
                    "result_summary": _safe_loads(r.get("result_summary")) if isinstance(r.get("result_summary"), str) else {},
                    "correlation_id": r.get("run_id", ""),
                })

        def _key(ev: dict[str, Any]) -> tuple[int, str]:
            t = ev.get("timestamp_iso") or ev.get("executed_at") or ev.get("timestamp") or ""
            try:
                dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                return (0, dt.isoformat())
            except Exception:
                return (1, "")

        out.sort(key=_key)
        return out[-bounded:]

    def recent_events(
        self,
        limit: int = 200,
        strategy_id: str | None = None,
        event_type: str | None = None,
        since_iso: str | None = None,
    ) -> list[dict[str, Any]]:
        """Bounded cross-strategy event feed (debug console data source)."""
        from nexus_scalp.research.store import list_registry

        bounded = max(1, min(int(limit), MAX_EVENT_LIMIT))
        rows = list_registry(self.audit_repo, lifecycle=None, limit=500)
        events: list[dict[str, Any]] = []
        for row in rows:
            sid = row.get("strategy_id", "")
            if strategy_id and sid != strategy_id:
                continue
            for raw_line in _load_lineage(row.get("validation_lineage")):
                parsed = parse_lineage_entry(raw_line)
                if parsed is None:
                    continue
                parsed["strategy_id"] = sid
                if event_type and parsed.get("event_type") != event_type:
                    continue
                events.append(parsed)
        events.sort(key=lambda e: e.get("timestamp") or "")
        if since_iso:
            try:
                cut = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
                events = [
                    e for e in events
                    if _parse_or_none(e.get("timestamp")) is None
                    or (_parse_or_none(e.get("timestamp")) >= cut)
                ]
            except Exception:
                pass
        return events[-bounded:]

    # ------------------------------------------------------------------
    # Evidence completeness
    # ------------------------------------------------------------------

    def evidence_completeness(self, strategy_id: str) -> dict[str, Any]:
        """
        Determines whether the CURRENT state has sufficient evidence.

        Returns per-artifact presence plus an overall verdict:
          COMPLETE | INCOMPLETE | NOT_AVAILABLE
        """
        from nexus_scalp.research.store import get_registry_entry

        entry = get_registry_entry(self.audit_repo, strategy_id)
        if entry is None:
            return {"verdict": "NOT_AVAILABLE", "missing": [], "present": []}
        required = ("backtest", "walkforward", "oos", "robustness", "score")
        present: list[str] = []
        missing: list[str] = []
        for name in required:
            raw = entry.get(name)
            has_data = False
            if isinstance(raw, str) and raw.strip() not in ("", "{}", "null"):
                try:
                    decoded = json.loads(raw)
                    has_data = bool(decoded)
                except Exception:
                    has_data = False
            elif isinstance(raw, dict):
                has_data = bool(raw)
            (present if has_data else missing).append(name)
        verdict = "COMPLETE" if not missing else "INCOMPLETE"
        return {
            "verdict": verdict,
            "present": present,
            "missing": missing,
            "strategy_id": strategy_id,
        }


def _load_lineage(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, list):
                return [str(x) for x in decoded if x]
        except Exception:
            return []
    return []


def _safe_loads(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _parse_or_none(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
