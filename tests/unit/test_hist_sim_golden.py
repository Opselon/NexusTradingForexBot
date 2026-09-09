"""I9 (audit rev2) — HIST-SIM v1 golden M5-derivation fixture test.

Golden test on a FIXED inline synthetic dataset: 45 one-minute slots
(2026-09-07 09:00..09:44 UTC) of deterministic XAUUSD-style bars with minute
offsets 7 and 33 REMOVED (43 bars remain). No file reads, no RNG, no clock.

Validates ``research/historical/bars.py`` M5 derivation against the
published contract (docs/historical_backtest_contract.md §1):

- M5 timestamps are floored to 5-minute UTC boundaries;
- a valid M5 bar requires ALL 5 constituent minutes -> the buckets containing
  the two missing minutes are SKIPPED and recorded in ``gaps``;
- NO forward fill: exactly 7 M5 bars derive from 43 M1 bars spanning
  9 buckets with 2 corrupted buckets (never fabricated);
- OHLC validity of every derived bar (high >= max(o,c), low <= min(o,c),
  all > 0), O = first minute open, C = last minute close, H/L = extrema,
  volume summed, spread from the first constituent minute;
- dataset identity is a deterministic SHA-256 over the canonical JSON of the
  validated M1 bars (same input -> same hash; one-close perturbation ->
  different hash).

The historical/ module is FOREIGN (untracked, other handoff owns it): this
file tests its ACTUAL API as found at audit rev2. If the module drifts,
this golden test is the tripwire.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BARS_PATH = REPO_ROOT / "src" / "nexus_scalp" / "research" / "historical" / "bars.py"


def _load_bars_module() -> Any:
    """Load bars.py by path.

    The historical/ subpackage is foreign/untracked and its package
    __init__ pulls torch-dependent factory imports (segment-mapping failures
    on memory-constrained CI runners). bars.py itself only needs pydantic —
    loading it directly keeps this golden test hermetic AND small enough for
    the 4GB gate box. If the module is moved/deleted the load fails loudly,
    which is exactly what a tripwire should do.
    """
    if not BARS_PATH.exists():  # pragma: no cover - tripwire
        pytest.fail(f"foreign historical/bars.py not found at {BARS_PATH}")
    spec = importlib.util.spec_from_file_location("hist_bars_golden", BARS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # bars.py's `from __future__ import annotations` defers its `datetime`
    # annotations; give pydantic the real type before rebuilding the models.
    import datetime as _datetime_mod

    module.datetime = _datetime_mod.datetime
    module.UTC = _datetime_mod.UTC
    module.timedelta = _datetime_mod.timedelta
    spec.loader.exec_module(module)
    module.M1Bar.model_rebuild(_types_namespace={"datetime": _datetime_mod.datetime})
    module.DerivedM5.model_rebuild(_types_namespace={"datetime": _datetime_mod.datetime})
    return module


bars_mod = _load_bars_module()

FIXTURE_START = datetime(2026, 9, 7, 9, 0, 0, tzinfo=UTC)
N_SLOTS = 45
MISSING_OFFSETS = frozenset({7, 33})  # minutes dropped from the raw feed
SYMBOL = "XAUUSD"


def _fixture_bars() -> list[Any]:
    """The 43 deterministic M1 bars (fixed timestamps AND prices)."""
    out = []
    for i in range(N_SLOTS):
        if i in MISSING_OFFSETS:
            continue
        ts = FIXTURE_START + timedelta(minutes=i)
        o = 2000.0 + i * 0.5
        c = o + (0.1 if i % 2 == 0 else -0.1)
        h = max(o, c) + 0.2
        low = min(o, c) - 0.2
        out.append(
            bars_mod.M1Bar(
                timestamp=ts,
                open=o,
                high=h,
                low=low,
                close=c,
                tick_volume=(i % 7) + 1,
                spread_points=0.15,
            )
        )
    return out


def _m1_canonical_json(m1_bars: list[Any]) -> str:
    """Mirror of bars.py canonical form (doc-contract: identity over M1)."""
    return json.dumps(
        [
            [
                b.timestamp.isoformat(),
                b.open,
                b.high,
                b.low,
                b.close,
                b.tick_volume,
                b.spread_points,
            ]
            for b in m1_bars
        ],
        separators=(",", ":"),
    )


class TestM5DerivationGolden:
    def test_fixture_shape(self) -> None:
        """The fixture itself is pinned: 43 bars, gaps exactly at 09:07/09:33."""
        m1 = _fixture_bars()
        assert len(m1) == 43
        stamps = [b.timestamp for b in m1]
        assert stamps[0] == FIXTURE_START
        assert stamps[-1] == FIXTURE_START + timedelta(minutes=44)
        assert datetime(2026, 9, 7, 9, 7, tzinfo=UTC) not in stamps
        assert datetime(2026, 9, 7, 9, 33, tzinfo=UTC) not in stamps
        assert all(b.timestamp.tzinfo is UTC for b in m1)

    def test_m5_floor_and_count(self) -> None:
        """9 five-minute buckets in range; the 2 corrupted ones are skipped."""
        m5, gaps = bars_mod.build_m5(_fixture_bars())
        assert len(m5) == 7
        assert [b.timestamp for b in m5] == [
            datetime(2026, 9, 7, 9, m, tzinfo=UTC) for m in (0, 10, 15, 20, 25, 35, 40)
        ]
        # every M5 timestamp sits exactly on a 5-minute UTC boundary
        for b in m5:
            assert b.timestamp.minute % 5 == 0
            assert b.timestamp.second == 0 and b.timestamp.microsecond == 0
            assert b.timestamp.tzinfo is UTC

    def test_m5_gaps_recorded_no_forward_fill(self) -> None:
        """Missing minute => bucket skipped + recorded in gaps; never filled."""
        m5, gaps = bars_mod.build_m5(_fixture_bars())
        assert len(gaps) == 2
        assert gaps[0] == ("2026-09-07T09:05:00+00:00 missing=['2026-09-07T09:07:00+00:00']")
        assert gaps[1] == ("2026-09-07T09:30:00+00:00 missing=['2026-09-07T09:33:00+00:00']")
        # the corrupted buckets must NOT appear as fabricated bars
        bucket_stamps = {b.timestamp for b in m5}
        assert datetime(2026, 9, 7, 9, 5, tzinfo=UTC) not in bucket_stamps
        assert datetime(2026, 9, 7, 9, 30, tzinfo=UTC) not in bucket_stamps

    def test_m5_ohlc_aggregation_validity(self) -> None:
        """O/C from first/last minute, H/L extrema, volume summed, OHLC valid."""
        m5, _gaps = bars_mod.build_m5(_fixture_bars())
        first = m5[0]
        # minutes 0..4: o=2000.0/2000.5/2001.0/2001.5/2002.0,
        # c=2000.1/2000.4/2001.1/2001.4/2002.1
        assert first.open == pytest.approx(2000.0)  # first minute open
        assert first.close == pytest.approx(2002.1)  # last minute close
        assert first.high == pytest.approx(max(2002.0, 2002.1) + 0.2)
        assert first.low == pytest.approx(min(2000.0, 2000.1) - 0.2)
        assert first.tick_volume == sum((i % 7) + 1 for i in range(5))
        assert first.spread_points == pytest.approx(0.15)  # first minute
        assert first.minutes == 5
        for b in m5:
            assert b.open > 0 and b.close > 0 and b.high > 0 and b.low > 0
            assert b.high >= max(b.open, b.close)
            assert b.low <= min(b.open, b.close)
        # last surviving bucket (09:40..09:44) pinned end-to-end
        last = m5[-1]
        assert last.timestamp == datetime(2026, 9, 7, 9, 40, tzinfo=UTC)
        assert last.open == pytest.approx(2020.0)  # minute 40 open
        assert last.close == pytest.approx(2022.1)  # minute 44 close
        assert last.tick_volume == sum((i % 7) + 1 for i in range(40, 45))

    def test_dataset_identity_deterministic_sha256(self) -> None:
        """Manifest sha256 == independent sha256 over canonical M1 JSON."""
        m1 = _fixture_bars()
        m5, gaps = bars_mod.build_m5(m1)
        manifest = bars_mod.dataset_manifest(
            SYMBOL, "M5", m1, {"rows_total": len(m1)}, len(m5), gaps
        )
        expected = hashlib.sha256(_m1_canonical_json(m1).encode()).hexdigest()
        assert manifest["sha256"] == expected
        assert manifest["symbol"] == SYMBOL
        assert manifest["timeframe_source"] == "M1"
        assert manifest["timeframe_derived"] == "M5"
        assert manifest["timezone"] == "UTC"
        assert manifest["m1_valid"] == 43
        assert manifest["m5_derived"] == 7
        assert manifest["gaps"] == gaps
        # determinism: rebuilding the identical dataset yields the same hash
        again = bars_mod.dataset_manifest(
            SYMBOL, "M5", _fixture_bars(), {"rows_total": len(m1)}, len(m5), gaps
        )
        assert again["sha256"] == expected
        # sensitivity: one changed close -> different identity
        perturbed = [
            b.model_copy(update={"close": b.close + 0.01}) if k == 10 else b
            for k, b in enumerate(_fixture_bars())
        ]
        p_manifest = bars_mod.dataset_manifest(SYMBOL, "M5", perturbed, {}, 7, [])
        assert p_manifest["sha256"] != expected
        assert manifest["range_start"] == "2026-09-07T09:00:00+00:00"
        assert manifest["range_end"] == "2026-09-07T09:44:00+00:00"

    def test_empty_dataset_degrades_cleanly(self) -> None:
        """No bars -> no M5, no gaps, manifest identity of empty input."""
        m5, gaps = bars_mod.build_m5([])
        assert m5 == [] and gaps == []
        manifest = bars_mod.dataset_manifest(SYMBOL, "M5", [], {}, 0, [])
        assert manifest["m1_valid"] == 0 and manifest["m5_derived"] == 0
        assert manifest["range_start"] is None and manifest["range_end"] is None
        assert manifest["sha256"] == hashlib.sha256(b"[]").hexdigest()
