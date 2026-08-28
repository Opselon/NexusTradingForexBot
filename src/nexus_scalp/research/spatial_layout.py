"""
Spatial Lifecycle Layout Engine (2.5D)
======================================
PHASE 4 implementation of the Strategy Command Center spatial 2.5D layout.

Pure computational geometry — no rendering, no I/O, no UI framework coupling.
Determines where each strategy node lives in the spatial 2.5D environment:

  * Zones: one per lifecycle state (PIPELINE_ORDER + terminal states).
  * Nodes: strategies placed within their zone with deterministic jitter.
  * Depth (z): derived from lifecycle maturity (validation truth established).
  * Elevation: derived from health score when available (never fabricated —
    missing scores yield elevation 0 / NOT_MEASURED).

The web layer consumes this to render the spatial map (canvas / CSS
perspective). All inputs come from authoritative registry data via
`build_snapshot` / `StrategyRegistryEntry`.
"""

from __future__ import annotations

import hashlib
from typing import Any

from nexus_scalp.research.models import StrategyRegistryEntry

#: Spatial zones in pipeline order (elevation increases with maturity).
PIPELINE_ZONES: list[str] = [
    "DISCOVERED",
    "INITIAL_TESTING",
    "EVIDENCE_BUILDING",
    "WALK_FORWARD_READY",
    "OOS_READY",
    "ROBUSTNESS_READY",
    "BACKTESTING",
    "VALIDATING",
    "OOS_TESTING",
    "ROBUSTNESS_TESTING",
    "VALIDATED",
    "SHADOW",
    "ACTIVE",
]

TERMINAL_ZONES: list[str] = ["REJECTED", "DEGRADED", "RETIRED"]

ALL_ZONES: list[str] = PIPELINE_ZONES + TERMINAL_ZONES

#: Maturity rank used for depth/elevation mapping (0..len-1).
MATURITY_RANK: dict[str, int] = {state: i for i, state in enumerate(ALL_ZONES)}


def _stable_jitter(seed_text: str, spread: float = 1.0) -> float:
    """Deterministic pseudo-random offset in [-spread, spread]."""
    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    value = int(digest[:8], 16) / 0xFFFFFFFF  # 0..1
    return (value * 2 - 1) * spread


class SpatialLayout:
    """
    Computes node positions for the 2.5D strategy map.

    Coordinates:
      x  — lateral position inside a zone row (grid columns)
      y  — zone depth index (pipeline progression)
      z  — maturity-based depth (used as CSS perspective scale)
    """

    def __init__(
        self,
        zone_spacing: float = 120.0,
        node_spacing: float = 70.0,
        max_columns: int = 6,
    ) -> None:
        self.zone_spacing = zone_spacing
        self.node_spacing = node_spacing
        self.max_columns = max(1, int(max_columns))

    def compute(
        self,
        entries: list[StrategyRegistryEntry],
        snapshots: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Returns the full spatial layout:
          zones: [{zone, x_index, elevation_hint, count}]
          nodes: [{strategy_id, zone, x, y, z, size_hint, ring_count}]
        """
        snapshots = snapshots or {}
        zone_counts: dict[str, int] = {z: 0 for z in ALL_ZONES}
        nodes: list[dict[str, Any]] = []

        for entry in entries:
            zone = entry.lifecycle.value
            if zone not in zone_counts:
                continue
            idx_in_zone = zone_counts[zone]
            zone_counts[zone] += 1

            col = idx_in_zone % self.max_columns
            row = idx_in_zone // self.max_columns
            y_idx = MATURITY_RANK.get(zone, 0)
            x = (col - (self.max_columns - 1) / 2.0) * self.node_spacing + _stable_jitter(
                f"{entry.strategy_id}:x", spread=8
            )
            # Rows stack downward within a zone; extra rows push slightly back.
            y = (
                y_idx * self.zone_spacing
                + row * (self.node_spacing * 0.4)
                + _stable_jitter(f"{entry.strategy_id}:y", spread=8)
            )
            z = float(MATURITY_RANK.get(zone, 0))

            snap_obj = snapshots.get(entry.strategy_id)
            if hasattr(snap_obj, "model_dump"):
                snap = snap_obj.model_dump()
            elif isinstance(snap_obj, dict):
                snap = snap_obj
            else:
                snap = {}
            health = snap.get("health_score") or {}
            final = health.get("final")
            elevation = float(final) if isinstance(final, (int, float)) else None
            ring_count = sum(
                1
                for key in (
                    "backtest_status",
                    "walkforward_status",
                    "oos_status",
                    "robustness_status",
                )
                if snap.get("evidence_summary", {}).get(key) == "PASS"
            )

            nodes.append(
                {
                    "strategy_id": entry.strategy_id,
                    "strategy_version": entry.strategy_version,
                    "zone": zone,
                    "x": round(x, 2),
                    "y": round(y, 2),
                    "z": z,
                    "elevation": elevation,  # None → NOT_MEASURED in UI
                    "size_hint": entry.sample_count,
                    "ring_count": ring_count,
                    "confidence": entry.confidence,
                }
            )

        zones_out = []
        for zone in ALL_ZONES:
            zones_out.append(
                {
                    "zone": zone,
                    "y_index": MATURITY_RANK[zone],
                    "count": zone_counts[zone],
                    "terminal": zone in TERMINAL_ZONES,
                }
            )

        return {
            "available": True,
            "zones": zones_out,
            "nodes": nodes,
            "meta": {
                "zone_spacing": self.zone_spacing,
                "node_spacing": self.node_spacing,
                "max_columns": self.max_columns,
                "total_nodes": len(nodes),
            },
        }
