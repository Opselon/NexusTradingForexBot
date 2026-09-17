"""Explicit, read-only acquisition of user-owned XAUUSD M1 history.

CSV needs only the standard library (including in a packaged EXE); Parquet
uses the app's lazy Polars dependency. Output is canonical CSV for the existing
pipeline normalizer. Never generates candles, fills gaps, or sends orders.
"""

from __future__ import annotations

import contextlib
import csv
import math
import tempfile
from collections.abc import Callable, Generator, Iterable
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from nexus_scalp.model_provisioning.pipeline import ProgressEvent, TrainingCancelledError

MIN_CANDLES = 3000
MAX_BROKER_CANDLES = 100_000  # Existing DirectMT5Adapter provider limit.
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_FILE_ROWS = 2_000_000
_FIELDS = ("time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume")


class DatasetSourceError(ValueError):
    """Unusable input or unavailable real history; no substitute is generated."""


def validate_dataset_request(
    *, source: str, source_file: Path | None, symbol: str, timeframe: str, candles: int | None
) -> None:
    """Cheap request validation; never imports ML, creates files or uses a broker."""
    if source not in ("file", "broker"):
        raise DatasetSourceError("source must be file or broker; no automatic fallback")
    if symbol != "XAUUSD" or timeframe != "M1":
        raise DatasetSourceError("first-setup training supports XAUUSD M1 only")
    if source == "broker" and source_file is not None:
        raise DatasetSourceError("choose file or broker, not both")
    if candles is not None and (type(candles) is not int or candles < MIN_CANDLES):
        raise DatasetSourceError("candles must be an integer >= 3000, or None for all file rows")
    if source == "broker" and (candles is None or candles > MAX_BROKER_CANDLES):
        raise DatasetSourceError("broker candles must be explicitly selected: 3000..100000")
    if source == "file":
        if source_file is None or not Path(source_file).is_file():
            raise DatasetSourceError("input file missing; select an existing CSV or Parquet export")
        if Path(source_file).suffix.lower() not in (".csv", ".txt", ".parquet"):
            raise DatasetSourceError("input file must be CSV, TXT (delimited MT5), or Parquet")
        if not 0 < Path(source_file).stat().st_size <= MAX_FILE_BYTES:
            raise DatasetSourceError("input file empty or exceeds 512 MiB; export a smaller slice")


@contextlib.contextmanager
def training_history_connection(adapter: Any = None) -> Generator[Any, None, None]:
    """Borrow an existing provider, or own a first-setup MT5 IPC connection.

    Only use the owned path when no engine/provider is active in this process:
    native MT5 shutdown is process-global. The caller must pass its active
    adapter when one exists. Never creates an engine or sends an order.
    """
    if adapter is not None:
        yield adapter
        return
    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter

    owned: DirectMT5Adapter = DirectMT5Adapter()
    try:
        if not owned.connect():
            raise DatasetSourceError(
                "MT5 connection unavailable; log in to the local terminal or import a file"
            )
        yield owned
    finally:
        with contextlib.suppress(Exception):
            owned.disconnect()


def _timestamp(value: Any) -> int:
    try:
        if isinstance(value, datetime):
            parsed = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            return int(parsed.timestamp())
        text = str(value).strip()
        try:
            epoch = float(text)
        except ValueError:
            for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
                try:
                    return int(datetime.strptime(text, fmt).replace(tzinfo=UTC).timestamp())
                except ValueError:
                    pass
            return _timestamp(datetime.fromisoformat(text.replace("Z", "+00:00")))
        if epoch >= 1e15:
            epoch /= 1e6
        elif epoch >= 1e12:
            epoch /= 1e3
        if not math.isfinite(epoch) or epoch <= 0 or epoch != int(epoch):
            raise ValueError
        return int(epoch)
    except (ValueError, OverflowError, TypeError, OSError) as exc:
        raise DatasetSourceError("invalid timestamp; use UTC epoch or ISO-8601") from exc


def _file_rows(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix.lower() == ".parquet":
        try:
            import polars as pl
        except ImportError as exc:
            raise DatasetSourceError(
                "Parquet reader unavailable in this app; export CSV or repair the application"
            ) from exc
        yield from pl.read_parquet(path).iter_rows(named=True)
        return
    with path.open(newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        yield from reader


def _normalize_row(raw: dict[str, Any]) -> dict[str, Any]:
    # MT5 terminal <DATE>/<TIME>/<TICKVOL>, generic OHLCV, and API-export names.
    row = {str(key).strip().strip("<>").lower(): value for key, value in raw.items()}
    if "date" in row and "time" in row and ":" in str(row["time"]):
        when = f"{row['date']} {row['time']}"
    else:
        when = next(
            (
                row[k]
                for k in ("time_utc", "timestamp", "datetime", "date", "time")
                if row.get(k) not in (None, "")
            ),
            None,
        )
    out: dict[str, Any] = {"time": _timestamp(when)}
    for key in ("open", "high", "low", "close"):
        try:
            val = float(row[key])
            if not math.isfinite(val) or val <= 0:
                raise ValueError
        except (KeyError, ValueError, TypeError) as exc:
            raise DatasetSourceError(f"{key} must be a finite positive OHLC price") from exc
        out[key] = val
    if out["high"] < max(out["open"], out["low"], out["close"]) or out["low"] > min(
        out["open"], out["close"]
    ):
        raise DatasetSourceError("invalid OHLC high/low geometry")
    for key, aliases in (
        ("tick_volume", ("tick_volume", "tickvol", "volume")),
        ("spread", ("spread",)),
        ("real_volume", ("real_volume", "vol")),
    ):
        value = next((row[k] for k in aliases if row.get(k) not in (None, "")), None)
        if value is None:
            if key == "tick_volume":
                raise DatasetSourceError("tick_volume is required; volume is never fabricated")
            continue  # Optional data stays absent, never manufactured as zero.
        try:
            val = float(value)
            if not math.isfinite(val) or val < 0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise DatasetSourceError(f"{key} must be finite and non-negative") from exc
        out[key] = val
    if row.get("source") not in (None, "", "BROKER_NATIVE", "MT5", "USER_FILE"):
        raise DatasetSourceError("dataset declares non-broker/synthetic provenance")
    if row.get("symbol") not in (None, "", "XAUUSD") or row.get("timeframe") not in (
        None,
        "",
        "M1",
    ):
        raise DatasetSourceError("dataset metadata must match XAUUSD M1")
    return out


def prepare_training_dataset(
    *,
    source: str = "file",
    source_file: Path | None = None,
    adapter: Any = None,
    symbol: str = "XAUUSD",
    timeframe: str = "M1",
    candles: int | None = None,
    output_dir: Path | None = None,
    progress: Callable[[ProgressEvent], None] | None = None,
    cancel_event: Any = None,
) -> Path:
    """Return a unique validated CSV; preserve the input and all serving models.

    A supplied connected adapter is borrowed only for get_rate_history; never
    connected/disconnected here. BROKER_NATIVE provenance is mandatory. No
    engine-memory/paper fallback. Call this synchronous API in a background job.
    Cancellation is cooperative before/after the provider call and per 1000 rows;
    the existing provider's blocking call cannot be interrupted by this function.
    """

    def emit(status: str, message: str = "", **metrics: Any) -> None:
        if progress:
            with contextlib.suppress(Exception):
                progress(
                    ProgressEvent(stage="dataset", status=status, message=message, metrics=metrics)
                )

    def check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise TrainingCancelledError("dataset preparation cancelled; no dataset published")

    result: Path | None = None
    try:
        check_cancel()
        validate_dataset_request(
            source=source,
            source_file=source_file,
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
        )
        if source == "broker" and adapter is None:
            # Explicit source selection is the authorization. This first-setup
            # connection exists only for acquisition and is closed on all exits.
            with training_history_connection() as owned:
                return prepare_training_dataset(
                    source=source,
                    adapter=owned,
                    symbol=symbol,
                    timeframe=timeframe,
                    candles=candles,
                    output_dir=output_dir,
                    progress=progress,
                    cancel_event=cancel_event,
                )
        emit("start", f"Reading {source} XAUUSD M1 history", requested_rows=candles, source=source)
        if source == "broker":
            provider = getattr(adapter, "get_rate_history", None)
            if not callable(provider):
                raise DatasetSourceError(
                    "connected broker history provider unavailable; connect MT5 or import a file"
                )
            assert candles is not None
            # Remote's existing contract exposes completed BarData through
            # GET_HISTORICAL_BARS; get_rate_history is only an empty port default.
            # Select by concrete adapter type BEFORE acquisition, never fallback
            # from a failed native provider to unproven engine/paper bars.
            from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter

            if isinstance(adapter, RemoteMT5GatewayAdapter):
                bars = adapter.get_historical_bars(
                    symbol=symbol, timeframe=timeframe, count=min(candles + 1, MAX_BROKER_CANDLES)
                )
                raw_rows: Iterable[dict[str, Any]] = (
                    {
                        "time_utc": bar.timestamp,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "tick_volume": bar.tick_volume,
                        "symbol": bar.symbol,
                        "timeframe": bar.timeframe,
                    }
                    for bar in bars
                    if bar.is_complete
                )
            else:
                bars = provider(
                    symbol=symbol, timeframe=timeframe, count=min(candles + 1, MAX_BROKER_CANDLES)
                )
                for bar in bars or []:
                    if getattr(bar, "source", None) != "BROKER_NATIVE" or not getattr(
                        bar, "available", False
                    ):
                        raise DatasetSourceError(
                            "history must have available BROKER_NATIVE provenance; paper/synthetic data refused"
                        )
                raw_rows = (
                    {key: getattr(bar, key, None) for key in (*_FIELDS, "time_utc")}
                    for bar in bars or []
                )
            check_cancel()
            if not bars:
                raise DatasetSourceError(
                    "broker returned no history; load XAUUSD M1 history in MT5 or import a file"
                )
        else:
            assert source_file is not None
            raw_rows = _file_rows(Path(source_file))
        unique: dict[int, dict[str, Any]] = {}
        total = duplicates = incomplete = 0
        closed_before = int(datetime.now(UTC).timestamp()) // 60 * 60
        for total, raw in enumerate(raw_rows, 1):
            if total > MAX_FILE_ROWS:
                raise DatasetSourceError("dataset exceeds 2000000 rows; export a smaller slice")
            if total % 1000 == 0:
                check_cancel()
                emit("progress", "Validating actual candles", received_rows=total)
            row = _normalize_row(raw)
            ts = row["time"]
            if ts >= closed_before:
                incomplete += 1
                continue
            if ts % 60:
                raise DatasetSourceError("M1 timestamps must align to a minute boundary")
            if ts in unique:
                if unique[ts] != row:
                    raise DatasetSourceError(
                        "conflicting duplicate timestamp; correct the export before training"
                    )
                duplicates += 1
            unique[ts] = row
        check_cancel()
        rows = [unique[key] for key in sorted(unique)]
        if len(rows) < (candles or MIN_CANDLES):
            raise DatasetSourceError(
                f"only {len(rows)} valid closed candles; need {candles or MIN_CANDLES} (minimum 3000); request less or export more history"
            )
        if candles is not None:
            rows = rows[-candles:]
        # Require actual M1 evidence; do not interpret M5/H1 bars as missing M1.
        diffs = [b["time"] - a["time"] for a, b in pairwise(rows)]
        if not any(diff == 60 for diff in diffs):
            raise DatasetSourceError("no M1 cadence detected; use XAUUSD M1 history")
        if output_dir is None:
            from nexus_scalp.release.paths import get_runtime_workspace

            output_dir = get_runtime_workspace() / "data" / "training"
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8",
            suffix=".csv",
            prefix="training-",
            dir=output_dir,
            delete=False,
        ) as handle:
            result = Path(handle.name)
            fields = [key for key in _FIELDS if any(key in row for row in rows)]
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        check_cancel()
        emit(
            "done",
            "Validated dataset ready for canonical training",
            source=source,
            received_rows=total,
            selected_rows=len(rows),
            dropped_duplicates=duplicates,
            excluded_unclosed=incomplete,
            missing_bar_gaps=sum(d > 60 for d in diffs),
            first_timestamp=rows[0]["time"],
            last_timestamp=rows[-1]["time"],
            path=str(result),
        )
        return result
    except TrainingCancelledError:
        if result is not None:
            result.unlink(missing_ok=True)
        emit("cancelled", "Dataset preparation cancelled")
        raise
    except Exception as exc:
        if result is not None:
            result.unlink(missing_ok=True)
        emit("failed", str(exc)[:300])
        if isinstance(exc, DatasetSourceError):
            raise
        raise DatasetSourceError(
            f"dataset acquisition failed ({type(exc).__name__}); check the export or broker connection"
        ) from exc
