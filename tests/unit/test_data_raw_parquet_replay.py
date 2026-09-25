"""DATA-RAW-01 — the REPLAY raw-bars fallback reads the producer's format.

Root cause (doctor /health, category DATA): ``nexus data-fetch`` writes
``data/raw/<SYM>_<TF>.parquet`` (``doctor.py``'s only producer), while every
raw-bars READER read the CSV sibling that nothing in the tree ever writes
(``grep -rn "write_csv" src/ scripts/`` -> zero sites). REPLAY's raw-bars
fallback was therefore permanently dead on any machine that used only the
shipped commands, and the DATA health gate could never be satisfied by the
data pipeline it was built around.

Contract pinned here (LANE 1 of the DATA contract):

1. ``RAW_M1_BARS_PATH`` / ``PaperDataConfig.raw_bars_path`` default to the
   parquet — the format ``data-fetch`` actually produces.
2. Both extensions are accepted: an explicit ``.csv`` path that exists still
   loads (back-compat for operators with the old export); otherwise parquet.
3. A relative path absent from CWD is resolved repo-root-relative, because the
   engine can be started from another cwd while ``data-fetch`` writes from the
   repo root. An explicitly configured ABSOLUTE path is always used as-is.
4. Fail-closed semantics are untouched: missing / corrupt / unreadable sources
   raise ``ReplayDataUnavailableError`` — never a silent synthetic fallback.
5. Identity fields (``source``, ``record_count``, ``start``, ``end``),
   chronological order and ``market_data_mode == "REPLAY"`` are preserved.

Fixtures write ONLY into per-test ``tmp_path`` — nothing is written into the
worktree's own ``data/raw``, so the environment-gated real-data probe below
can never be satisfied by a fixture of our own making.
"""

from __future__ import annotations

import contextlib
import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType

import polars as pl
import pytest

from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.adapters.paper.paper_data import build_paper_adapter
from nexus_scalp.adapters.paper.replay_source import (
    RAW_M1_BARS_PATH,
    ReplayDataUnavailableError,
    ReplayTickSource,
    _resolve_raw_bars_path,
)
from nexus_scalp.configuration.config import PaperDataConfig

_T0 = datetime(2026, 5, 1, 12, 0)  # naive UTC: the data-fetch parquet convention

#: The exact column schema ``nexus data-fetch`` writes (doctor.py:data_fetch).
_EXPORT_COLUMNS = [
    "time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
    "time_utc",
]


def _bars_frame(n: int = 5, start: datetime = _T0) -> pl.DataFrame:
    """A frame shaped exactly like ``data-fetch``'s parquet output.

    ``time_utc`` is a NAIVE UTC datetime[us] — that is what ``data-fetch``
    writes (``doctor.py``: ``b.time_utc`` straight into the frame) and what
    the CSV export encodes as an ISO string; the loader normalizes both to
    the same naive-UTC timeline.
    """
    price = 3200.0
    rows = []
    ts = start
    for _i in range(n):
        rows.append(
            {
                "time": int(ts.replace(tzinfo=UTC).timestamp()),
                "open": price,
                "high": price + 0.4,
                "low": price - 0.4,
                "close": price + 0.1,
                "tick_volume": 100,
                "spread": 20,
                "real_volume": 0,
                "time_utc": ts,
            }
        )
        price += 0.5
        ts = ts + timedelta(minutes=1)
    return pl.DataFrame(rows, orient="row")


def _write_parquet(path: Path, n: int = 5, start: datetime = _T0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _bars_frame(n, start).write_parquet(path)
    return path


def _csv_text(n: int = 5, start: datetime = _T0) -> str:
    """The legacy CSV export: same columns, time_utc as ISO strings."""
    out = io.StringIO()
    out.write(",".join(_EXPORT_COLUMNS) + "\n")
    ts = start
    price = 3200.0
    for _i in range(n):
        out.write(
            f"{int(ts.timestamp())},{price},{price + 0.4},{price - 0.4},"
            f"{price + 0.1},100,20,0,{ts.isoformat()}\n"
        )
        price += 0.5
        ts = ts + timedelta(minutes=1)
    return out.getvalue()


def _write_csv(path: Path, n: int = 5, start: datetime = _T0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_csv_text(n, start), encoding="utf-8")
    return path


def _replay(path: Path | str) -> ReplayTickSource:
    """Construct a raw-bars replay source over ``path`` (fallback allowed)."""
    return ReplayTickSource(symbol="XAUUSD", allow_raw_fallback=True, raw_bars_path=str(path))


@contextlib.contextmanager
def _hold_dir(path: Path) -> Iterator[Path | None]:
    """Temporarily hide ``path`` so tests can assert on an empty data world.

    The repo's ``data/raw`` may hold the host operator's gitignored export;
    renaming it away (rather than deleting) keeps the data safe and is always
    restored, including on failure. Yields the backup location or None when
    ``path`` did not exist.
    """
    if not path.exists():
        yield None
        return
    backup = path.with_name(path.name + "._test_hold")
    path.rename(backup)
    try:
        yield backup
    finally:
        if backup.exists():
            backup.rename(path)


def _release_dir(backup: Path | None) -> None:
    """Restore a directory hidden by :func:`_hold_dir` (no-op when None).

    Kept as an explicit escape hatch for tests that cannot use the context
    manager form; :func:`_hold_dir` restores itself and does not need this.
    """
    if backup is None or not backup.exists():
        return
    backup.rename(backup.with_name(backup.name[: -len("._test_hold")]))


# ---------------------------------------------------------------------------
# 1. The default is the producer's format
# ---------------------------------------------------------------------------


def test_raw_m1_bars_path_default_is_the_parquet_producer_format() -> None:
    """The fallback must default to what `nexus data-fetch` actually writes."""
    assert RAW_M1_BARS_PATH == "data/raw/XAUUSD_M1.parquet"


def test_config_default_matches_replay_source_default() -> None:
    """PaperDataConfig and ReplayTickSource must agree on one canonical path."""
    assert PaperDataConfig().raw_bars_path == RAW_M1_BARS_PATH
    assert PaperDataConfig().raw_bars_path.endswith(".parquet")


# ---------------------------------------------------------------------------
# 2. Parquet load (the canonical format)
# ---------------------------------------------------------------------------


def test_parquet_load_produces_replay_source(tmp_path: Path) -> None:
    pq = _write_parquet(tmp_path / "XAUUSD_M1.parquet")
    src = _replay(pq)
    prov = src.identity()
    assert prov["market_data_mode"] == "REPLAY"
    assert prov["source"] == "RAW_BARS_PARQUET"
    assert prov["record_count"] == 5
    assert prov["start"] < prov["end"]
    assert prov["path"] == str(pq)


def test_parquet_load_is_chronological_and_serves_historical_timestamps(tmp_path: Path) -> None:
    pq = _write_parquet(tmp_path / "XAUUSD_M1.parquet", n=4)
    src = _replay(pq)
    t1 = src.next_tick()
    t2 = src.next_tick()
    assert t1 is not None and t2 is not None
    assert t2["timestamp"] > t1["timestamp"], "chronology must be ascending"
    assert t1["timestamp"] == _T0, "replay serves the HISTORICAL timestamp"
    assert t2["ask"] > t2["bid"], "direction-aware quote (close + spread)"


def test_parquet_shuffled_input_is_sorted_to_chronology(tmp_path: Path) -> None:
    """The loader sorts; a shuffled file must still replay in order."""
    frame = _bars_frame(n=6).sample(seed=7, shuffle=True)
    pq = _write_parquet(tmp_path / "shuffled.parquet")
    frame.write_parquet(pq)
    src = _replay(pq)
    stamps = [r["timestamp"] for r in src._records]
    assert stamps == sorted(stamps)


def test_parquet_duplicate_timestamps_are_deduplicated(tmp_path: Path) -> None:
    """Duplicate bars collapse to one timeline entry (causal cursor)."""
    frame = pl.concat([_bars_frame(n=3), _bars_frame(n=3)])
    pq = _write_parquet(tmp_path / "dup.parquet")
    frame.write_parquet(pq)
    src = _replay(pq)
    assert src.identity()["record_count"] == 3
    stamps = [r["timestamp"] for r in src._records]
    assert len(stamps) == len(set(stamps))


def test_parquet_bar_record_pricing_uses_close_and_spread(tmp_path: Path) -> None:
    pq = _write_parquet(tmp_path / "one.parquet", n=1)
    src = _replay(pq)
    tick = src.next_tick()
    assert tick is not None
    assert tick["bid"] == pytest.approx(3200.1)  # close
    assert tick["ask"] == pytest.approx(3200.1 + 20.0)  # close + recorded spread


def test_parquet_history_bars_window_is_causal(tmp_path: Path) -> None:
    pq = _write_parquet(tmp_path / "hist.parquet", n=5)
    src = _replay(pq)
    assert src.next_tick() is not None  # advance the cursor
    bars = src.history_bars("M1", 100)
    assert len(bars) == 1, "only bars strictly behind the cursor are exposed"
    assert {b["timestamp"] for b in bars} < {r["timestamp"] for r in src._records}


def test_parquet_exhaustion_returns_none(tmp_path: Path) -> None:
    pq = _write_parquet(tmp_path / "one.parquet", n=2)
    src = _replay(pq)
    assert src.next_tick() is not None
    assert src.next_tick() is not None
    assert src.next_tick() is None
    assert src.exhausted
    assert len(src) == 0


def test_parquet_accepts_string_path_and_path_object(tmp_path: Path) -> None:
    pq = _write_parquet(tmp_path / "one.parquet", n=1)
    s1 = _replay(pq)
    s2 = ReplayTickSource(symbol="XAUUSD", allow_raw_fallback=True, raw_bars_path=pq)
    assert s1.identity()["record_count"] == s2.identity()["record_count"] == 1


def test_parquet_tz_aware_time_utc_is_normalized_to_naive_utc(tmp_path: Path) -> None:
    """An export carrying tz info lands on the same naive-UTC timeline as
    data-fetch's own parquet (the CSV/parquet chronologies must match)."""
    frame = _bars_frame(n=2).with_columns(pl.col("time_utc").dt.replace_time_zone("UTC"))
    pq = _write_parquet(tmp_path / "tz.parquet")
    frame.write_parquet(pq)
    src = _replay(pq)
    assert [r["timestamp"] for r in src._records] == [_T0, _T0 + timedelta(minutes=1)]


def test_parquet_time_utc_as_int_epoch_is_coerced(tmp_path: Path) -> None:
    """A frame whose time_utc is stored as epoch seconds still parses."""
    frame = _bars_frame(n=2).with_columns(pl.col("time_utc").cast(pl.Int64))
    pq = _write_parquet(tmp_path / "epoch.parquet")
    frame.write_parquet(pq)
    src = _replay(pq)
    assert src.identity()["record_count"] == 2
    assert src.next_tick()["timestamp"] == _T0  # type: ignore[index]


def test_parquet_time_utc_as_epoch_microseconds_is_coerced(tmp_path: Path) -> None:
    """Epoch-microsecond magnitude parses too.

    data-fetch's ``time`` column is epoch seconds; this covers a parquet whose
    time_utc was stored with microsecond magnitude — the loader must not
    overflow past year 2262 on it. Built from the epoch-seconds ``time``
    column so the magnitude is genuinely microseconds.
    """
    us_frame = _bars_frame(n=2).with_columns((pl.col("time") * 1_000_000).cast(pl.Int64))
    pq = _write_parquet(tmp_path / "epoch_us.parquet")
    us_frame.write_parquet(pq)
    src = _replay(pq)
    assert src.identity()["record_count"] == 2
    assert src.next_tick()["timestamp"] == _T0  # type: ignore[index]


def test_parquet_string_timestamps_are_coerced(tmp_path: Path) -> None:
    """A frame whose time_utc is stored as ISO text still parses."""
    frame = _bars_frame(n=2).with_columns(pl.col("time_utc").cast(pl.String))
    pq = _write_parquet(tmp_path / "str.parquet")
    frame.write_parquet(pq)
    src = _replay(pq)
    assert src.identity()["record_count"] == 2
    assert src.next_tick()["timestamp"] == _T0  # type: ignore[index]


def test_parquet_unparseable_string_timestamps_fails_closed(tmp_path: Path) -> None:
    """Garbage text in time_utc yields zero parseable rows -> fail-closed."""
    frame = _bars_frame(n=2).with_columns(
        pl.Series("time_utc", ["not-a-date", "also-not-a-date"], dtype=pl.String)
    )
    pq = _write_parquet(tmp_path / "junk.parquet")
    frame.write_parquet(pq)
    with pytest.raises(ReplayDataUnavailableError):
        _replay(pq)


# ---------------------------------------------------------------------------
# 3. CSV back-compat (the legacy operator export) MUST remain working
# ---------------------------------------------------------------------------


def test_csv_load_still_works_for_back_compat(tmp_path: Path) -> None:
    csv = _write_csv(tmp_path / "XAUUSD_M1.csv")
    src = _replay(csv)
    prov = src.identity()
    assert prov["source"] == "RAW_BARS_CSV"
    assert prov["record_count"] == 5
    t1, t2 = src.next_tick(), src.next_tick()
    assert t1 is not None and t2 is not None
    assert t2["timestamp"] > t1["timestamp"]
    assert t2["ask"] > t2["bid"]


def test_csv_and_parquet_yield_the_same_chronology(tmp_path: Path) -> None:
    """Both formats must produce the same replay timeline shape."""
    pq = _write_parquet(tmp_path / "same.parquet", n=4)
    csv = _write_csv(tmp_path / "same.csv", n=4)
    a = _replay(pq)
    b = _replay(csv)
    assert [r["timestamp"] for r in a._records] == [r["timestamp"] for r in b._records]
    assert [r["close"] for r in a._records] == [r["close"] for r in b._records]


def test_csv_skips_unparseable_timestamps(tmp_path: Path) -> None:
    """Malformed rows are skipped, not fatal (existing CSV behavior)."""
    path = tmp_path / "skip.csv"
    text = _csv_text(n=2)
    text += "1777655700,1,2,0,1,1,1,0,not-a-timestamp\n"
    path.write_text(text, encoding="utf-8")
    src = _replay(path)
    assert src.identity()["record_count"] == 2


def test_csv_with_zero_rows_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text(",".join(_EXPORT_COLUMNS) + "\n", encoding="utf-8")
    with pytest.raises(ReplayDataUnavailableError):
        _replay(path)


# ---------------------------------------------------------------------------
# 4. Path resolution order
# ---------------------------------------------------------------------------


def test_resolution_prefers_configured_path_as_given(tmp_path: Path) -> None:
    csv = _write_csv(tmp_path / "operator_export.csv", n=2)
    assert _resolve_raw_bars_path(str(csv)) == csv


def test_resolution_prefers_absolute_configured_path(tmp_path: Path) -> None:
    """An absolute configured path is used as-is; it is never rewritten."""
    pq = _write_parquet(tmp_path / "custom" / "bars.parquet", n=2)
    csv = _write_csv(tmp_path / "custom" / "bars.csv", n=2)
    # The parquet sibling exists too, but the configured CSV absolute path wins.
    assert _resolve_raw_bars_path(str(csv)) == csv
    assert _resolve_raw_bars_path(str(pq)) == pq


def test_resolution_falls_back_to_repo_root_when_not_in_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative path missing from CWD is retried against the repo root."""
    from nexus_scalp.adapters.paper import replay_source

    repo_root = replay_source._REPO_ROOT
    rel = "data/raw/XAUUSD_M1.parquet"
    placed = _write_parquet(repo_root / rel, n=2)
    try:
        monkeypatch.chdir(tmp_path)  # cwd has no data/raw at all
        assert _resolve_raw_bars_path(RAW_M1_BARS_PATH) == placed
        assert _resolve_raw_bars_path(rel) == placed
        assert _resolve_raw_bars_path(str(placed)) == placed
    finally:
        placed.unlink(missing_ok=True)
        (repo_root / "data" / "raw").mkdir(parents=True, exist_ok=True)


def test_resolution_uses_cwd_first_for_relative_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing behavior preserved: CWD-relative default resolves from CWD."""
    pq = _write_parquet(tmp_path / RAW_M1_BARS_PATH, n=2)
    monkeypatch.chdir(tmp_path)
    assert _resolve_raw_bars_path(RAW_M1_BARS_PATH) == pq


def test_resolution_finds_csv_sibling_of_parquet_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy export: only the .csv exists (no parquet) -> CSV is used.

    Probed with the no-path default (None): the operator did not name a file,
    so the default candidates are consulted and the CSV sibling wins.
    """
    csv = _write_csv(tmp_path / "data/raw/XAUUSD_M1.csv", n=2)
    monkeypatch.chdir(tmp_path)
    assert _resolve_raw_bars_path(None) == csv
    assert _resolve_raw_bars_path(RAW_M1_BARS_PATH) is None, (
        "an explicit default-shaped path must resolve as that exact file, "
        "not fall through to the CSV sibling"
    )


def test_resolution_returns_none_when_nothing_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No candidate -> None, so the caller stays fail-closed.

    The CWD probe AND the repo-root probe must both see an empty world: the
    repo's own data/raw may hold the host operator's gitignored export, so the
    default candidates are temporarily renamed away for this test only.
    """
    from nexus_scalp.adapters.paper import replay_source

    repo_data = replay_source._REPO_ROOT / "data" / "raw"
    with _hold_dir(repo_data):
        monkeypatch.chdir(tmp_path)
        assert _resolve_raw_bars_path(RAW_M1_BARS_PATH) is None
        assert _resolve_raw_bars_path(None) is None  # the no-path default case
        assert _resolve_raw_bars_path(str(tmp_path / "absent.parquet")) is None
        assert _resolve_raw_bars_path("data/raw/XAUUSD_M1.parquet") is None, (
            "explicit relative path must not fall through to the default candidates"
        )


def test_resolution_ignores_unrelated_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file with a non-bars extension must not satisfy the fallback."""
    monkeypatch.chdir(tmp_path)  # do not see the host's real data/raw
    other = tmp_path / "notes.txt"
    other.write_text("not bars", encoding="utf-8")
    assert _resolve_raw_bars_path(str(other)) is None


# ---------------------------------------------------------------------------
# 5. Fail-closed semantics (never a synthetic fallback)
# ---------------------------------------------------------------------------


def test_missing_parquet_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """chdir isolation: on a host WITH real data/raw the default fallback would
    otherwise find the operator parquet — the missing-path contract must be
    tested against an empty world."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ReplayDataUnavailableError):
        _replay(tmp_path / "does_not_exist.parquet")


def test_missing_raw_fallback_disabled_fails_closed() -> None:
    with pytest.raises(ReplayDataUnavailableError):
        ReplayTickSource(symbol="XAUUSD", allow_raw_fallback=False)


def test_corrupt_parquet_fails_closed(tmp_path: Path) -> None:
    """A file that is not a valid parquet must raise, not serve junk."""
    bad = tmp_path / "corrupt.parquet"
    bad.write_bytes(b"NOT A PARQUET FILE AT ALL")
    with pytest.raises(ReplayDataUnavailableError):
        _replay(bad)


def test_empty_parquet_fails_closed(tmp_path: Path) -> None:
    empty = tmp_path / "empty.parquet"
    pl.DataFrame({"time_utc": []}).write_parquet(empty)
    with pytest.raises(ReplayDataUnavailableError):
        _replay(empty)


def test_parquet_missing_time_utc_column_fails_closed(tmp_path: Path) -> None:
    """A frame without the chronological authority is rejected."""
    bad = tmp_path / "no_time.parquet"
    pl.DataFrame({"open": [1.0], "close": [1.1]}).write_parquet(bad)
    with pytest.raises(ReplayDataUnavailableError):
        _replay(bad)


def test_parquet_missing_ohlc_columns_fails_closed(tmp_path: Path) -> None:
    """time_utc present but the OHLC columns renamed -> rejected."""
    bad = tmp_path / "no_ohlc.parquet"
    pl.DataFrame({"time_utc": [_T0], "o": [1.0], "h": [1.1]}).write_parquet(bad)
    with pytest.raises(ReplayDataUnavailableError):
        _replay(bad)


def test_all_null_timestamps_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "null_ts.parquet"
    pl.DataFrame(
        {
            "time_utc": [None, None],
            "open": [1.0, 2.0],
            "high": [1.1, 2.1],
            "low": [0.9, 1.9],
            "close": [1.05, 2.05],
            "tick_volume": [1, 1],
            "spread": [1.0, 1.0],
        }
    ).write_parquet(bad)
    with pytest.raises(ReplayDataUnavailableError):
        _replay(bad)


def test_dataset_cache_error_path_is_unaffected() -> None:
    """The dataset-id path (preferred source) still routes to its own error."""
    with pytest.raises(ReplayDataUnavailableError):
        ReplayTickSource(symbol="XAUUSD", dataset_id="ds_does_not_exist")


# ---------------------------------------------------------------------------
# 6. Factory wiring (BUG-266 surface) — both formats through build_paper_adapter
# ---------------------------------------------------------------------------


def test_factory_replay_with_parquet_attaches_real_source(tmp_path: Path) -> None:
    pq = _write_parquet(tmp_path / "XAUUSD_M1.parquet", n=3)
    adapter = build_paper_adapter(
        symbol="XAUUSD", paper_data=PaperDataConfig(mode="REPLAY", raw_bars_path=str(pq))
    )
    assert isinstance(adapter, PaperMT5Adapter)
    assert adapter.market_data_mode == "REPLAY"
    assert adapter.replay_provenance["source"] == "RAW_BARS_PARQUET"
    assert adapter.replay_provenance["record_count"] == 3


def test_factory_replay_with_csv_still_attaches_real_source(tmp_path: Path) -> None:
    """Existing tests pass csv paths — CSV support MUST stay (contract step 2)."""
    csv = _write_csv(tmp_path / "XAUUSD_M1.csv", n=3)
    adapter = build_paper_adapter(
        symbol="XAUUSD", paper_data=PaperDataConfig(mode="REPLAY", raw_bars_path=str(csv))
    )
    assert adapter.market_data_mode == "REPLAY"
    assert adapter.replay_provenance["source"] == "RAW_BARS_CSV"
    assert adapter.replay_provenance["record_count"] == 3


def test_factory_replay_missing_source_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chdir isolation: the default fallback must not find the host's real
    data/raw while this test asserts the missing-source contract."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ReplayDataUnavailableError):
        build_paper_adapter(
            symbol="XAUUSD",
            paper_data=PaperDataConfig(
                mode="REPLAY", raw_bars_path=str(tmp_path / "absent.parquet")
            ),
        )


def test_factory_replay_missing_source_degrades_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """on_replay_unavailable="synthetic": boundary re-alignment must not brick.

    chdir isolation (see test_factory_replay_missing_source_fails_closed).
    """
    monkeypatch.chdir(tmp_path)
    adapter = build_paper_adapter(
        symbol="XAUUSD",
        paper_data=PaperDataConfig(mode="REPLAY", raw_bars_path=str(tmp_path / "absent.parquet")),
        on_replay_unavailable="synthetic",
    )
    assert adapter.market_data_mode == "SYNTHETIC"
    prov = adapter.replay_provenance
    assert prov["requested_mode"] == "REPLAY"
    assert "REPLAY_UNAVAILABLE" in str(prov.get("degraded_reason", ""))


def test_factory_replay_disabled_keeps_synthetic(tmp_path: Path) -> None:
    """SHADOW keeps the real-feed contract: replay is never attached there."""
    pq = _write_parquet(tmp_path / "XAUUSD_M1.parquet", n=3)
    adapter = build_paper_adapter(
        symbol="XAUUSD",
        paper_data=PaperDataConfig(mode="REPLAY", raw_bars_path=str(pq)),
        allow_replay=False,
    )
    assert adapter.market_data_mode == "SYNTHETIC"


# ---------------------------------------------------------------------------
# 7. Real-data honesty probe (environment-gated, never faked)
# ---------------------------------------------------------------------------


def test_real_export_when_present_is_loaded_as_parquet() -> None:
    """data/raw is gitignored operator data. Assert on the real parquet when
    present; skip loudly when not — never fake a pass and never fail a machine
    that simply has no export.

    This is the end-to-end point of DATA-RAW-01: the REPLAY raw-bars fallback
    now consumes ``nexus data-fetch``'s own output format (100k real M1 bars on
    this host), with no CSV anywhere in the path.
    """
    resolved = _resolve_raw_bars_path(RAW_M1_BARS_PATH)
    if resolved is None:
        pytest.skip(f"{RAW_M1_BARS_PATH} not exported on this host (gitignored operator data)")
    src = ReplayTickSource(symbol="XAUUSD", allow_raw_fallback=True)
    prov = src.identity()
    assert prov["market_data_mode"] == "REPLAY"
    assert prov["source"] == "RAW_BARS_PARQUET"
    assert prov["record_count"] > 0
    assert prov["start"] < prov["end"]
    t1, t2 = src.next_tick(), src.next_tick()
    assert t1 is not None and t2 is not None
    assert t2["timestamp"] > t1["timestamp"], "real data must replay in chronology"
    assert t2["ask"] > t2["bid"]
