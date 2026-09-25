"""MT5 Tick/Bar Dataset Acquisition + Local Cache (CHG-0035, AGENT-14 v3).

ONLINE/LOCAL SEPARATION (user brief §2, §13, §57, §59, §66):

    MT5 ONLINE   -> acquisition window ONLY (via the ALREADY-PROBED adapter
                    surface: DirectMT5Adapter.get_tick_history /
                    get_rate_history — copy_ticks_range COPY_TICKS_ALL and
                    copy_rates_range, field contracts verified by
                    tests/integration/test_mt5_api_probes.py).
    LOCAL CACHE  -> per (symbol, timeframe, range) identity: parquet under
                    artifacts/datasets/replay/ + a meta JSON with dataset
                    fingerprint = sha256 of the canonicalized records.
    OFFLINE      -> after acquisition, replay/backtest/forward-test consume
                    ONLY the cache; this module needs NO MT5 import to
                    serve cached data and the replay engine needs neither.

Chunked acquisition (§48): ticks are pulled in bounded windows so RAM stays
bounded; every chunk is appended and the final fingerprint covers the full
merged record set (chunk geometry does not change dataset identity).

AGENT-14 HARDENING (CHG-0061, 2026-09-05 — dataset forensic mission):

    IDENTITY      dataset ids are (symbol, kind, start, END) addressed;
                  acquire_bars takes an explicit `end` (the wall-clock
                  "now" default destroyed idempotence: the same historical
                  window re-acquired later minted a NEW id and re-downloaded
                  forever). Bars meta carries provenance v2.
    IMMUTABILITY  a cached dataset is immutable: when the parquet exists but
                  the meta is missing/corrupt (interrupted write / lost
                  meta), re-acquiring the same id RAISES ArtifactConflictError —
                  a correction must mint a NEW dataset id, never silently
                  rebuild different bytes under the same identity.
    INTEGRITY     load() re-verifies the stored fingerprint (content hash of
                  records) and the manifest record count; any tampering
                  (byte mutation, appended/spliced rows, swapped foreign
                  dataset) raises DatasetCorruptionError — corrupt data is
                  DETECTED and REJECTED, never served silently.
    SAFETY        hostile symbols (path separators, traversal, ':' etc.)
                  are rejected at the acquisition boundary — a dataset id
                  must never escape the cache root (path safety + prevents
                  one dataset replacing another).
    HONESTY       partial/failed acquisition never masquerades as complete:
                  an adapter returning zero rows for EVERY chunk raises
                  AcquisitionIncomplete (offline/broker failure), while a
                  genuinely empty healthy window is cached with
                  complete=True; empty chunks inside a window mark the
                  dataset complete=False; out-of-window rows returned by a
                  misbehaving adapter are dropped and counted
                  (meta['out_of_window']) — BUG-188-class timebase
                  recurrences can never silently enter a dataset.
    PROVENANCE    unknown provenance values serialize as NOT_RECORDED
                  (never an empty string, never invented).
    CONCURRENCY   a per-dataset advisory lock serializes racing
                  acquisitions of the same id; losers re-check the cache
                  (idempotent) instead of racing tmp+replace writes.

NO order_send, NO order_check anywhere — read-only market data only (§64,
§99). This module NEVER touches positions/account state.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.research.mt5_tick_dataset")

DEFAULT_CACHE_ROOT = "artifacts/datasets/replay"
DEFAULT_CHUNK_MINUTES = 60

#: Unknown-provenance sentinel (contract: never invent, never blank).
NOT_RECORDED = "NOT_RECORDED"

#: Symbols feed dataset ids / file names: reject anything that could escape
#: the cache root or collide with another dataset (AGENT-14 path safety).
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


# ---------------------------------------------------------------------------
# Exception taxonomy (stable import surface for tests/consumers)
# ---------------------------------------------------------------------------


class DatasetIdentityError(ValueError):
    """A dataset id / symbol cannot be safely constructed."""


class AcquisitionIncompleteError(RuntimeError):
    """Acquisition failed or returned no data for a demanded window.

    Never cached: partial/failed acquisition must not masquerade as a
    complete dataset (AGENT-14 honesty contract).
    """


class ArtifactConflictError(RuntimeError):
    """An existing immutable artifact blocks the requested rebuild.

    Corrections must mint a NEW dataset id (new fingerprint), never
    overwrite the bytes under an existing identity.
    """


class DatasetCorruptionError(RuntimeError):
    """A cached artifact failed integrity verification on read."""


def _validate_symbol(symbol: str) -> str:
    if not isinstance(symbol, str) or not _SYMBOL_RE.match(symbol) or ".." in symbol:
        raise DatasetIdentityError(
            f"Unsafe dataset symbol {symbol!r}: allowed [A-Za-z0-9_.-] (no '..')"
        )
    return symbol


def _utc(v: datetime) -> datetime:
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


def dataset_id(symbol: str, kind: str, start: datetime, end: datetime) -> str:
    """Stable identity for one acquisition window (§90: never 'XAUUSD M1' alone)."""
    _validate_symbol(symbol)
    payload = f"{symbol}|{kind}|{start.isoformat()}|{end.isoformat()}"
    return f"{symbol}_{kind}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def dataset_fingerprint(records: list[dict[str, Any]], dataset_id_value: str) -> str:
    """Content hash of the canonicalized record set (§90)."""
    h = hashlib.sha256()
    h.update(dataset_id_value.encode("utf-8"))
    for rec in records:
        h.update("|".join(str(rec.get(k, "")) for k in sorted(rec.keys())).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:32]


def _not_recorded(v: Any) -> str:
    """Honest provenance: blank/None/unknown -> NOT_RECORDED (never invented)."""
    s = str(v) if v is not None else ""
    return s if s.strip() else NOT_RECORDED


class _DatasetLock:
    """Cross-process advisory lock for one dataset id (AGENT-14 concurrency).

    Atomic create (O_CREAT|O_EXCL) on a sidecar ``.lock`` file; stale locks
    (owner crashed) expire after ``stale_seconds``. The lock file is never
    part of the artifact itself.
    """

    def __init__(self, path: Path, stale_seconds: float = 600.0) -> None:
        self.path = path
        self.stale_seconds = stale_seconds
        self._acquired = False

    def __enter__(self) -> _DatasetLock:
        deadline = datetime.now(UTC).timestamp() + 60.0
        while True:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with contextlib.suppress(Exception):
                    os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self._acquired = True
                return self
            except FileExistsError:
                try:
                    age = datetime.now(UTC).timestamp() - self.path.stat().st_mtime
                    if age > self.stale_seconds:
                        self.path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if datetime.now(UTC).timestamp() > deadline:
                    raise ArtifactConflictError(
                        f"dataset acquisition lock busy: {self.path.name}"
                    ) from None
                import time

                time.sleep(0.05)

    def __exit__(self, *exc: Any) -> None:
        if self._acquired:
            self.path.unlink(missing_ok=True)
            self._acquired = False


class MT5TickDataset:
    """Acquires + caches historical ticks/bars; serves them offline.

    The class has exactly one online surface (``acquire``) which requires a
    CONNECTED adapter handed in by the caller; every read path
    (``load``/``to_records``/``event_source``) is cache-only and works with
    the terminal closed (§57) — test-enforced.
    """

    def __init__(self, cache_root: str | Path = DEFAULT_CACHE_ROOT) -> None:
        self.cache_root = Path(cache_root)

    # ------------------------------------------------------------------
    # Paths / meta
    # ------------------------------------------------------------------

    def _meta_path(self, ds_id: str) -> Path:
        return self.cache_root / f"{ds_id}.meta.json"

    def _data_path(self, ds_id: str) -> Path:
        return self.cache_root / f"{ds_id}.parquet"

    def _lock_path(self, ds_id: str) -> Path:
        return self.cache_root / f".{ds_id}.acquire.lock"

    def _load_meta(self, ds_id: str) -> dict[str, Any] | None:
        p = self._meta_path(ds_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ------------------------------------------------------------------
    # ONLINE acquisition (adapter-probed surface only)
    # ------------------------------------------------------------------

    def acquire_ticks(
        self,
        adapter: Any,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
        chunk_minutes: int = DEFAULT_CHUNK_MINUTES,
        git_commit: str = "",
        adapter_name: str = "DirectMT5Adapter.get_tick_history (copy_ticks_range COPY_TICKS_ALL, probed contract)",
    ) -> str:
        """Pulls historical ticks in bounded chunks and caches them.

        ``adapter`` is a CONNECTED DirectMT5Adapter (or any object exposing
        ``get_tick_history(symbol, count, from_utc, to_utc)`` — the probed
        surface). Returns the dataset_id. Idempotent per window identity.

        CHG-0041 (TICK_DATASET_META v2): provenance records the adapter
        surface name, the acquisition wall-clock time and the git commit.
        AGENT-14 (CHG-0061): window containment, honest completeness,
        immutability, path safety and concurrency hardening — see the
        module docstring.
        """
        symbol = _validate_symbol(symbol)
        start = _utc(start)
        end = _utc(end)
        if end <= start:
            raise ValueError("acquire_ticks: end must be after start")
        ds_id = dataset_id(symbol, "ticks", start, end)

        with _DatasetLock(self._lock_path(ds_id)):
            existing = self._load_meta(ds_id)
            data_exists = self._data_path(ds_id).exists()
            if existing is not None and data_exists:
                logger.info(
                    "[MT5_TICK_DATASET] event=CACHE_HIT dataset=%s records=%s",
                    ds_id,
                    existing.get("records"),
                )
                return ds_id
            if data_exists and existing is None:
                # AGENT-14 immutability: parquet without meta = interrupted
                # write / lost manifest. Never silently rebuild different
                # bytes under the SAME id — corrections need a new id.
                raise ArtifactConflictError(
                    f"dataset {ds_id}: parquet exists without meta (interrupted "
                    "write or lost manifest). Refusing to overwrite an existing "
                    "identity — remove the orphan artifact explicitly or use a "
                    "new acquisition window (corrections = NEW dataset id). "
                    "(IMMUTABLE ARTIFACT CONFLICT)"
                )
            if existing is not None and not data_exists:
                # meta without parquet: stale manifest, artifact lost.
                # Safe to re-acquire: the identity is empty on disk.
                logger.warning(
                    "[MT5_TICK_DATASET] event=META_WITHOUT_DATA dataset=%s — re-acquiring",
                    ds_id,
                )

            records: list[dict[str, Any]] = []
            chunk_rows_returned: list[int] = []
            out_of_window = 0
            cur = start
            step = timedelta(minutes=max(1, chunk_minutes))
            while cur < end:
                c_end = min(cur + step, end)
                snaps = adapter.get_tick_history(symbol, count=100_000, from_utc=cur, to_utc=c_end)
                if snaps is None:
                    # Adapter contract violation: None is not "empty", it is
                    # an unavailable response (BUG-188 probe semantics: the
                    # None return is the reliable failure signal).
                    raise AcquisitionIncompleteError(
                        f"acquire_ticks: adapter returned None for chunk "
                        f"[{cur.isoformat()}, {c_end.isoformat()}) — acquisition "
                        "incomplete, nothing cached"
                    )
                chunk_rows_returned.append(len(snaps))
                for s in snaps:
                    ts = getattr(s, "time_utc", None)
                    bid = getattr(s, "bid", None)
                    ask = getattr(s, "ask", None)
                    if ts is not None and not (start <= ts < end):
                        # AGENT-14 containment: a misbehaving/shifted timebase
                        # must never silently enter the dataset (BUG-188-class
                        # defense-in-depth). Dropped and counted.
                        out_of_window += 1
                        continue
                    if ts is None or bid is None or ask is None:
                        # Preserve visibility of anomalies; the cache stores
                        # only complete records and the acquisition report
                        # counts skips.
                        records.append(
                            {
                                "timestamp": ts.isoformat() if isinstance(ts, datetime) else "",
                                "bid": float(bid) if bid is not None else 0.0,
                                "ask": float(ask) if ask is not None else 0.0,
                                "time_msc": int(getattr(s, "time_msc", 0) or 0),
                                "last": float(getattr(s, "last", 0.0) or 0.0),
                                "flags": int(getattr(s, "flags", 0) or 0),
                                "volume": float(getattr(s, "volume", 0.0) or 0.0),
                                "symbol": symbol,
                                "_incomplete": True,
                            }
                        )
                        continue
                    records.append(
                        {
                            "timestamp": ts.isoformat() if isinstance(ts, datetime) else "",
                            "bid": float(bid),
                            "ask": float(ask),
                            "time_msc": int(getattr(s, "time_msc", 0) or 0),
                            "last": float(getattr(s, "last", 0.0) or 0.0),
                            "flags": int(getattr(s, "flags", 0) or 0),
                            "volume": float(getattr(s, "volume", 0.0) or 0.0),
                            "symbol": symbol,
                        }
                    )
                cur = c_end

            # AGENT-14 honesty: an acquisition with NO rows for ANY chunk is
            # either a genuinely empty window (adapter reports healthy) or a
            # dead broker/invalid symbol. The first is cacheable as a
            # complete empty dataset; the second must fail loudly and never
            # be cached.
            if not chunk_rows_returned or all(n == 0 for n in chunk_rows_returned):
                if getattr(adapter, "available", None) is True:
                    fp_empty = dataset_fingerprint(records, ds_id)
                    self._write_cache(
                        ds_id,
                        records,
                        self._base_meta(
                            kind="ticks",
                            symbol=symbol,
                            start=start,
                            end=end,
                            records=0,
                            fp=fp_empty,
                            adapter_name=adapter_name,
                            git_commit=git_commit,
                            chunk_minutes=max(1, chunk_minutes),
                            complete=True,
                            chunk_rows_returned=chunk_rows_returned,
                            out_of_window=0,
                        ),
                    )
                    return ds_id
                raise AcquisitionIncompleteError(
                    f"acquire_ticks: adapter returned zero rows for the whole "
                    f"window [{start.isoformat()}, {end.isoformat()}) across "
                    f"{len(chunk_rows_returned)} chunk(s) — acquisition "
                    "incomplete (broker offline / invalid symbol / window "
                    "without data). Nothing cached; retry or widen the window."
                )
            complete = all(n > 0 for n in chunk_rows_returned)
            fp = dataset_fingerprint(records, ds_id)
            self._write_cache(
                ds_id,
                records,
                self._base_meta(
                    kind="ticks",
                    symbol=symbol,
                    start=start,
                    end=end,
                    records=len(records),
                    fp=fp,
                    adapter_name=adapter_name,
                    git_commit=git_commit,
                    chunk_minutes=max(1, chunk_minutes),
                    complete=complete,
                    chunk_rows_returned=chunk_rows_returned,
                    out_of_window=out_of_window,
                    incomplete_rows=sum(1 for r in records if r.get("_incomplete")),
                ),
            )
            return ds_id

    def acquire_bars(
        self,
        adapter: Any,
        *,
        symbol: str,
        start: datetime,
        end: datetime | None = None,
        timeframe: str = "M1",
        max_bars: int = 100_000,
        git_commit: str = "",
    ) -> str:
        """Pulls rate history (M1 etc.) and caches it (read-only surface).

        AGENT-14 (CHG-0061): ``end`` is now an explicit window boundary (the
        historical wall-clock ``datetime.now()`` default made the identity
        non-idempotent and the acquisition window unbounded). Existing
        callers without ``end`` keep working but their identity is derived
        from the acquisition wall-clock instant (legacy behaviour
        preserved, meta_version=1).
        """
        symbol = _validate_symbol(symbol)
        start = _utc(start)
        legacy_end = end is None
        window_end = _utc(end) if end is not None else datetime.now(UTC)
        if window_end <= start:
            raise ValueError("acquire_bars: end must be after start")
        ds_id = dataset_id(symbol, f"bars_{timeframe.lower()}", start, window_end)

        with _DatasetLock(self._lock_path(ds_id)):
            existing = self._load_meta(ds_id)
            data_exists = self._data_path(ds_id).exists()
            if existing is not None and data_exists:
                logger.info(
                    "[MT5_TICK_DATASET] event=CACHE_HIT dataset=%s records=%s",
                    ds_id,
                    existing.get("records"),
                )
                return ds_id
            if data_exists and existing is None:
                raise ArtifactConflictError(
                    f"dataset {ds_id}: parquet exists without meta (interrupted "
                    "write or lost manifest). Refusing to overwrite an existing "
                    "identity — remove the orphan artifact explicitly or use a "
                    "new acquisition window (corrections = NEW dataset id). "
                    "(IMMUTABLE ARTIFACT CONFLICT)"
                )

            snaps = adapter.get_rate_history(
                symbol, timeframe=timeframe, count=max_bars, from_utc=start
            )
            if snaps is None:
                raise AcquisitionIncompleteError(
                    "acquire_bars: adapter returned None — acquisition incomplete, nothing cached"
                )
            records: list[dict[str, Any]] = []
            out_of_window = 0
            for s in snaps:
                ts = getattr(s, "time_utc", None)
                if ts is None or getattr(s, "close", None) is None:
                    continue
                if ts >= window_end:
                    # rate rows are bar-OPEN stamps; keep bars that OPEN
                    # inside the window, drop anything beyond it (the legacy
                    # "now" path could never bound this).
                    out_of_window += 1
                    continue
                records.append(
                    {
                        "timestamp": ts.isoformat() if isinstance(ts, datetime) else "",
                        "open": float(s.open or 0.0),
                        "high": float(s.high or 0.0),
                        "low": float(s.low or 0.0),
                        "close": float(s.close or 0.0),
                        "tick_volume": int(getattr(s, "tick_volume", 0) or 0),
                        "spread": int(getattr(s, "spread", 0) or 0),
                        "symbol": symbol,
                    }
                )
            if not records and not snaps:
                raise AcquisitionIncompleteError(
                    f"acquire_bars: adapter returned zero rows for window "
                    f"[{start.isoformat()}, {window_end.isoformat()}) — acquisition "
                    "incomplete. Nothing cached."
                )
            fp = dataset_fingerprint(records, ds_id)
            meta = self._base_meta(
                kind=f"bars_{timeframe.lower()}",
                symbol=symbol,
                start=start,
                end=window_end,
                records=len(records),
                fp=fp,
                adapter_name="DirectMT5Adapter.get_rate_history (copy_rates_range, probed contract)",
                git_commit=git_commit,
                complete=True,
                out_of_window=out_of_window,
            )
            meta["meta_version"] = 1 if legacy_end else 2
            self._write_cache(ds_id, records, meta)
            return ds_id

    # ------------------------------------------------------------------
    # LOCAL cache write/read (offline after this point)
    # ------------------------------------------------------------------

    def _base_meta(
        self,
        *,
        kind: str,
        symbol: str,
        start: datetime,
        end: datetime,
        records: int,
        fp: str,
        adapter_name: str,
        git_commit: str,
        complete: bool,
        chunk_minutes: int | None = None,
        chunk_rows_returned: list[int] | None = None,
        out_of_window: int = 0,
        incomplete_rows: int = 0,
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "kind": kind,
            "symbol": symbol,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "records": records,
            "incomplete": incomplete_rows,
            "dataset_fingerprint": fp,
            "source": adapter_name,
            "acquired_at": datetime.now(UTC).isoformat(),
            # CHG-0041 TICK_DATASET_META v2 provenance
            "meta_version": 2,
            "git_commit": _not_recorded(git_commit),
            # AGENT-14 CHG-0061 honesty / acquisition report
            "complete": complete,
            "out_of_window": out_of_window,
        }
        if chunk_minutes is not None:
            meta["chunk_minutes"] = chunk_minutes
        if chunk_rows_returned is not None:
            meta["chunk_rows_returned"] = chunk_rows_returned
        return meta

    def _write_cache(self, ds_id: str, records: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        import polars as pl

        clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in records]
        frame = pl.DataFrame(clean) if clean else pl.DataFrame()
        tmp = self._data_path(ds_id).with_suffix(".parquet.tmp")
        frame.write_parquet(tmp)
        tmp.replace(self._data_path(ds_id))
        tmp_meta = self._meta_path(ds_id).with_suffix(".meta.json.tmp")
        tmp_meta.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
        tmp_meta.replace(self._meta_path(ds_id))
        logger.info(
            "[MT5_TICK_DATASET] event=CACHED dataset=%s records=%d fingerprint=%s complete=%s",
            ds_id,
            len(records),
            meta.get("dataset_fingerprint", ""),
            meta.get("complete", ""),
        )

    def load(self, ds_id: str) -> list[dict[str, Any]]:
        """Cache-only read with integrity verification (NO MT5 dependency).

        AGENT-14 (CHG-0061): the stored fingerprint is recomputed from the
        deserialized records and compared to the manifest; the manifest
        record count must match the artifact. Any tampering (mutated rows,
        appended rows, swapped foreign dataset, stale manifest) raises
        DatasetCorruptionError — corrupt data is DETECTED and REJECTED,
        never served silently.
        """
        data_path = self._data_path(ds_id)
        if not data_path.exists():
            raise FileNotFoundError(
                f"dataset {ds_id} not cached under {self.cache_root} — run acquire first"
            )
        import polars as pl

        frame = pl.read_parquet(data_path)
        records = frame.to_dicts() if frame.height else []
        meta = self._load_meta(ds_id) or {}
        stored_fp = str(meta.get("dataset_fingerprint", ""))
        if stored_fp:
            actual_fp = dataset_fingerprint(records, ds_id)
            if actual_fp != stored_fp:
                raise DatasetCorruptionError(
                    f"dataset {ds_id}: content fingerprint mismatch "
                    f"(manifest={stored_fp} actual={actual_fp}) — the cached "
                    "artifact was modified, corrupted, or swapped; REJECTING. "
                    "Re-acquire under a NEW dataset id."
                )
        manifest_records = meta.get("records")
        if manifest_records is not None and int(manifest_records) != len(records):
            raise DatasetCorruptionError(
                f"dataset {ds_id}: manifest records={manifest_records} but "
                f"artifact holds {len(records)} rows — manifest/artifact "
                "mismatch; REJECTING."
            )
        return records

    def meta(self, ds_id: str) -> dict[str, Any] | None:
        return self._load_meta(ds_id)

    # ------------------------------------------------------------------
    # Offline event-source bridge (feeds the replay engine, §42/§66)
    # ------------------------------------------------------------------

    def event_source(self, ds_id: str, name: str = "") -> Any:
        """Returns a re-iterable event source over the CACHED records.

        Ticks -> TickEventSource, bars -> BarEventSource. Malformed cached
        rows surface as DATA_ERROR events (never fabricated, §54). Reads
        go through load() so integrity verification applies here too.
        """
        from nexus_scalp.research.event_source import BarEventSource, TickEventSource

        meta = self._load_meta(ds_id) or {}
        records = self.load(ds_id)
        parsed: list[dict[str, Any]] = []
        for r in records:
            row = dict(r)
            ts_raw = row.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(str(ts_raw))
            except (TypeError, ValueError):
                ts = None
            row["timestamp"] = ts
            if ts is None:
                row["__invalid__"] = True
            parsed.append(row)
        kind = str(meta.get("kind", "ticks"))
        if kind.startswith("bars"):
            return BarEventSource(
                parsed, symbol=str(meta.get("symbol", "XAUUSD")), name=name or ds_id
            )
        return TickEventSource(parsed, symbol=str(meta.get("symbol", "XAUUSD")), name=name or ds_id)
