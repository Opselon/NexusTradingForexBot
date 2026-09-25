"""Paper adapter — REPLAY mode (P0 phase 4: paper data integrity).

The Paper adapter has always had a SYNTHETIC mode (AR(1) random walk with
seeded determinism for CI). This module adds the OTHER half of the mission
contract: a REPLAY mode where the paper broker's tick stream is the REAL
historical chronology (dataset cache from research.mt5_tick_dataset, or the
committed data/raw M1 bars), so paper-derived experience carries real-market
statistics.

Integrity contract (mission 5C):

* The adapter's data-source mode is EXPLICIT and observable:
  ``market_data_mode`` in {"SYNTHETIC", "REPLAY"}.
* REPLAY mode is fail-closed: constructing/clocking it with a missing or
  corrupt dataset raises (never falls back silently to synthetic ticks).
* Replay preserves the historical chronology (the source is already
  chronologically ordered + validated by event_source) and direction-aware
  pricing (bid/ask come from the historical record; bar records synthesize
  the dataset-builder convention spread).
* Every replayed tick carries its HISTORICAL timestamp — wall-clock is never
  substituted, so experience rows keep the true decision timeline.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.adapters.paper.replay_source")

#: Dataset-builder convention: synthetic spread (USD) used for BAR replay
#: records (mirrors research.streaming_replay.BAR_MODE_SYNTHETIC_SPREAD_USD).
BAR_REPLAY_SYNTHETIC_SPREAD_USD: float = 0.20

#: Committed raw-bar fallback (repo-relative). These are REAL broker-exported
#: bars; timestamps are the true historical timeline. The parquet is the
#: CANONICAL format: it is exactly what ``nexus data-fetch`` writes
#: (``data/raw/<SYM>_<TF>.parquet``), so the producer and the REPLAY fallback
#: finally share one contract. A legacy ``.csv`` export of the same schema is
#: still accepted for back-compat (see :func:`_resolve_raw_bars_path`).
RAW_M1_BARS_PATH: str = "data/raw/XAUUSD_M1.parquet"

#: src/nexus_scalp/adapters/paper/replay_source.py -> repo root
#: (src/nexus_scalp/adapters/paper/ = 5 components below root).
_REPO_ROOT = Path(__file__).resolve().parents[4]

#: Extensions accepted for the raw-bars fallback. Parquet is canonical (what
#: ``nexus data-fetch`` writes); CSV is the legacy operator export.
_RAW_BARS_SUFFIXES = frozenset({".parquet", ".csv"})


def _as_naive_utc_us(frame: Any, name: str = "time_utc") -> Any:
    """Normalize ``frame[name]`` to a naive-UTC microsecond datetime column.

    ``nexus data-fetch`` writes naive-UTC ``datetime[us]``. Other exports may
    store tz-aware datetimes, epoch ints, or ISO strings; the replay timeline
    is one shape regardless. Unparseable values become null and are dropped by
    the caller (fail-closed when nothing parseable remains).
    """
    import polars as pl

    dtype = frame.schema[name]
    if isinstance(dtype, pl.Datetime):
        if dtype.time_zone is None:
            return frame.with_columns(pl.col(name).cast(pl.Datetime("us")))
        return frame.with_columns(
            pl.col(name)
            .cast(pl.Datetime("us", "UTC"))
            .dt.convert_time_zone("UTC")
            .dt.replace_time_zone(None)
        )
    if isinstance(
        dtype, (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.Float64)
    ):
        # Epoch stored numerically. The unit is inferred from the magnitude:
        # data-fetch's own ``time`` column is epoch SECONDS, but a column of
        # epoch microseconds is also loadable. An Int64 of us magnitude
        # interpreted as seconds overflows past year 2262, so rescale us->s
        # when the values are clearly too large for seconds. The otherwise()
        # branch is MANDATORY: without it pl.when yields null on the false
        # branch (not the original value).
        raw = pl.col(name).cast(pl.Int64)
        secs = pl.when(raw > 4_000_000_000).then((raw // 1_000_000).cast(pl.Int64)).otherwise(raw)
        return frame.with_columns(pl.from_epoch(secs, time_unit="s").alias(name))
    if isinstance(dtype, pl.String):
        # ISO-8601 text (the CSV export's native form — naive UTC). str.strptime
        # with strict=False yields null for unparseable text (which the caller
        # drops) instead of raising "no appropriate format". The trailing %.f
        # captures the CSV export's fractional seconds; a plain-space separator
        # is accepted via the coalesced alternative.
        return frame.with_columns(
            pl.coalesce(
                pl.col(name).str.strptime(
                    pl.Datetime("us"), format="%Y-%m-%dT%H:%M:%S%.f", strict=False
                ),
                pl.col(name).str.strptime(
                    pl.Datetime("us"), format="%Y-%m-%d %H:%M:%S%.f", strict=False
                ),
            ).alias(name)
        )
    return frame.with_columns(pl.col(name).cast(pl.Datetime("us"), strict=False))


class ReplayDataUnavailableError(RuntimeError):
    """Raised when REPLAY mode is requested but no historical source exists.

    FAIL-CLOSED: callers must NOT silently fall back to synthetic ticks —
    a synthetic learning cycle must never masquerade as real-market evidence
    (mission phase 5).
    """


class ReplayTickSource:
    """Causal, chronological historical tick/bar source for Paper REPLAY.

    Prefer a dataset-cache id (research.mt5_tick_dataset — fingerprint-
    verified ticks/bars with full provenance). Falls back to the committed
    data/raw M1 CSV when explicitly allowed (allow_raw_fallback=True) AND
    the file exists. Missing sources raise ReplayDataUnavailableError.
    """

    def __init__(
        self,
        symbol: str = "XAUUSD",
        dataset_id: str = "",
        allow_raw_fallback: bool = False,
        raw_bars_path: str = RAW_M1_BARS_PATH,
    ) -> None:
        self.symbol = symbol
        self.dataset_id = dataset_id
        self.source_mode = "REPLAY"
        self._records: list[dict[str, Any]] = []
        self._cursor = 0
        self._identity: dict[str, Any] = {}
        self._load(dataset_id, allow_raw_fallback, raw_bars_path)

    # ------------------------------------------------------------------
    # Loading (fail-closed)
    # ------------------------------------------------------------------

    def _load(self, dataset_id: str, allow_raw_fallback: bool, raw_path: str) -> None:
        if dataset_id:
            self._load_from_dataset_cache(dataset_id)
            return
        if allow_raw_fallback:
            resolved = _resolve_raw_bars_path(raw_path)
            if resolved is not None:
                self._load_from_raw_bars(resolved)
                return
        raise ReplayDataUnavailableError(
            "Paper REPLAY mode requested but no historical source is available "
            f"(dataset_id={dataset_id or 'NOT_SPECIFIED'}, raw fallback "
            f"allowed={allow_raw_fallback}, path={raw_path}). Construct the "
            "SYNTHETIC paper adapter explicitly instead — never fall back "
            "silently."
        )

    def _load_from_dataset_cache(self, ds_id: str) -> None:
        from nexus_scalp.research.mt5_tick_dataset import DatasetCorruptionError

        try:
            from nexus_scalp.research.mt5_tick_dataset import MT5TickDataset

            cache = MT5TickDataset()
            meta = cache.meta(ds_id) or {}
            self._records = cache.load(ds_id)
            self._identity = {
                "source": "DATASET_CACHE",
                "dataset_id": ds_id,
                "dataset_kind": str(meta.get("kind", "ticks")),
                "dataset_fingerprint": str(meta.get("fingerprint", "")),
                "record_count": len(self._records),
                "start": str(meta.get("start", "")),
                "end": str(meta.get("end", "")),
            }
        except DatasetCorruptionError:
            raise  # corrupt data is DETECTED and REJECTED, never served
        except Exception as exc:
            raise ReplayDataUnavailableError(
                f"dataset cache load failed for {ds_id}: {exc}"
            ) from exc

    def _load_from_raw_bars(self, path: Path) -> None:
        """Loads the committed real M1 bar export (chronological, UTC).

        Dispatches on the resolved extension: parquet (the canonical format
        written by ``nexus data-fetch``) is preferred; a legacy ``.csv`` export
        of the identical column schema is still honored for operators who keep
        one. Both paths produce the same record shape and the same identity
        block (only the ``source`` tag differs).
        """
        if path.suffix.lower() == ".csv":
            self._load_from_raw_bars_csv(path)
            return
        self._load_from_raw_bars_parquet(path)

    def _load_from_raw_bars_parquet(self, path: Path) -> None:
        """Loads the canonical parquet written by ``nexus data-fetch``.

        Columns are identical to the CSV export (time, open, high, low, close,
        tick_volume, spread, real_volume, time_utc); ``time_utc`` is the
        chronological authority. Keeps the same fail-closed contract as the CSV
        path: missing/corrupt/unreadable raises ReplayDataUnavailableError.
        """
        import polars as pl

        try:
            frame = pl.read_parquet(path)
        except FileNotFoundError as exc:
            raise ReplayDataUnavailableError(f"raw bars file {path} not found") from exc
        except Exception as exc:  # corrupt / truncated / unreadable parquet
            raise ReplayDataUnavailableError(
                f"raw bars parquet {path} could not be read: {exc}"
            ) from exc
        if frame.is_empty() or "time_utc" not in frame.columns:
            raise ReplayDataUnavailableError(
                f"raw bars parquet {path} yielded no usable rows "
                f"(rows={frame.height}, has_time_utc={'time_utc' in frame.columns})"
            )
        try:
            selected = frame.select(
                [
                    pl.col("time_utc"),
                    pl.col("open").cast(pl.Float64),
                    pl.col("high").cast(pl.Float64),
                    pl.col("low").cast(pl.Float64),
                    pl.col("close").cast(pl.Float64),
                    pl.col("tick_volume").cast(pl.Int64),
                    pl.col("spread").cast(pl.Float64),
                ]
            )
        except Exception as exc:  # missing/renamed required column
            raise ReplayDataUnavailableError(
                f"raw bars parquet {path} is missing required OHLC columns: {exc}"
            ) from exc
        # Normalize the chronological authority to naive UTC microseconds:
        # data-fetch writes naive UTC, other exports may carry tz info or epoch
        # ints. drop_nulls + unique + sort enforce strict chronology so the
        # replay cursor is a causal, duplicate-free timeline.
        selected = (
            _as_naive_utc_us(selected)
            .drop_nulls("time_utc")
            .unique(subset=["time_utc"], keep="first", maintain_order=True)
            .sort("time_utc")
        )
        if selected.is_empty():
            raise ReplayDataUnavailableError(
                f"raw bars parquet {path} yielded zero rows after time parsing"
            )
        records: list[dict[str, Any]] = [
            {
                "timestamp": row["time_utc"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "tick_volume": int(row["tick_volume"]),
                "spread": float(row["spread"]),
            }
            for row in selected.iter_rows(named=True)
        ]
        self._records = records
        self._identity = {
            "source": "RAW_BARS_PARQUET",
            "path": str(path),
            "record_count": len(records),
            "start": records[0]["timestamp"].isoformat(),
            "end": records[-1]["timestamp"].isoformat(),
        }

    def _load_from_raw_bars_csv(self, path: str | Path) -> None:
        """Loads a legacy real M1 bar CSV export (chronological, UTC)."""
        records: list[dict[str, Any]] = []
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                # column names per the export: time,open,high,low,close,
                # tick_volume,spread,real_volume,time_utc
                try:
                    ts = datetime.fromisoformat(str(row.get("time_utc", "")).strip())
                except (TypeError, ValueError):
                    continue
                records.append(
                    {
                        "timestamp": ts,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "tick_volume": int(float(row.get("tick_volume") or 0)),
                        "spread": float(row.get("spread") or 0.0),
                    }
                )
        if not records:
            raise ReplayDataUnavailableError(f"raw bars file {path} yielded zero rows")
        records.sort(key=lambda r: r["timestamp"])  # strict chronology
        self._records = records
        self._identity = {
            "source": "RAW_BARS_CSV",
            "path": str(path),
            "record_count": len(records),
            "start": records[0]["timestamp"].isoformat(),
            "end": records[-1]["timestamp"].isoformat(),
        }

    # ------------------------------------------------------------------
    # Identity / provenance
    # ------------------------------------------------------------------

    def identity(self) -> dict[str, Any]:
        """Provenance block recorded with every paper run (mission 5C):
        source mode, dataset version, timestamp range, spread model."""
        return {
            "market_data_mode": self.source_mode,
            "symbol": self.symbol,
            **self._identity,
            "spread_model": (
                "RECORDED_BAR_SPREAD"
                if self._identity.get("dataset_kind", "").startswith("bars")
                else "RECORDED_TICK_BID_ASK"
            )
            if self._identity.get("source") == "DATASET_CACHE"
            else "SYNTHETIC_BAR_CLOSE_SPREAD_0.20USD",
            "slippage_model": "PAPER_ADAPTER_DETERMINISTIC",
        }

    # ------------------------------------------------------------------
    # Causal iteration
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return max(0, len(self._records) - self._cursor)

    @property
    def exhausted(self) -> bool:
        return self._cursor >= len(self._records)

    def next_tick(self) -> dict[str, Any] | None:
        """Returns the NEXT historical record as a bid/ask quote dict, or None
        at end-of-data. Chronology is the file's own order (validated on
        load); direction-aware pricing: bid/ask are the RECORDED prices for
        ticks, or close/close+synthetic-spread for bar records (the dataset
        builder convention shared with StreamingReplayEngine bar mode).
        """
        if self.exhausted:
            return None
        rec = self._records[self._cursor]
        self._cursor += 1
        if "bid" in rec and "ask" in rec:
            return {
                "timestamp": rec["timestamp"],
                "bid": float(rec["bid"]),
                "ask": float(rec["ask"]),
                "volume": float(rec.get("volume", 0.0) or 0.0),
            }
        # bar record
        close = float(rec["close"])
        spread = float(rec.get("spread") or 0.0)
        ask = close + (spread if spread > 0 else BAR_REPLAY_SYNTHETIC_SPREAD_USD)
        return {
            "timestamp": rec["timestamp"],
            "bid": close,
            "ask": round(ask, 5),
            "volume": float(rec.get("tick_volume", 0) or 0),
        }

    def history_bars(self, timeframe: str, count: int) -> list[dict[str, Any]]:
        """The UP-TO-CURSOR historical bar window (causal: only the past)."""
        if "high" not in (self._records[0] if self._records else {}):
            return []
        upto = self._records[: self._cursor]
        return [
            {
                "timestamp": r["timestamp"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "tick_volume": r.get("tick_volume", 0),
            }
            for r in upto[-int(count) :]
        ]


def _resolve_raw_bars_path(configured: str | None = None) -> Path | None:
    """Resolve the configured raw-bars path to a file that exists.

    Resolution order (DATA-RAW-01 contract):

    1. The configured path AS-GIVEN when it exists — an explicitly configured
       ABSOLUTE path is always preferred and is never rewritten (an operator
       who points at their own export gets exactly that file, CSV or parquet).
    2. A RELATIVE configured path that is not found relative to CWD is retried
       relative to the REPO ROOT — the engine can be started from any cwd
       while ``nexus data-fetch`` writes its parquet relative to the repo
       root, so a CWD-relative default would otherwise be permanently dead.
    3. The parquet default (``RAW_M1_BARS_PATH``) relative to CWD, then repo
       root.
    4. The sibling ``.csv`` of the parquet default (legacy operator export),
       relative to CWD, then repo root.

    Returns ``None`` when no candidate exists — the caller keeps the
    fail-closed ReplayDataUnavailableError path (never a synthetic fallback).
    """
    # ``None``/"" = "use the default": the caller did not name a path, so the
    # default candidates below are allowed. Any other value is an EXPLICIT
    # operator choice that the defaults must never override (an absent
    # configured path stays absent and stays fail-closed).
    explicit = bool(configured)
    cfg = Path(configured) if configured else Path(RAW_M1_BARS_PATH)

    def _probe(cand: Path) -> Path | None:
        return (
            cand.resolve() if cand.is_file() and cand.suffix.lower() in _RAW_BARS_SUFFIXES else None
        )

    # (1) configured as-given (covers absolute paths that exist + relative
    # paths that happen to resolve against CWD).
    found = _probe(cfg)
    if found is not None:
        return found

    # (2) relative configured path, retried from the repo root.
    if not cfg.is_absolute():
        found = _probe(_REPO_ROOT / cfg)
        if found is not None:
            return found

    # (3)+(4) ONLY when the caller did not name a path: the parquet default,
    # then its legacy .csv sibling — each probed against CWD first (existing
    # behavior) and then the repo root.
    if explicit:
        return None

    default_parquet = Path(RAW_M1_BARS_PATH)
    for cand in (default_parquet, default_parquet.with_suffix(".csv")):
        found = _probe(cand)
        if found is not None:
            return found
        found = _probe(_REPO_ROOT / cand)
        if found is not None:
            return found
    return None
