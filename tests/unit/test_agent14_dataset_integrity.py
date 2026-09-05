"""Agent 14 RED regression tests — dataset acquisition/immutability layer.

These tests encode the CHG-0061 fix contracts. Written RED against the
pre-fix tree; turn GREEN with the Agent 14 hardening commit.

Contracts (MT5_TICK_DATASET v3 / DatasetArtifactImmutability v1):
  R1  acquire_bars: explicit `end` semantics — id is (symbol, kind, start,
      end); identical windows produce identical ids across wall-clock days.
  R2  acquire_bars meta carries provenance v2 fields (meta_version=2,
      git_commit, end) and unknown values are NOT_RECORDED.
  R3  Empty completed acquisition is cached but marked complete=True with
      per-chunk accounting; a dead/unavailable adapter (no rows for ANY
      chunk + adapter reports unavailability) must NOT be cached as a
      complete dataset (raises AcquisitionIncomplete).
  R4  Containment: adapter rows outside [start, end) are dropped and
      counted (meta['out_of_window']), never stored silently.
  R5  Path safety: hostile symbols rejected at the acquisition boundary.
  R6  Immutability: acquire over an existing id whose parquet exists but
      whose meta is missing/corrupt must RAISE (conflict), never overwrite.
  R7  Read-path integrity: load() recomputes the fingerprint and rejects
      mutated / swapped / appended artifacts (DatasetCorruptionError).
  R8  Concurrency: simultaneous identical acquisitions converge to ONE
      consistent artifact (no duplicates, no torn writes, one winner,
      losers either reuse cache or fail loudly without partial state).
"""

from __future__ import annotations

import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.research.mt5_tick_dataset import (
    DatasetCorruptionError,
    DatasetIdentityError,
    MT5TickDataset,
)

T0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
END = T0 + timedelta(minutes=10)


class _Snap:
    def __init__(self, ts: datetime, bid: float) -> None:
        self.time_utc = ts
        self.bid = bid
        self.ask = bid + 0.2
        self.time_msc = int(ts.timestamp() * 1000)
        self.last = 0.0
        self.flags = 0
        self.volume = 1.0


def _make_adapter(rows_fn):
    class _A:
        def get_tick_history(self, symbol, count=100_000, from_utc=None, to_utc=None):
            return rows_fn(from_utc, to_utc)

    return _A()


def _healthy_adapter(n: int = 5, bid: float = 3300.0):
    return _make_adapter(lambda f, t: [_Snap(T0 + timedelta(minutes=m), bid) for m in range(n)])


# ---------------------------------------------------------------------------
# R1/R2 — acquire_bars explicit-end identity + provenance v2
# ---------------------------------------------------------------------------


class _Bar:
    def __init__(self, ts: datetime, o: float) -> None:
        self.time_utc = ts
        self.open = o
        self.high = o + 1.0
        self.low = o - 1.0
        self.close = o + 0.5
        self.tick_volume = 100
        self.spread = 20


class _BarsAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def get_rate_history(self, symbol, timeframe="M1", count=100_000, from_utc=None):
        self.calls += 1
        return [_Bar(from_utc + timedelta(minutes=m), 3300.0 + m) for m in range(60)]


def test_acquire_bars_identity_uses_explicit_end_not_wall_clock(tmp_path) -> None:
    ds = MT5TickDataset(cache_root=tmp_path)
    end = T0 + timedelta(minutes=60)
    id1 = ds.acquire_bars(_BarsAdapter(), symbol="XAUUSD", start=T0, end=end, timeframe="M1")
    id2 = ds.acquire_bars(_BarsAdapter(), symbol="XAUUSD", start=T0, end=end, timeframe="M1")
    assert id1 == id2, "same historical window must yield the SAME dataset id"


def test_acquire_bars_meta_provenance_v2(tmp_path) -> None:
    ds = MT5TickDataset(cache_root=tmp_path)
    end = T0 + timedelta(minutes=60)
    ds_id = ds.acquire_bars(
        _BarsAdapter(), symbol="XAUUSD", start=T0, end=end, timeframe="M1", git_commit=""
    )
    meta = ds.meta(ds_id)
    assert meta["meta_version"] == 2
    assert meta["end"] == end.isoformat()
    assert meta["git_commit"] == "NOT_RECORDED"


# ---------------------------------------------------------------------------
# R3 — partial/failed acquisition never masquerades as complete
# ---------------------------------------------------------------------------


def test_dead_adapter_does_not_publish_complete_dataset(tmp_path) -> None:
    calls = {"n": 0}

    def dead(from_utc, to_utc):
        calls["n"] += 1
        return []

    ds = MT5TickDataset(cache_root=tmp_path)
    with pytest.raises(Exception) as excinfo:
        ds.acquire_ticks(_make_adapter(dead), symbol="XAUUSD", start=T0, end=END, chunk_minutes=5)
    assert "acquisition" in str(excinfo.value).lower()
    assert not (tmp_path / f"XAUUSD_ticks_{''}".replace("XAUUSD_ticks_", "")).exists() or not any(
        tmp_path.glob("*.parquet")
    ), "failed acquisition must not leave a cached complete artifact"


def test_empty_healthy_window_cached_with_complete_marker(tmp_path) -> None:
    class HealthyEmptyAdapter:
        available = True

        def get_tick_history(self, symbol, count=100_000, from_utc=None, to_utc=None):
            return []

    ds = MT5TickDataset(cache_root=tmp_path)
    ds_id = ds.acquire_ticks(
        HealthyEmptyAdapter(), symbol="XAUUSD", start=T0, end=END, chunk_minutes=5
    )
    meta = ds.meta(ds_id)
    assert meta["complete"] is True
    assert meta["records"] == 0


def test_partial_outage_annotated_not_silent(tmp_path) -> None:
    state = {"n": 0}

    def partial(from_utc, to_utc):
        state["n"] += 1
        if state["n"] == 1:
            return [_Snap(T0 + timedelta(minutes=m), 3300.0) for m in range(5)]
        return []

    ds = MT5TickDataset(cache_root=tmp_path)
    ds_id = ds.acquire_ticks(
        _make_adapter(partial), symbol="XAUUSD", start=T0, end=END, chunk_minutes=5
    )
    meta = ds.meta(ds_id)
    assert meta["complete"] is False, "a window with empty chunks is not a complete acquisition"


# ---------------------------------------------------------------------------
# R4 — containment gate
# ---------------------------------------------------------------------------


def test_out_of_window_ticks_dropped_and_counted(tmp_path) -> None:
    def rogue(from_utc, to_utc):
        assert from_utc is not None and to_utc is not None
        return [
            _Snap(from_utc - timedelta(minutes=30), 1000.0),
            _Snap(from_utc + timedelta(minutes=1), 3300.0),
            _Snap(to_utc + timedelta(minutes=30), 5000.0),
        ]

    ds = MT5TickDataset(cache_root=tmp_path)
    ds_id = ds.acquire_ticks(
        _make_adapter(rogue), symbol="XAUUSD", start=T0, end=END, chunk_minutes=60
    )
    recs = ds.load(ds_id)
    assert len(recs) == 1, "only in-window ticks stored"
    assert ds.meta(ds_id)["out_of_window"] == 2


# ---------------------------------------------------------------------------
# R5 — path safety at the acquisition boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", ["../../evil", "a/b", "C:", "..", "XAU/USD", ""])
def test_hostile_symbol_rejected(symbol: str, tmp_path) -> None:
    ds = MT5TickDataset(cache_root=tmp_path)
    with pytest.raises(ValueError):
        ds.acquire_ticks(_healthy_adapter(), symbol=symbol, start=T0, end=END, chunk_minutes=60)


# ---------------------------------------------------------------------------
# R6 — immutability: no silent rebuild under an existing id
# ---------------------------------------------------------------------------


def test_orphan_parquet_blocks_reacquire_same_id(tmp_path) -> None:
    ds = MT5TickDataset(cache_root=tmp_path)
    ds_id = ds.acquire_ticks(
        _healthy_adapter(bid=3300.0), symbol="XAUUSD", start=T0, end=END, chunk_minutes=60
    )
    # simulate interrupted/lost meta write
    (tmp_path / f"{ds_id}.meta.json").unlink()

    def corrected(from_utc, to_utc):
        return [_Snap(T0 + timedelta(minutes=m), 9999.0) for m in range(5)]

    with pytest.raises(Exception) as excinfo:
        ds.acquire_ticks(
            _make_adapter(corrected), symbol="XAUUSD", start=T0, end=END, chunk_minutes=60
        )
    assert "conflict" in str(excinfo.value).lower() or "immutable" in str(excinfo.value).lower()
    # original bytes untouched
    recs = ds.load(ds_id)
    assert recs[0]["bid"] == 3300.0


def test_corrupt_meta_blocks_reacquire_same_id(tmp_path) -> None:
    ds = MT5TickDataset(cache_root=tmp_path)
    ds_id = ds.acquire_ticks(
        _healthy_adapter(), symbol="XAUUSD", start=T0, end=END, chunk_minutes=60
    )
    (tmp_path / f"{ds_id}.meta.json").write_text("{CORRUPT", encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError)):
        ds.acquire_ticks(_healthy_adapter(), symbol="XAUUSD", start=T0, end=END, chunk_minutes=60)


# ---------------------------------------------------------------------------
# R7 — read-path integrity (DETECT/REJECT)
# ---------------------------------------------------------------------------


def test_load_rejects_tampered_records(tmp_path) -> None:
    import polars as pl

    ds = MT5TickDataset(cache_root=tmp_path)
    ds_id = ds.acquire_ticks(
        _healthy_adapter(), symbol="XAUUSD", start=T0, end=END, chunk_minutes=60
    )
    p = tmp_path / f"{ds_id}.parquet"
    frame = pl.read_parquet(p)
    mutated = frame.with_columns(pl.lit(5555.0).alias("bid"))
    mutated.write_parquet(p)
    with pytest.raises(Exception) as excinfo:
        ds.load(ds_id)
    assert "corrupt" in str(excinfo.value).lower() or "fingerprint" in str(excinfo.value).lower()


def test_load_rejects_swapped_foreign_dataset(tmp_path) -> None:
    ds = MT5TickDataset(cache_root=tmp_path)
    ds_id = ds.acquire_ticks(
        _healthy_adapter(), symbol="XAUUSD", start=T0, end=END, chunk_minutes=60
    )
    other_id = ds.acquire_ticks(
        _healthy_adapter(bid=1111.0),
        symbol="EURUSD",
        start=T0,
        end=END,
        chunk_minutes=60,
    )
    other_p = tmp_path / f"{other_id}.parquet"
    this_p = tmp_path / f"{ds_id}.parquet"
    backup = this_p.read_bytes()
    other_p.replace(this_p)
    with pytest.raises((ValueError, RuntimeError, DatasetIdentityError, DatasetCorruptionError)):
        ds.load(ds_id)
    this_p.write_bytes(backup)


def test_load_rejects_appended_row(tmp_path) -> None:
    import polars as pl

    ds = MT5TickDataset(cache_root=tmp_path)
    ds_id = ds.acquire_ticks(
        _healthy_adapter(), symbol="XAUUSD", start=T0, end=END, chunk_minutes=60
    )
    p = tmp_path / f"{ds_id}.parquet"
    frame = pl.read_parquet(p)
    extra = dict(frame.to_dicts()[0])
    extra["bid"] = 42.0
    pl.concat([frame, pl.DataFrame([extra])]).write_parquet(p)
    with pytest.raises((ValueError, RuntimeError, DatasetIdentityError, DatasetCorruptionError)):
        ds.load(ds_id)


def test_load_detects_manifest_record_count_mismatch(tmp_path) -> None:
    import json

    ds = MT5TickDataset(cache_root=tmp_path)
    ds_id = ds.acquire_ticks(
        _healthy_adapter(), symbol="XAUUSD", start=T0, end=END, chunk_minutes=60
    )
    meta = ds.meta(ds_id)
    meta2 = dict(meta)
    meta2["records"] = meta["records"] + 7
    (tmp_path / f"{ds_id}.meta.json").write_text(json.dumps(meta2), encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError, DatasetIdentityError, DatasetCorruptionError)):
        ds.load(ds_id)


# ---------------------------------------------------------------------------
# R8 — concurrency
# ---------------------------------------------------------------------------


def test_concurrent_identical_acquisition_converges(tmp_path) -> None:
    ds = MT5TickDataset(cache_root=tmp_path)
    results: list[str] = []
    errors: list[str] = []
    lock = threading.Lock()

    def run() -> None:
        try:
            r = ds.acquire_ticks(
                _healthy_adapter(), symbol="XAUUSD", start=T0, end=END, chunk_minutes=5
            )
            with lock:
                results.append(r)
        except Exception as e:
            with lock:
                errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    parquet_files = list(tmp_path.glob("*.parquet"))
    assert len(parquet_files) <= 1, "no duplicate artifacts"
    leftovers = [p for p in tmp_path.glob("*.tmp")]
    assert not leftovers, f"torn tmp state: {leftovers}"
    assert len(results) + len(errors) == 4
    if results:
        recs = ds.load(results[0])
        assert len(recs) == 10, "no duplicated rows from racing writers"
