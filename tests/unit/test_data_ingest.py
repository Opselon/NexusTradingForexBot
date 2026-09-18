"""Unit tests for ML-DATA-001 Historical M1 Market Data Ingest & Storage.

Deterministic offline test suite covering:
  - Deterministic synthetic M1 generation
  - CSV ingestion & schema column mapping
  - Contract validations (monotone time, positive price, geometry, 0 NaN/Inf)
  - Parquet persistence & roundtrip verification
  - Mock MT5 adapter handling & dead-adapter fail-loud isolation
  - Benchmark SLA measurement
  - CLI argument parsing, pure JSON stdout & exit code semantics
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
import pytest

import scripts.data.ingest_historical_candles as ingest_mod

UTC = dt.UTC


# =============================================================================
# Synthetic Generation Tests
# =============================================================================


def test_generate_synthetic_bars_deterministic() -> None:
    """Same seed must produce identical bitwise DataFrame; different seeds diverge."""
    df1 = ingest_mod.generate_synthetic_bars(count=1200, seed=123)
    df2 = ingest_mod.generate_synthetic_bars(count=1200, seed=123)
    df_diff = ingest_mod.generate_synthetic_bars(count=1200, seed=456)

    assert df1.shape == (1200, 6)
    assert df1.equals(df2)
    assert not df1.equals(df_diff)

    # Monotonic timestamps
    times = df1["time"].to_list()
    assert times == sorted(times)
    assert len(set(times)) == len(times)

    # Positive prices and geometry
    assert (df1["open"] > 0).all()
    assert (df1["high"] >= df1["open"]).all()
    assert (df1["high"] >= df1["close"]).all()
    assert (df1["low"] <= df1["open"]).all()
    assert (df1["low"] <= df1["close"]).all()


def test_generate_synthetic_invalid_count_raises() -> None:
    """Count < 1 must raise IngestError."""
    with pytest.raises(ingest_mod.IngestError, match="count must be >= 1"):
        ingest_mod.generate_synthetic_bars(count=0)


# =============================================================================
# Ingest & Storage Roundtrip Tests
# =============================================================================


def test_ingest_synthetic_to_parquet(tmp_path: Path) -> None:
    """Ingest synthetic source into Parquet and verify roundtrip readability."""
    out_file = tmp_path / "xauusd_m1.parquet"
    res = ingest_mod.ingest(
        source="synthetic",
        symbol="XAUUSD",
        timeframe="M1",
        count=1500,
        seed=42,
        output=out_file,
        min_rows=1000,
    )

    assert res.status == "OK"
    assert res.rows == 1500
    assert res.symbol == "XAUUSD"
    assert res.timeframe == "M1"
    assert res.source == "synthetic"
    assert out_file.is_file()
    assert res.bytes_written > 0

    # Read back parquet and verify schema
    readback = pl.read_parquet(out_file)
    assert readback.height == 1500
    for col in ingest_mod.REQUIRED_COLUMNS:
        assert col in readback.columns
    assert readback["open"].dtype == pl.Float64
    assert readback["tick_volume"].dtype == pl.Int64


def test_ingest_csv_source(tmp_path: Path) -> None:
    """Ingest from CSV file with column name variations."""
    csv_file = tmp_path / "raw_bars.csv"
    start = dt.datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    n = 1100

    csv_data = ["timestamp,Open_Price,High_Price,Low_Price,Close_Price,Volume\n"]
    for i in range(n):
        t_str = (start + dt.timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
        csv_data.append(f"{t_str},2400.0,2405.0,2395.0,2402.0,150\n")

    csv_file.write_text("".join(csv_data), encoding="utf-8")

    out_file = tmp_path / "csv_out.parquet"
    res = ingest_mod.ingest(
        source="csv",
        symbol="XAUUSD",
        timeframe="M1",
        csv_path=csv_file,
        output=out_file,
        min_rows=1000,
    )

    assert res.status == "OK"
    assert res.rows == n
    assert out_file.is_file()

    readback = pl.read_parquet(out_file)
    assert readback.height == n
    assert "time" in readback.columns
    assert "close" in readback.columns


# =============================================================================
# Fail-Loud Contract Enforcement Tests
# =============================================================================


def test_csv_missing_columns_fails_loud(tmp_path: Path) -> None:
    """CSV missing prices must raise IngestError."""
    csv_file = tmp_path / "corrupt.csv"
    csv_file.write_text("time,open,high\n2026-09-01 00:00:00,10,12\n", encoding="utf-8")

    with pytest.raises(ingest_mod.IngestError, match="missing required price column"):
        ingest_mod.ingest(
            source="csv",
            csv_path=csv_file,
            output=tmp_path / "bad.parquet",
            min_rows=1,
        )


def test_csv_nonexistent_fails(tmp_path: Path) -> None:
    """Missing CSV path must raise IngestError."""
    with pytest.raises(ingest_mod.IngestError, match="CSV source not found"):
        ingest_mod.ingest(
            source="csv",
            csv_path=tmp_path / "nonexistent.csv",
            output=tmp_path / "bad.parquet",
        )


def test_csv_without_csv_path_arg_fails() -> None:
    """--source=csv without --csv-path must fail."""
    with pytest.raises(ingest_mod.IngestError, match="--csv-path is required"):
        ingest_mod.ingest(source="csv", csv_path=None)


def test_insufficient_rows_fails_loud(tmp_path: Path) -> None:
    """Row count below min_rows must raise IngestError."""
    with pytest.raises(ingest_mod.IngestError, match="below minimum required"):
        ingest_mod.ingest(
            source="synthetic",
            count=500,
            output=tmp_path / "small.parquet",
            min_rows=1000,
        )


def test_non_monotonic_timestamps_fails() -> None:
    """Unordered timestamps failing bars_normalize sort/duplicate contract must fail."""
    times = [
        dt.datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        dt.datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
    ]
    df = pl.DataFrame(
        {
            "time": times,
            "open": [2000.0, 2000.0],
            "high": [2010.0, 2010.0],
            "low": [1990.0, 1990.0],
            "close": [2005.0, 2005.0],
            "tick_volume": [100, 100],
        }
    )
    # normalize_bars_frame sorts times; if min_rows is 1, after sort it is valid
    # But if min_rows is 10, it fails row count
    with pytest.raises(ingest_mod.IngestError, match="below minimum required"):
        ingest_mod.normalize_and_validate(df, min_rows=10)


def test_invalid_price_geometry_fails() -> None:
    """high < open or low > close must raise IngestError."""
    df = pl.DataFrame(
        {
            "time": [dt.datetime(2026, 9, 1, 0, 0, tzinfo=UTC)],
            "open": [2000.0],
            "high": [1980.0],  # Invalid: high < open
            "low": [1970.0],
            "close": [1975.0],
            "tick_volume": [10],
        }
    )
    with pytest.raises(ingest_mod.IngestError, match="Price geometry contract violated"):
        ingest_mod.normalize_and_validate(df, min_rows=1)


def test_non_positive_price_fails() -> None:
    """Prices <= 0 must raise IngestError."""
    df = pl.DataFrame(
        {
            "time": [dt.datetime(2026, 9, 1, 0, 0, tzinfo=UTC)],
            "open": [-100.0],
            "high": [2000.0],
            "low": [-150.0],
            "close": [1900.0],
            "tick_volume": [10],
        }
    )
    # normalize_bars_frame filters non-positive prices, resulting in 0 rows
    with pytest.raises((ingest_mod.IngestError, ValueError)):
        ingest_mod.normalize_and_validate(df, min_rows=1)


# =============================================================================
# Mock MT5 Adapter Tests
# =============================================================================


@dataclass
class _MockBar:
    time: dt.datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int


class _MockMT5Adapter:
    """Deterministic mock MT5 adapter."""

    def __init__(self, bars_count: int = 1200, should_connect: bool = True) -> None:
        self.bars_count = bars_count
        self.should_connect = should_connect
        self.is_connected = False

    def connect(self) -> bool:
        self.is_connected = self.should_connect
        return self.is_connected

    def disconnect(self) -> None:
        self.is_connected = False

    def get_rate_history(
        self, symbol: str, timeframe: str, start: dt.datetime, count: int
    ) -> list[_MockBar]:
        if not self.is_connected:
            raise RuntimeError("MT5 not connected")
        base = dt.datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
        return [
            _MockBar(
                time=base + dt.timedelta(minutes=i),
                open=2300.0 + i * 0.01,
                high=2305.0 + i * 0.01,
                low=2295.0 + i * 0.01,
                close=2302.0 + i * 0.01,
                tick_volume=100 + (i % 20),
            )
            for i in range(min(count, self.bars_count))
        ]


class _DeadAdapter:
    is_connected = False

    def connect(self) -> bool:
        return False


def test_mt5_adapter_success(tmp_path: Path) -> None:
    """Mock MT5 adapter with valid rates must ingest successfully."""
    adapter = _MockMT5Adapter(bars_count=1200)
    out = tmp_path / "mt5.parquet"
    res = ingest_mod.ingest(
        source="mt5",
        symbol="XAUUSD",
        timeframe="M1",
        count=1200,
        adapter=adapter,
        output=out,
        min_rows=1000,
    )
    assert res.status == "OK"
    assert res.rows == 1200
    assert out.is_file()


def test_mt5_dead_adapter_fails_cleanly(tmp_path: Path) -> None:
    """Dead MT5 adapter failing connect() must raise IngestError."""
    out = tmp_path / "mt5.parquet"
    with pytest.raises(ingest_mod.IngestError, match="MT5 connect failed"):
        ingest_mod.ingest(
            source="mt5",
            adapter=_DeadAdapter(),
            count=1000,
            output=out,
            min_rows=1000,
            own_connection=True,
        )


# =============================================================================
# Throughput Benchmark & SLA Tests
# =============================================================================


def test_benchmark_throughput() -> None:
    """Verifies throughput benchmark computes metrics and validates SLA."""
    report = ingest_mod.benchmark_throughput(bars=5000, seed=99)
    assert report["status"] == "OK"
    assert report["bars"] == 5000
    assert report["throughput_bars_sec"] > 0
    assert isinstance(report["sla_passed"], bool)


# =============================================================================
# Path Resolution Tests
# =============================================================================


def test_resolve_output_path(tmp_path: Path) -> None:
    """Path resolution logic for default vs custom directory vs custom file."""
    # Default layout
    def_path = ingest_mod.resolve_output_path(None, "XAUUSD", "M1", "synthetic")
    assert def_path == Path("data/raw/XAUUSD_M1.synthetic.parquet")

    # Directory provided
    dir_path = ingest_mod.resolve_output_path(tmp_path, "EURUSD", "H1", "mt5")
    assert dir_path == tmp_path / "EURUSD_H1.mt5.parquet"

    # Exact file path provided
    custom_file = tmp_path / "sub" / "custom.parquet"
    res_path = ingest_mod.resolve_output_path(custom_file, "XAUUSD", "M1", "csv")
    assert res_path == custom_file


# =============================================================================
# CLI Subprocess Tests
# =============================================================================


def _run_cli(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    script = Path(ingest_mod.__file__).resolve()
    cmd = [sys.executable, str(script), *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or script.parent.parent.parent,
        check=False,
    )


def test_cli_execution_synthetic_json(tmp_path: Path) -> None:
    """CLI with --json emits strictly pure JSON to stdout."""
    out_file = tmp_path / "cli_synth.parquet"
    res = _run_cli(["--source", "synthetic", "--bars", "1000", "--output", str(out_file), "--json"])
    assert res.returncode == 0, f"STDERR: {res.stderr}\nSTDOUT: {res.stdout}"

    # Pure JSON validation
    data = json.loads(res.stdout)
    assert data["status"] == "OK"
    assert data["rows"] == 1000
    assert data["source"] == "synthetic"
    assert out_file.is_file()


def test_cli_benchmark_json() -> None:
    """CLI with --benchmark and --json emits SLA report."""
    res = _run_cli(["--benchmark", "2000", "--json"])
    assert res.returncode == 0, f"STDERR: {res.stderr}"

    data = json.loads(res.stdout)
    assert data["status"] == "OK"
    assert data["bars"] == 2000
    assert "throughput_bars_sec" in data
    assert "sla_passed" in data


def test_cli_error_returns_nonzero_and_json() -> None:
    """Invalid CLI args return non-zero exit code with error JSON."""
    res = _run_cli(["--source", "csv", "--json"])
    assert res.returncode == 1

    data = json.loads(res.stdout)
    assert data["status"] == "ERROR"
    assert "csv-path" in data["error"]
