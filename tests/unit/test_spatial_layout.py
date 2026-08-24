"""
Tests for the spatial 2.5D layout engine (Phase 4).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nexus_scalp.research.models import (
    CandidateLifecycle,
    StrategyRegistryEntry,
    StrategyScore,
)
from nexus_scalp.research.snapshot import build_snapshot
from nexus_scalp.research.spatial_layout import ALL_ZONES, SpatialLayout


def _entry(
    sid: str, lc: CandidateLifecycle, score: StrategyScore | None = None, sample_count: int = 10
):
    return StrategyRegistryEntry(
        strategy_id=sid,
        strategy_version="1.0.0",
        lifecycle=lc,
        score=score,
        sample_count=sample_count,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class TestSpatialLayout:
    def test_zones_cover_all_states(self):
        assert set(ALL_ZONES) == {s.value for s in CandidateLifecycle}

    def test_nodes_placed_in_correct_zone(self):
        entries = [
            _entry("a", CandidateLifecycle.DISCOVERED),
            _entry("b", CandidateLifecycle.SHADOW),
            _entry("c", CandidateLifecycle.ACTIVE),
        ]
        layout = SpatialLayout().compute(entries)
        node_by_id = {n["strategy_id"]: n for n in layout["nodes"]}
        assert node_by_id["a"]["zone"] == "DISCOVERED"
        assert node_by_id["b"]["zone"] == "SHADOW"
        assert node_by_id["c"]["zone"] == "ACTIVE"

    def test_depth_follows_maturity(self):
        entries = [_entry(f"s{i}", lc) for i, lc in enumerate(CandidateLifecycle)]
        layout = SpatialLayout().compute(entries)
        z_by_zone = {n["zone"]: n["z"] for n in layout["nodes"]}
        assert z_by_zone["DISCOVERED"] < z_by_zone["VALIDATED"]
        assert z_by_zone["VALIDATED"] < z_by_zone["SHADOW"]
        assert z_by_zone["SHADOW"] < z_by_zone["ACTIVE"]

    def test_elevation_none_when_not_measured(self):
        # No score → elevation must be None (UI shows NOT_MEASURED).
        layout = SpatialLayout().compute([_entry("bare", CandidateLifecycle.VALIDATED)])
        node = next(n for n in layout["nodes"] if n["strategy_id"] == "bare")
        assert node["elevation"] is None

    def test_elevation_uses_real_score(self):
        score = StrategyScore(final_score=0.77)
        entries = [_entry("scored", CandidateLifecycle.VALIDATED, score=score)]
        snapshots = {"scored": build_snapshot(entries[0])}
        layout = SpatialLayout().compute(entries, snapshots=snapshots)
        node = next(n for n in layout["nodes"] if n["strategy_id"] == "scored")
        assert node["elevation"] == pytest.approx(0.77)

    def test_deterministic_positions(self):
        entries = [_entry("same", CandidateLifecycle.DISCOVERED)]
        l1 = SpatialLayout().compute(list(entries))
        l2 = SpatialLayout().compute(list(entries))
        n1, n2 = l1["nodes"][0], l2["nodes"][0]
        assert (n1["x"], n1["y"]) == (n2["x"], n2["y"])

    def test_ring_count_counts_passed_gates(self):
        from nexus_scalp.research.models import (
            BacktestResult,
            OOSResult,
            RobustnessResult,
            WalkForwardResult,
        )

        entry = _entry("gated", CandidateLifecycle.VALIDATED)
        entry = entry.model_copy(
            update={
                "backtest": BacktestResult(
                    strategy_id="g", strategy_version="1", dataset_id="d", total_trades=5
                ),
                "walkforward": WalkForwardResult(
                    strategy_id="g", strategy_version="1", dataset_id="d", passed=True
                ),
                "oos": OOSResult(
                    strategy_id="g", strategy_version="1", dataset_id="d", status="PASS"
                ),
                "robustness": RobustnessResult(
                    strategy_id="g", strategy_version="1", dataset_id="d", status="PASS"
                ),
            }
        )
        snapshots = {"gated": build_snapshot(entry)}
        layout = SpatialLayout().compute([entry], snapshots=snapshots)
        node = layout["nodes"][0]
        assert node["ring_count"] == 4

    def test_grid_wraps_at_max_columns(self):
        # Deterministic jitter is small (±8) relative to node spacing (70),
        # so wrapped-row x values must be near the first-row column centers.
        entries = [_entry(f"n{i}", CandidateLifecycle.DISCOVERED) for i in range(7)]
        layout = SpatialLayout(max_columns=6, node_spacing=70.0).compute(entries)
        nodes = layout["nodes"]
        first_row_x = [n["x"] for n in nodes[:6]]
        seventh_x = nodes[6]["x"]
        # Column 0 center = (0 - 2.5)*70 = -175; jitter ±8 keeps it within ±10.
        assert any(abs(seventh_x - fx) <= 10 for fx in first_row_x)
        # And its y must be pushed to a second row band.
        assert nodes[6]["y"] > max(n["y"] for n in nodes[:6])
