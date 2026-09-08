"""Replay Candidate Benchmark tests (research/training-parity P0).

Covers:
  1. determinism: same stream + same candidates => identical artifact
  2. same-stream invariant: all candidates share the event hash
  3. no-side-effect: benchmark never imports an adapter / order_send
  4. champion/challenger/control produce distinct metric blocks
  5. evaluation-mode stamps: COUNTERFACTUAL_REPLAY, never EMPIRICAL
  6. no ledger mutation: replay output never touches ExperienceLedger
  7. role/name validation fails loud
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch

from nexus_scalp.research.replay_benchmark import (
    EVALUATION_MODE,
    ReplayCandidate,
    ReplayCandidateBenchmark,
)

T0 = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[2]


def _make_bundle(tmp_path: Path, seed: int) -> Path:
    """Deterministic 70D model bundle (seed varies => different behavior)."""
    from nexus_scalp.models.scalp_net import ScalpNet

    torch.manual_seed(seed)
    net = ScalpNet(num_features=70, num_classes=4, hidden_dim=128)
    for p in net.parameters():
        p.data.uniform_(-0.01, 0.01)
    net.eval()
    out = tmp_path / f"model_{seed}.pt"
    torch.save(net.state_dict(), out)
    np.savez(
        out.with_suffix(".scaler.npz").with_name(out.stem + ".scaler.npz"),
        mean=np.zeros(70),
        std=np.ones(70),
    )
    return out


def _bar_records(n: int = 320) -> list[dict[str, Any]]:
    """Deterministic zig-zag bar stream (no RNG)."""

    def price(i: int) -> float:
        return 2650.0 + 0.05 * (i % 13) + 0.5 * ((i // 13) % 7) + 0.01 * i

    out = []
    for i in range(n):
        c = price(i)
        o = price(i - 1) if i else c
        hi = max(o, c) + 0.3
        lo = min(o, c) - 0.3
        out.append(
            {
                "kind": "BAR",
                "timestamp": T0 + timedelta(minutes=i),
                "open": o,
                "high": hi,
                "low": lo,
                "close": c,
                "tick_volume": 100 + (i % 17),
                "spread": 0.2,
                "symbol": "XAUUSD",
                "timeframe": "M1",
            }
        )
    return out


def _runner() -> ReplayCandidateBenchmark:
    return ReplayCandidateBenchmark(decide_on="bar_close")


def test_benchmark_is_deterministic_and_same_stream(tmp_path: Path) -> None:
    bundle_a = _make_bundle(tmp_path, seed=11)
    bundle_b = _make_bundle(tmp_path, seed=23)
    candidates = [
        ReplayCandidate("champion", "CHAMPION", str(bundle_a), {"confidence_threshold": 0.35}),
        ReplayCandidate("challenger", "CHALLENGER", str(bundle_b), {"confidence_threshold": 0.35}),
    ]
    events = _bar_records()

    r1 = _runner().run(events, candidates, run_id="RBM-DET-1")
    r2 = _runner().run(events, candidates, run_id="RBM-DET-2")

    # Determinism: identical benchmark identity regardless of run_id.
    assert r1["benchmark_id"] == r2["benchmark_id"]
    # Same stream for every candidate: all event hashes agree.
    eh = r1["stream"]["event_hash"]
    for block in r1["candidates"].values():
        assert block["determinism"]["event_hash"] == eh
    # Deterministic ledgers: candidate reruns reproduce identical CONTENT
    # (the engine's own ledger_hash embeds the run_id label; the content
    # hash is the run-label-independent identity).
    for name in r1["candidates"]:
        assert (
            r1["candidates"][name]["determinism"]["ledger_content_hash"]
            == r2["candidates"][name]["determinism"]["ledger_content_hash"]
        )
    assert r1["evaluation_mode"] == EVALUATION_MODE == "COUNTERFACTUAL_REPLAY"


def test_benchmark_rejects_cross_stream_candidates(tmp_path: Path) -> None:
    """A same-stream violation must abort, never publish a bad comparison."""
    from nexus_scalp.research import replay_benchmark as rb

    bundle = _make_bundle(tmp_path, seed=11)
    good = ReplayCandidate("champion", "CHAMPION", str(bundle), {"confidence_threshold": 0.35})
    events = _bar_records()

    class _ClobberingSource:
        """Event source whose hash path cannot collide with the honest one."""

    # Simulate the violation by monkeypatching the engine's event hash input:
    # a foreign source that flips the event stream mid-run.
    original_make = ReplayCandidateBenchmark._make_source

    def poisoned_make(self, evs, name):  # type: ignore[no-untyped-def]
        if name.endswith("[challenger]"):
            evs = [*(evs[:-5]), dict(evs[-1], close=evs[-1]["close"] + 1.0)]
        return original_make(self, evs, name)

    rb.ReplayCandidateBenchmark._make_source = poisoned_make  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="SAME-STREAM"):
            _runner().run(
                events,
                [good, ReplayCandidate("challenger", "CHALLENGER", str(bundle), {})],
                run_id="RBM-X",
            )
    finally:
        rb.ReplayCandidateBenchmark._make_source = original_make  # type: ignore[method-assign]
    del _ClobberingSource


def test_benchmark_has_no_execution_side_effects(tmp_path: Path) -> None:
    """Structural guard: the benchmark module never touches broker surface."""
    src = (REPO / "src" / "nexus_scalp" / "research" / "replay_benchmark.py").read_text(
        encoding="utf-8"
    )
    for forbidden in ("order_send", "mt5", "ExperienceLedger", "record_experience"):
        assert forbidden not in src, f"forbidden surface in benchmark module: {forbidden}"


def test_benchmark_roles_and_names_fail_loud(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path, seed=11)
    with pytest.raises(ValueError, match="role"):
        _runner().run(
            _bar_records(60),
            [ReplayCandidate("x", "BOGUS", str(bundle))],
            run_id="r",
        )
    with pytest.raises(ValueError, match="duplicate"):
        _runner().run(
            _bar_records(60),
            [
                ReplayCandidate("same", "CHAMPION", str(bundle)),
                ReplayCandidate("same", "CHALLENGER", str(bundle)),
            ],
            run_id="r",
        )
    with pytest.raises(ValueError, match="no events"):
        _runner().run([], [ReplayCandidate("x", "CHAMPION", str(bundle))], run_id="r")


def test_benchmark_candidate_blocks_are_comparable(tmp_path: Path) -> None:
    """Every candidate block carries the same metric keys (comparability)."""
    bundle_a = _make_bundle(tmp_path, seed=11)
    bundle_b = _make_bundle(tmp_path, seed=23)
    artifact = _runner().run(
        _bar_records(320),
        [
            ReplayCandidate("champion", "CHAMPION", str(bundle_a), {"confidence_threshold": 0.35}),
            ReplayCandidate(
                "challenger", "CHALLENGER", str(bundle_b), {"confidence_threshold": 0.35}
            ),
            ReplayCandidate(
                "always_low_conf", "CONTROL", str(bundle_b), {"confidence_threshold": 0.99}
            ),
        ],
        run_id="RBM-CMP",
    )
    blocks = artifact["candidates"]
    assert set(blocks) == {"champion", "challenger", "always_low_conf"}
    keys = set(blocks["champion"]["metrics"])
    for block in blocks.values():
        assert set(block["metrics"]) == keys
        assert block["metrics"]["evaluation_mode"] == "COUNTERFACTUAL_REPLAY"
    # Control (threshold 0.99) may differ from challenger; the point is that
    # all were evaluated on the identical stream with identical event hash.
    eh = artifact["stream"]["event_hash"]
    assert all(b["determinism"]["event_hash"] == eh for b in blocks.values())
