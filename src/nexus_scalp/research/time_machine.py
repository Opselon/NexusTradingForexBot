"""
Historical Time Machine (Replay Layer)
======================================
PHASE 7 implementation of the Strategy Command Center historical playback.

Reconstructs the full state of the strategy fleet at any historical instant T
from authoritative event data (validation_lineage). Supports:
  * full-fleet state at time T
  * per-strategy journey (event-by-event)
  * scrub / step semantics via ordered event indices

The replay NEVER fabricates states: at time T, a strategy is shown in the
state its most recent (<= T) transition established. Strategies not yet
discovered at T are absent from the frame.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.event_projection import parse_lineage_entry
from nexus_scalp.research.models import StrategyRegistryEntry
from nexus_scalp.research.spatial_layout import MATURITY_RANK


class TimeMachine:
    """Historical fleet-state reconstruction from lifecycle events."""

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo

    # ------------------------------------------------------------------
    # Event indexing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_ts(value: str) -> datetime | None:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except Exception:
            return None

    def _strategy_events(self, entry: StrategyRegistryEntry) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for raw in entry.validation_lineage or []:
            parsed = parse_lineage_entry(str(raw))
            if parsed is None:
                continue
            dt = self._parse_ts(parsed.get("timestamp", ""))
            if dt is None and entry.created_at is not None:
                # Lineage without parseable timestamp: fall back to created_at
                dt = entry.created_at.astimezone(UTC) if entry.created_at.tzinfo else entry.created_at.replace(tzinfo=UTC)
            events.append({
                "timestamp": parsed.get("timestamp", ""),
                "dt": dt,
                "to_state": parsed.get("to_state", ""),
                "reason": parsed.get("reason", ""),
                "actor": parsed.get("actor", ""),
            })
        events.sort(key=lambda e: e["dt"] or datetime.max.replace(tzinfo=UTC))
        return events

    # ------------------------------------------------------------------
    # Frame computation
    # ------------------------------------------------------------------

    def frame_at(
        self,
        entries: list[StrategyRegistryEntry],
        at: datetime,
    ) -> dict[str, Any]:
        """
        Fleet state at instant `at`.

        Returns nodes with the state each strategy was in at that moment,
        plus the transitions happening exactly in this frame.
        """
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)

        nodes: list[dict[str, Any]] = []
        transitioning: list[dict[str, Any]] = []
        window_start = at

        for entry in entries:
            events = self._strategy_events(entry)
            if not events:
                continue
            first_dt = events[0]["dt"]
            if first_dt and first_dt > at:
                continue  # not yet discovered at this instant

            current_state = events[0]["to_state"]
            last_event = events[0]
            for ev in events:
                ev_dt = ev["dt"] or datetime.min.replace(tzinfo=UTC)
                if ev_dt <= at:
                    current_state = ev["to_state"]
                    last_event = ev
                else:
                    break

            node = {
                "strategy_id": entry.strategy_id,
                "zone": current_state,
                "maturity": MATURITY_RANK.get(current_state, 0),
                "last_event_ts": last_event["timestamp"],
                "last_actor": last_event["actor"],
            }
            # Transition occurring within this frame (same timestamp bucket).
            if last_event["dt"] is not None and abs((at - last_event["dt"]).total_seconds()) < 60:
                node["transitioning"] = True
                transitioning.append({
                    "strategy_id": entry.strategy_id,
                    "to_state": current_state,
                    "reason": last_event["reason"],
                    "actor": last_event["actor"],
                })
            nodes.append(node)

        return {
            "available": True,
            "frame_time": at.isoformat(),
            "node_count": len(nodes),
            "nodes": nodes,
            "transitions_in_frame": transitioning,
        }

    def journey(self, entry: StrategyRegistryEntry) -> dict[str, Any]:
        """Full per-strategy journey (ordered events) for step/scrub UI."""
        events = self._strategy_events(entry)
        out_events = []
        cumulative_states: list[str] = []
        for i, ev in enumerate(events):
            cumulative_states.append(ev["to_state"])
            out_events.append({
                "index": i,
                "timestamp": ev["timestamp"],
                "to_state": ev["to_state"],
                "actor": ev["actor"],
                "reason": ev["reason"],
                "states_so_far": list(cumulative_states),
            })
        return {
            "available": True,
            "strategy_id": entry.strategy_id,
            "current_state": entry.lifecycle.value,
            "journey_length": len(out_events),
            "events": out_events,
        }

    def bounds(self, entries: list[StrategyRegistryEntry]) -> dict[str, Any]:
        """Earliest / latest event timestamps across the fleet (scrubber range)."""
        lo: datetime | None = None
        hi: datetime | None = None
        total_events = 0
        for entry in entries:
            for ev in self._strategy_events(entry):
                total_events += 1
                dt = ev["dt"]
                if dt is None:
                    continue
                if lo is None or dt < lo:
                    lo = dt
                if hi is None or dt > hi:
                    hi = dt
        return {
            "available": lo is not None,
            "earliest": lo.isoformat() if lo else "",
            "latest": hi.isoformat() if hi else "",
            "total_events": total_events,
        }
