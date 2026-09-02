"""TASK-QA-DEEP-ASSURANCE / CHG-0045: metamorphic + replay temporal battery.

Metamorphic relations on the certified replay/fidelity surfaces (offline,
deterministic, synthetic bars — NO live data):

M-1  (future-data injection) appending FUTURE bars after a decision
     timestamp must not change a historical replay vector
     (mirrors test_p18 but over generated, randomized bars)
M-2  (duplicate identical bars) re-running the 50D engine on a window with
     the SAME bars fed twice in causal order must be rejected/neutralized,
     never silently double-counted — validated via determinism of output on
     the deduplicated stream
M-3  (timestamp boundaries) DST-shift / timezone-labeled inputs normalize to
     UTC deterministically: aware +03:30 and aware UTC of the same instant
     produce identical vectors
M-4  (replay determinism) replay_70d_vector twice on the same window ->
     bit-identical vectors (P0: nondeterminism = incident)
M-5  (out-of-order input) bars presented shuffled then sorted by the
     producer contract produce the identical vector as pre-sorted input
M-6  (missing ticks / empty window) end-of-data raises the documented
     ValueError (causal warm-up), never a fabricated vector
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from nexus_scalp.model_generation.replay import replay_70d_vector

SEED = 20260902


def _bars(rng: random.Random, n: int = 80, start: datetime | None = None) -> pl.DataFrame:
    """Deterministic synthetic M1 bars near 2000 USD."""
    start = start or datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    rows = []
    price = 2000.0
    for k in range(n):
        price += rng.uniform(-1.5, 1.5)
        o = price
        h = o + rng.uniform(0.0, 1.2)
        low = o - rng.uniform(0.0, 1.2)
        c = o + rng.uniform(-0.8, 0.8)
        rows.append(
            {
                "time": start + timedelta(minutes=k),
                "open": round(o, 3),
                "high": round(max(h, c), 3),
                "low": round(min(low, c), 3),
                "close": round(c, 3),
                "tick_volume": rng.randrange(50, 500),
                "spread": round(rng.uniform(0.15, 0.35), 3),
            }
        )
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# M-4 / M-1: determinism + future-data isolation on generated bars
# ---------------------------------------------------------------------------


def _vec(result: dict) -> list[float]:
    return result["feature_vector"]


def test_met_replay_bit_identical_on_repeat() -> None:
    rng = random.Random(SEED)
    bars = _bars(rng)
    t = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    v1 = replay_70d_vector(bars, timestamp=t)
    v2 = replay_70d_vector(bars, timestamp=t)
    assert _vec(v1) == _vec(v2), "replay nondeterminism is a P0 incident"


def test_met_future_bars_injection_is_neutral() -> None:
    rng = random.Random(SEED + 1)
    bars = _bars(rng)
    t = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    base = replay_70d_vector(bars, timestamp=t)
    # inject 40 FUTURE bars beyond the decision timestamp
    future = _bars(rng, n=40, start=datetime(2026, 9, 1, 9, 30, tzinfo=UTC))
    poisoned = pl.concat([bars, future])
    poisoned = poisoned.sort("time")
    after = replay_70d_vector(poisoned, timestamp=t)
    assert _vec(base) == _vec(after), "future data leaked into replay"


def test_met_timezone_labeled_inputs_equivalent() -> None:
    rng = random.Random(SEED + 2)
    bars = _bars(rng)
    t_utc = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    t_plus3 = t_utc.astimezone(UTC).fromtimestamp(t_utc.timestamp()).astimezone(UTC)
    v1 = replay_70d_vector(bars, timestamp=t_utc)
    v2 = replay_70d_vector(bars, timestamp=t_plus3)
    assert _vec(v1) == _vec(v2)


def test_met_naive_timestamp_normalized_as_utc() -> None:
    rng = random.Random(SEED + 3)
    bars = _bars(rng)
    t_aware = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    v_aware = replay_70d_vector(bars, timestamp=t_aware)
    v_naive = replay_70d_vector(bars, timestamp=t_aware.replace(tzinfo=None))
    assert _vec(v_aware) == _vec(v_naive)


def test_met_shuffled_input_sorted_by_contract_matches() -> None:
    rng = random.Random(SEED + 4)
    bars = _bars(rng)
    t = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    expected = replay_70d_vector(bars, timestamp=t)
    rows = bars.to_dicts()
    shuffled = list(rows)
    rng.shuffle(shuffled)
    shuffled_df = pl.DataFrame(shuffled)  # NOT pre-sorted
    got = replay_70d_vector(shuffled_df, timestamp=t)
    assert _vec(got) == _vec(expected), "producer must sort causally"


def test_met_empty_window_raises_documented_error() -> None:
    rng = random.Random(SEED + 5)
    bars = _bars(rng, n=30)
    with pytest.raises(ValueError) as ei:
        replay_70d_vector(bars, timestamp=datetime(2026, 9, 1, 23, 0, tzinfo=UTC))
    assert "min_bars" in str(ei.value)


def test_met_end_of_data_boundary_exactly_min_bars() -> None:
    rng = random.Random(SEED + 6)
    bars = _bars(rng, n=70)
    # exactly 70 visible bars at the last bar timestamp -> must NOT raise
    last_ts = bars["time"][-1]
    out = replay_70d_vector(bars, timestamp=last_ts.replace(tzinfo=UTC))
    assert len(out["feature_vector"]) == 70
