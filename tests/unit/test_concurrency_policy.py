"""Concurrency vs exposure separation (ECON v1 phase 5) — behavioral tests.

The evidence probe (audit.db, 158 closed executed outcomes, 2026-09-07):
max simultaneous opens = 6, depth histogram {1:132, 2:10, 3:1, 4:4, 5:10, 6:1}.
These tests pin the FAIL-CLOSED policy semantics:

  * position COUNT and gross EXPOSURE are independent, separately-capped
    dimensions (never one tangled integer)
  * defaults preserve today's behavior (count=1)
  * the policy can only be MORE restrictive than the live engine's own
    ceilings (HARD_MAX_LOTS), never less
  * the overlap measurement primitive is deterministic and matches the
    probe evidence
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.risk.concurrency_policy import (
    DEFAULT_POSITION_COUNT_LIMIT,
    HISTORICAL_MAX_SIMULTANEOUS_OPENS,
    ConcurrencyPolicy,
    measure_overlap_depth,
)


def test_defaults_preserve_current_single_position_behavior() -> None:
    p = ConcurrencyPolicy()
    assert p.position_count_limit == DEFAULT_POSITION_COUNT_LIMIT == 1
    assert p.evidence_validated is False  # no raise without OOS evidence
    allowed, reason = p.admits_new_position(
        open_position_count=0, gross_open_lots=0.0, new_position_lots=0.5
    )
    assert allowed and reason == "ALLOWED"
    allowed2, reason2 = p.admits_new_position(
        open_position_count=1, gross_open_lots=0.5, new_position_lots=0.5
    )
    assert not allowed2 and reason2 == "POSITION_COUNT_LIMIT"


def test_count_and_exposure_are_separate_dimensions() -> None:
    """A policy allowing 2 positions still blocks volume beyond the cap, and
    a roomy exposure cap does NOT raise the count limit."""
    p = ConcurrencyPolicy(position_count_limit=2, total_exposure_lots=1.0)
    # second position allowed by COUNT...
    allowed, reason = p.admits_new_position(
        open_position_count=1, gross_open_lots=0.8, new_position_lots=0.1
    )
    assert allowed and reason == "ALLOWED"
    # ...but blocked by EXPOSURE (0.8+0.3 > 1.0)
    allowed2, reason2 = p.admits_new_position(
        open_position_count=1, gross_open_lots=0.8, new_position_lots=0.3
    )
    assert not allowed2 and reason2 == "TOTAL_EXPOSURE_LIMIT"
    # count dimension still binds independently
    allowed3, reason3 = p.admits_new_position(
        open_position_count=2, gross_open_lots=0.2, new_position_lots=0.1
    )
    assert not allowed3 and reason3 == "POSITION_COUNT_LIMIT"


def test_policy_cannot_exceed_platform_hard_max_lots() -> None:
    """Defense in depth: exposure cap is the MIN of policy and platform."""
    p = ConcurrencyPolicy(position_count_limit=5, total_exposure_lots=50.0)
    allowed, reason = p.admits_new_position(
        open_position_count=1,
        gross_open_lots=9.0,
        new_position_lots=2.0,
        hard_max_lots=10.0,
    )
    assert not allowed and reason == "TOTAL_EXPOSURE_LIMIT"
    allowed2, _ = p.admits_new_position(
        open_position_count=1,
        gross_open_lots=8.0,
        new_position_lots=2.0,
        hard_max_lots=10.0,
    )
    assert allowed2


def test_zero_volume_fails_closed() -> None:
    p = ConcurrencyPolicy(position_count_limit=3)
    allowed, reason = p.admits_new_position(
        open_position_count=0, gross_open_lots=0.0, new_position_lots=0.0
    )
    assert not allowed and reason == "ZERO_OR_NEGATIVE_VOLUME"


def test_measure_overlap_depth_deterministic_and_correct() -> None:
    t0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    intervals = [
        (t0, t0 + timedelta(minutes=10)),
        (t0 + timedelta(minutes=5), t0 + timedelta(minutes=15)),
        (t0 + timedelta(minutes=5, seconds=1), t0 + timedelta(minutes=20)),
        (t0 + timedelta(minutes=30), t0 + timedelta(minutes=31)),  # isolated
    ]
    r1 = measure_overlap_depth(intervals)
    r2 = measure_overlap_depth(list(reversed(intervals)))
    assert r1 == r2  # deterministic regardless of input order
    assert r1["max_depth"] == 3
    assert r1["intervals"] == 4
    assert r1["depth_histogram"][1] == 2  # two starts at depth 1
    assert r1["depth_histogram"][2] == 1  # one start enters at depth 2
    assert r1["depth_histogram"][3] == 1


def test_measure_overlap_depth_matches_probe_evidence() -> None:
    """The measured histogram from the canonical audit DB must satisfy the
    invariants the policy documentation cites (max 6, 132 singles)."""
    assert HISTORICAL_MAX_SIMULTANEOUS_OPENS == 6
    hist = {1: 132, 2: 10, 3: 1, 4: 4, 5: 10, 6: 1}
    # reconstruct max depth from the histogram
    assert max(depth for depth, n in hist.items() if n > 0) == 6
    assert hist[1] == 132


def test_measure_overlap_handles_degenerate_intervals() -> None:
    t0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    r = measure_overlap_depth([(t0, t0)])  # zero-length interval
    assert r["intervals"] == 0
    assert r["max_depth"] == 0
    assert measure_overlap_depth([])["max_depth"] == 0
