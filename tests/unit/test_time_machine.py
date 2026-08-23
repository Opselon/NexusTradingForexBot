"""
Tests for the Historical Time Machine (Phase 7 replay layer).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nexus_scalp.research.models import CandidateLifecycle, StrategyRegistryEntry
from nexus_scalp.research.time_machine import TimeMachine


def _entry(sid: str, lc: CandidateLifecycle, lineage: list[str]) -> StrategyRegistryEntry:
    return StrategyRegistryEntry(
        strategy_id=sid,
        strategy_version="1",
        lifecycle=lc,
        validation_lineage=lineage,
        created_at=datetime(2026, 8, 20, tzinfo=UTC),
        updated_at=datetime(2026, 8, 23, tzinfo=UTC),
    )


ENTRIES = [
    _entry(
        "fast",
        CandidateLifecycle.ACTIVE,
        [
            "2026-08-21T09:00:00+00:00:DISCOVERED",
            "2026-08-21T10:00:00+00:00:VALIDATED",
            "2026-08-22T11:00:00+00:00:SHADOW",
            "2026-08-23T12:00:00+00:00:ACTIVE:operator_promotion:actor=alice",
        ],
    ),
    _entry(
        "slow",
        CandidateLifecycle.VALIDATED,
        [
            "2026-08-23T09:00:00+00:00:DISCOVERED",
            "2026-08-23T11:00:00+00:00:VALIDATED",
        ],
    ),
    _entry(
        "future",
        CandidateLifecycle.DISCOVERED,
        ["2026-08-30T09:00:00+00:00:DISCOVERED"],
    ),
]


class TestTimeMachine:
    def setup_method(self):
        self.tm = TimeMachine(None)

    def test_frame_before_any_event_empty(self):
        frame = self.tm.frame_at(ENTRIES, datetime(2026, 8, 1, tzinfo=UTC))
        assert frame["node_count"] == 0
        assert frame["nodes"] == []

    def test_frame_mid_history(self):
        # Between DISCOVERED and VALIDATED for 'slow'; 'fast' already ACTIVE? no:
        # at Aug 22 15:00, fast is SHADOW (since Aug 22 11:00), slow not yet born.
        frame = self.tm.frame_at(ENTRIES, datetime(2026, 8, 22, 15, 0, tzinfo=UTC))
        by_id = {n["strategy_id"]: n for n in frame["nodes"]}
        assert "slow" not in by_id          # not yet discovered
        assert by_id["fast"]["zone"] == "SHADOW"

    def test_frame_excludes_future_strategy(self):
        frame = self.tm.frame_at(ENTRIES, datetime(2026, 8, 25, tzinfo=UTC))
        ids = {n["strategy_id"] for n in frame["nodes"]}
        assert "future" not in ids

    def test_transition_flagged_in_frame(self):
        frame = self.tm.frame_at(ENTRIES, datetime(2026, 8, 23, 12, 0, 30, tzinfo=UTC))
        trans = frame["transitions_in_frame"]
        assert any(t["strategy_id"] == "fast" and t["to_state"] == "ACTIVE" for t in trans)
        node = next(n for n in frame["nodes"] if n["strategy_id"] == "fast")
        assert node.get("transitioning") is True

    def test_journey_ordered_and_cumulative(self):
        j = self.tm.journey(ENTRIES[0])
        assert j["journey_length"] == 4
        states = [e["to_state"] for e in j["events"]]
        assert states == ["DISCOVERED", "VALIDATED", "SHADOW", "ACTIVE"]
        last = j["events"][-1]
        assert last["states_so_far"] == ["DISCOVERED", "VALIDATED", "SHADOW", "ACTIVE"]

    def test_bounds(self):
        b = self.tm.bounds(ENTRIES)
        assert b["available"] is True
        assert b["earliest"].startswith("2026-08-21T09:00:00")
        assert b["latest"].startswith("2026-08-30T09:00:00")
        assert b["total_events"] == 7

    def test_scrub_step_semantics(self):
        # Step through fast's journey one event at a time and check the zone.
        j = self.tm.journey(ENTRIES[0])
        checkpoints = [
            ("2026-08-21T09:30:00+00:00", "DISCOVERED"),
            ("2026-08-21T10:30:00+00:00", "VALIDATED"),
            ("2026-08-22T11:30:00+00:00", "SHADOW"),
            ("2026-08-23T12:30:00+00:00", "ACTIVE"),
        ]
        for iso, expected in checkpoints:
            dt = datetime.fromisoformat(iso)
            frame = self.tm.frame_at([ENTRIES[0]], dt)
            assert frame["nodes"][0]["zone"] == expected
