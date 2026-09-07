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
#: bars; timestamps are the true historical timeline.
RAW_M1_BARS_PATH: str = "data/raw/XAUUSD_M1.csv"


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
        if allow_raw_fallback and Path(raw_path).exists():
            self._load_from_raw_bars_csv(raw_path)
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

    def _load_from_raw_bars_csv(self, path: str) -> None:
        """Loads the committed real M1 bar export (chronological, UTC)."""
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
            "path": path,
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
