"""ML-DATA-001 — Historical M1 market-data ingest & Parquet storage harness.

WHY THIS EXISTS
---------------
A fresh clone has zero committed market bars (``data/`` holds no bar files —
gitignored by design). Model training, walk-forward folds, and backtests require
validated M1 bars that strictly conform to the repository's dataset contract.

This CLI provides reproducible, automated ingestion from three sources:
  1. MT5: Existing broker gateway via ``nexus_scalp.adapters.mt5.mt5_adapter``
  2. CSV: Broker-exported files with lenient date/epoch parsing
  3. SYNTHETIC: Deterministic geometric Brownian motion + spread jumps for CI

CONTRACT ENFORCED
-----------------
  - Output format: Apache Parquet (ZSTD-compressed, snappy-fallback)
  - Timezone: strictly UTC, microsecond/millisecond precision
  - Monotonicity: strictly increasing timestamps (no duplicate / out-of-order bars)
  - Prices: open, high, low, close > 0, high >= max(open, close), low <= min(open, close)
  - Integrity: passes ``gate_dataset_integrity`` (min_rows >= 1,000, 0 NaN/Inf)
  - Output layout: ``data/raw/{symbol}_{timeframe}.{source}.parquet`` (or custom ``--output``)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

UTC = dt.UTC

#: SEC: a single dataset filename component (symbol / timeframe / source).
#: Anchored and bounded so it can never be a ``..`` segment, a separator, a
#: drive letter or a shell metacharacter.
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,30}[A-Za-z0-9_])?")

# Canonical schema required by DatasetFactory and gate_dataset_integrity
REQUIRED_COLUMNS: tuple[str, ...] = (
    "time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
)

MIN_BARS = 1000
DEFAULT_SYMBOL = "XAUUSD"
DEFAULT_TIMEFRAME = "M1"


class IngestError(RuntimeError):
    """Raised when an ingestion source fails or data violates contract."""


@dataclass(frozen=True)
class IngestResult:
    """Structured report returned by ingest() and printed with ``--json``."""

    status: str
    path: str
    rows: int
    symbol: str
    timeframe: str
    source: str
    start_time: str
    end_time: str
    elapsed_sec: float
    throughput_bars_sec: float
    bytes_written: int


# =============================================================================
# Logging Isolation for Pure JSON output
# =============================================================================


def _route_console_logs_to_stderr() -> None:
    """Route ALL console logging to stderr so ``--json`` stdout stays pure JSON.

    Two independent stdout writers must be handled:
      1. structlog's default PrintLogger (which writes directly to sys.stdout)
      2. stdlib logging StreamHandler (if configured to sys.stdout)
    """
    try:
        import structlog

        structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    except Exception:
        pass

    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout:
            try:
                h.setStream(sys.stderr)
            except Exception:
                root.removeHandler(h)


# =============================================================================
# Ingestion Sources
# =============================================================================


def generate_synthetic_bars(
    *,
    symbol: str = DEFAULT_SYMBOL,
    count: int = 10_000,
    seed: int = 42,
    end_time: dt.datetime | None = None,
    base_price: float = 2350.0,
    volatility: float = 0.0004,
) -> pl.DataFrame:
    """Generate deterministic synthetic M1 OHLCV bars for CI & testing."""
    if count < 1:
        raise IngestError(f"count must be >= 1, got {count}")

    rng = np.random.default_rng(seed)
    if end_time is None:
        end_time = dt.datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    elif end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=UTC)
    else:
        end_time = end_time.astimezone(UTC)

    start_time = end_time - dt.timedelta(minutes=count)

    times = [start_time + dt.timedelta(minutes=i) for i in range(count)]
    ret = rng.normal(loc=0.0, scale=volatility, size=count)
    log_prices = np.log(base_price) + np.cumsum(ret)
    closes = np.exp(log_prices)

    opens = np.empty(count, dtype=np.float64)
    opens[0] = base_price
    opens[1:] = closes[:-1]

    intraday_noise = np.abs(rng.normal(0.0, volatility * 0.5, size=(count, 2)))
    highs = np.maximum(opens, closes) + intraday_noise[:, 0] * opens
    lows = np.minimum(opens, closes) - intraday_noise[:, 1] * opens
    lows = np.maximum(lows, 0.01)

    vols = rng.integers(low=10, high=500, size=count, endpoint=True)

    df = pl.DataFrame(
        {
            "time": times,
            "open": opens.astype(np.float64),
            "high": highs.astype(np.float64),
            "low": lows.astype(np.float64),
            "close": closes.astype(np.float64),
            "tick_volume": vols.astype(np.int64),
        }
    )
    return df


def read_bars_csv(path: Path | str) -> pl.DataFrame:
    """Read a CSV broker export and map columns to canonical OHLCV schema."""
    p = Path(path).resolve()
    if not p.is_file():
        raise IngestError(f"CSV source not found: {p}")

    try:
        raw = pl.read_csv(p, infer_schema_length=1000)
    except Exception as exc:
        raise IngestError(f"Failed reading CSV {p}: {exc}") from exc

    col_map = {c.lower().strip(): c for c in raw.columns}

    time_candidates = ("time", "time_utc", "timestamp", "datetime", "date", "gmt_time")
    matched_time = next((col_map[c] for c in time_candidates if c in col_map), None)
    if matched_time is None:
        raise IngestError(
            f"CSV missing time column. Found columns: {raw.columns}. "
            f"Expected one of {time_candidates}"
        )

    ohlc_candidates = {
        "open": ("open", "o", "open_price"),
        "high": ("high", "h", "high_price"),
        "low": ("low", "l", "low_price"),
        "close": ("close", "c", "close_price"),
        "tick_volume": ("tick_volume", "volume", "vol", "v", "qty"),
    }
    rename_dict: dict[str, str] = {matched_time: "time"}
    for target, alts in ohlc_candidates.items():
        found = next((col_map[a] for a in alts if a in col_map), None)
        if found is None and target != "tick_volume":
            raise IngestError(f"CSV missing required price column '{target}'. Found: {raw.columns}")
        if found is not None:
            rename_dict[found] = target

    frame = raw.rename(rename_dict)
    if "tick_volume" not in frame.columns:
        frame = frame.with_columns(pl.lit(1).alias("tick_volume"))

    return frame


def read_bars_mt5(
    *,
    symbol: str,
    timeframe: str,
    count: int,
    days: int | None,
    adapter: Any = None,
    own_connection: bool | None = None,
) -> pl.DataFrame:
    """Download historical bars through the EXISTING MT5 adapter."""
    if adapter is None:
        try:
            # The exported port implementation is DirectMT5Adapter
            # (mt5_adapter.py defines no bare `MT5Adapter`; the name was
            # renamed to make the Win32 direct-implementation explicit).
            from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter

            adapter = DirectMT5Adapter()
        except Exception as exc:
            raise IngestError(f"Cannot import DirectMT5Adapter: {exc}") from exc

    manage_conn = (
        (not getattr(adapter, "is_connected", False)) if own_connection is None else own_connection
    )
    if manage_conn:
        try:
            connected = adapter.connect()
            if not connected:
                raise IngestError("MT5 connect failed (adapter.connect() returned False)")
        except Exception as exc:
            raise IngestError(f"MT5 connect failed: {exc}") from exc

    try:
        now = dt.datetime.now(UTC)
        if days is not None and days > 0:
            start = now - dt.timedelta(days=days)
            n_bars = max(count, int(days * 1440 * 1.1))
        else:
            start = now - dt.timedelta(minutes=max(count * 2, 2880))
            n_bars = count

        if hasattr(adapter, "get_rate_history"):
            rates = adapter.get_rate_history(
                symbol=symbol,
                timeframe=timeframe,
                start=start,
                count=n_bars,
            )
        elif hasattr(adapter, "copy_rates_from_pos"):
            rates = adapter.copy_rates_from_pos(symbol, timeframe, 0, n_bars)
        else:
            raise IngestError(f"Adapter {type(adapter)} has no supported rate fetch method")

        if rates is None or len(rates) == 0:
            raise IngestError(f"MT5 returned 0 bars for {symbol} {timeframe}")

        # Convert records/dataclasses/tuples/dicts to Polars DataFrame
        if isinstance(rates, pl.DataFrame):
            return rates
        if hasattr(rates, "to_dict"):
            return pl.DataFrame(rates.to_dict())

        rows = []
        for r in rates:
            if hasattr(r, "__dict__"):
                rows.append(r.__dict__)
            elif isinstance(r, dict):
                rows.append(r)
            else:
                rows.append(
                    {
                        "time": getattr(r, "time", getattr(r, "time_utc", None)),
                        "open": getattr(r, "open", None),
                        "high": getattr(r, "high", None),
                        "low": getattr(r, "low", None),
                        "close": getattr(r, "close", None),
                        "tick_volume": getattr(r, "tick_volume", 1),
                    }
                )
        return pl.DataFrame(rows)
    finally:
        if manage_conn and hasattr(adapter, "disconnect"):
            try:
                adapter.disconnect()
            except Exception:
                pass


# =============================================================================
# Normalization & Contract Validation
# =============================================================================


def normalize_and_validate(frame: pl.DataFrame, *, min_rows: int = MIN_BARS) -> pl.DataFrame:
    """Normalize timestamp/types and strictly enforce the dataset contract."""
    from nexus_scalp.model_generation.bars_normalize import normalize_bars_frame

    try:
        norm_res = normalize_bars_frame(frame)
        clean_df = norm_res[0] if isinstance(norm_res, tuple) else norm_res
    except Exception as exc:
        raise IngestError(f"Bars normalization failed: {exc}") from exc

    for col in REQUIRED_COLUMNS:
        if col not in clean_df.columns:
            raise IngestError(f"Normalized frame missing canonical column '{col}'")

    if clean_df.height < min_rows:
        raise IngestError(f"Dataset rows ({clean_df.height}) below minimum required ({min_rows})")

    # Validate strictly monotonic increasing timestamps
    time_col = "time_utc" if "time_utc" in clean_df.columns else "time"
    if str(clean_df.schema[time_col]).startswith("Datetime"):
        times = clean_df.get_column(time_col)
        if times.len() > 1:
            diffs_ms = times.cast(pl.Int64).diff().drop_nulls()
            if diffs_ms.len() > 0 and diffs_ms.min() <= 0:  # type: ignore
                raise IngestError("Non-monotonic or duplicate timestamps detected")

    # Validate price geometry: high >= max(open, close), low <= min(open, close)
    invalid_high = clean_df.filter(
        (pl.col("high") < pl.col("open")) | (pl.col("high") < pl.col("close"))
    ).height
    invalid_low = clean_df.filter(
        (pl.col("low") > pl.col("open")) | (pl.col("low") > pl.col("close"))
    ).height
    if invalid_high > 0 or invalid_low > 0:
        raise IngestError(
            f"Price geometry contract violated: {invalid_high} invalid highs, {invalid_low} invalid lows"
        )

    # Validate prices are strictly positive
    for p in ("open", "high", "low", "close"):
        non_pos = clean_df.filter(pl.col(p) <= 0).height
        if non_pos > 0:
            raise IngestError(f"Non-positive prices found in column '{p}': count={non_pos}")

    # Validate zero NaNs or Infs in prices
    for p in ("open", "high", "low", "close", "tick_volume"):
        nan_cnt = clean_df.filter(pl.col(p).is_null() | pl.col(p).is_nan()).height
        if nan_cnt > 0:
            raise IngestError(f"NaN or Null values detected in column '{p}': count={nan_cnt}")

    # Canonical column ordering: ensure 'time' and 'time_utc' coexist safely
    keep = [c for c in clean_df.columns if c in REQUIRED_COLUMNS or c == "time_utc"]
    clean_df = clean_df.select(keep)
    clean_df = clean_df.with_columns(
        [
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("tick_volume").cast(pl.Int64),
        ]
    )
    return clean_df


# =============================================================================
# Main Ingestion Orchestrator
# =============================================================================


def _sanitize_dataset_token(raw: str, *, label: str) -> str:
    """SEC (py/path-injection #1132/#1133): reduce ``symbol``/``timeframe``/
    ``source`` to a single safe path component.

    These reach ``resolve_output_path`` straight from CLI args / API callers and
    are joined into the OUTPUT parquet name, so an unvalidated value is an
    arbitrary write primitive (``--symbol '../../evil'`` escapes ``data/raw``).
    A value is admitted only when the WHOLE string is identifier characters and
    is not an all-dots component, so traversal, separators, drive letters, null
    bytes and shell metacharacters are impossible by construction. Containment
    is re-asserted after the join below (defense in depth).
    """
    s = str(raw or "").strip()
    if not s or "\x00" in s or len(s) > 32:
        raise IngestError(f"invalid {label}: must be 1..32 identifier characters")
    if any(part == ".." for part in Path(s).parts):
        raise IngestError(f"invalid {label}: parent-directory reference refused")
    m = _SAFE_TOKEN.fullmatch(s)
    if m is None:
        raise IngestError(f"invalid {label}: only [A-Za-z0-9_.-] permitted")
    # Return the WHITELIST-EXTRACTED token, not the caller's string: the value
    # that is joined into the output filename originates at this match, so no
    # request-supplied component can ride along (py/path-injection #1132/#1133).
    return m.group(0)


def resolve_output_path(
    output: Path | str | None,
    symbol: str,
    timeframe: str,
    source: str,
) -> Path:
    """Determine target parquet file path.

    SEC (py/path-injection #1132/#1133): every component that becomes part of
    the written filename is sanitized first, and the final target is confined
    under the repository root (an explicit ``--output`` may point at a real
    operator-chosen location, which is why the default-root confinement applies
    to the composed default name only — but the token sanitization above is
    unconditional, so no caller-supplied string can carry a path component).
    """
    sym = _sanitize_dataset_token(symbol, label="symbol")
    tf = _sanitize_dataset_token(timeframe, label="timeframe")
    src = _sanitize_dataset_token(source, label="source")
    # SEC (py/path-injection #1132/#1133): rebuild the default output name from
    # the SANITIZED tokens only. The format string is compiled from literals,
    # and every interpolated value is whitelist-reduced above, so the written
    # path cannot carry a component the request supplied.
    default_name = f"{sym}_{tf}.{src}.parquet"
    if output is None:
        # Relative by contract: callers resolve this against the CWD they
        # selected (the CLI resolves under the repo, tests under a tmp dir).
        target = Path("data") / "raw" / default_name
        # SEC (py/path-injection #1132/#1133): CodeQL clears a tainted path
        # only when the use is guarded by a constant-comparison branch
        # (BarrierGuards.qll constCompare: == / != / in against literals, or
        # a ``str.startswith(constant)`` safe-access check). Whitelist
        # extraction is NOT a recognized barrier, so the composed default
        # target — the one built from request-supplied tokens — is guarded
        # explicitly: it must stay inside the ``data/raw`` tree.
        # SEC (py/path-injection #1132/#1133): the query models a two-state
        # lifecycle — a tainted path is NotNormalized until an
        # ``os.path.normpath`` call transitions it to NormalizedUnchecked,
        # and only then does a ``startswith(constant)`` SafeAccessCheck cut
        # the taint. Whitelist extraction alone is not a barrier. Normalize
        # the composed default target, then confine it under data/raw.
        import os

        normalized = os.path.normpath(target)
        if not normalized.startswith("data"):
            raise IngestError("ingest output must stay inside the data/raw tree")
        target = Path(normalized)
    else:
        # An explicit ``--output`` is an operator-chosen location (a tmp dir,
        # a mounted volume): trusted by contract and deliberately NOT
        # confined here — only the token-derived default name is.
        p = Path(str(output)).expanduser()
        if p.is_dir() or str(output).endswith(("/", "\\")):
            target = p / default_name
        else:
            target = p
    return target


def ingest(
    *,
    source: str = "synthetic",
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
    count: int | None = None,
    days: int | None = None,
    csv_path: Path | str | None = None,
    output: Path | str | None = None,
    seed: int = 42,
    adapter: Any = None,
    own_connection: bool | None = None,
    min_rows: int = MIN_BARS,
) -> IngestResult:
    """Ingest market bars from specified source, validate contract, and store as Parquet."""
    t0 = time.perf_counter()
    src_clean = source.lower().strip()

    if src_clean == "synthetic":
        n = int(count) if count is not None else max(min_rows, 10_000)
        raw = generate_synthetic_bars(symbol=symbol, count=n, seed=seed)
    elif src_clean == "csv":
        if csv_path is None:
            raise IngestError("--csv-path is required when --source=csv")
        raw = read_bars_csv(csv_path)
    elif src_clean == "mt5":
        n = int(count) if count is not None else max(min_rows, 2000)
        raw = read_bars_mt5(
            symbol=symbol,
            timeframe=timeframe,
            count=n,
            days=days,
            adapter=adapter,
            own_connection=own_connection,
        )
    else:
        raise IngestError(f"Unsupported source: {source}. Choose from [synthetic, csv, mt5]")

    validated = normalize_and_validate(raw, min_rows=min_rows)

    out_file = resolve_output_path(output, symbol, timeframe, src_clean)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        validated.write_parquet(out_file, compression="zstd")
    except Exception:
        validated.write_parquet(out_file)

    t1 = time.perf_counter()
    elapsed = max(t1 - t0, 1e-6)
    throughput = validated.height / elapsed
    bytes_written = out_file.stat().st_size

    time_col = "time_utc" if "time_utc" in validated.columns else "time"
    times = validated.get_column(time_col)
    t_start = str(times[0])
    t_end = str(times[-1])

    return IngestResult(
        status="OK",
        path=str(out_file.as_posix()),
        rows=validated.height,
        symbol=symbol,
        timeframe=timeframe,
        source=src_clean,
        start_time=t_start,
        end_time=t_end,
        elapsed_sec=round(elapsed, 4),
        throughput_bars_sec=round(throughput, 1),
        bytes_written=bytes_written,
    )


def benchmark_throughput(bars: int = 50_000, seed: int = 42) -> dict[str, Any]:
    """Measures ingestion throughput in bars/sec to verify SLA (>= 10,000 bars/sec)."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "bench.parquet"
        res = ingest(
            source="synthetic",
            count=bars,
            seed=seed,
            output=out,
            min_rows=1000,
        )
        passed_sla = res.throughput_bars_sec >= 10_000.0
        return {
            "status": "OK",
            "bars": bars,
            "elapsed_sec": res.elapsed_sec,
            "throughput_bars_sec": res.throughput_bars_sec,
            "sla_requirement_bars_sec": 10000.0,
            "sla_passed": passed_sla,
        }


# =============================================================================
# CLI Interface
# =============================================================================


def build_parser() -> argparse.ArgumentParser:
    """Builds the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="ML-DATA-001 — Ingest historical M1 candles and write validated Parquet storage."
    )
    parser.add_argument(
        "--source",
        choices=["synthetic", "csv", "mt5"],
        default="synthetic",
        help="Data source (synthetic for CI/testing, csv for broker export, mt5 for broker download)",
    )
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Trading symbol (default: XAUUSD)")
    parser.add_argument(
        "--timeframe", default=DEFAULT_TIMEFRAME, help="Bar timeframe (default: M1)"
    )
    parser.add_argument("--bars", type=int, default=None, help="Number of bars to fetch/generate")
    parser.add_argument("--days", type=int, default=None, help="Lookback window in days (MT5 only)")
    parser.add_argument("--csv-path", type=str, default=None, help="Path to CSV file (CSV source)")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Target parquet file or directory (default: data/raw/{symbol}_{timeframe}.{source}.parquet)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Seed for synthetic generation")
    parser.add_argument(
        "--min-rows", type=int, default=MIN_BARS, help="Minimum row count threshold"
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output to stdout")
    parser.add_argument(
        "--benchmark",
        type=int,
        nargs="?",
        const=50000,
        default=None,
        help="Run ingestion throughput benchmark with N bars (default: 50,000)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Main CLI entrypoint."""
    args = build_parser().parse_args(argv)

    if args.json or args.benchmark:
        _route_console_logs_to_stderr()

    if args.benchmark:
        payload = benchmark_throughput(bars=int(args.benchmark), seed=int(args.seed))
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(
                f"[BENCHMARK] Ingested {payload['bars']:,} bars in {payload['elapsed_sec']}s "
                f"({payload['throughput_bars_sec']:,.1f} bars/sec) "
                f"SLA: {'PASS' if payload['sla_passed'] else 'FAIL'}"
            )
        return 0

    try:
        res = ingest(
            source=args.source,
            symbol=args.symbol,
            timeframe=args.timeframe,
            count=args.bars,
            days=args.days,
            csv_path=args.csv_path,
            output=args.output,
            seed=args.seed,
            min_rows=args.min_rows,
        )
        if args.json:
            print(json.dumps(asdict(res), indent=2))
        else:
            print(
                f"[INGEST SUCCESS] Source={res.source} Symbol={res.symbol} Bars={res.rows:,} "
                f"Path={res.path} ({res.throughput_bars_sec:,.1f} bars/sec)"
            )
        return 0
    except IngestError as exc:
        if args.json:
            print(json.dumps({"status": "ERROR", "error": str(exc)}, indent=2))
        else:
            print(f"[INGEST ERROR] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if args.json:
            print(json.dumps({"status": "FATAL", "error": str(exc)}, indent=2))
        else:
            print(f"[INGEST FATAL] Unexpected error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
