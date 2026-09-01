"""Event aggregation for repeated diagnostic log lines (Agent 2, 2026-09-01).

WHY THIS EXISTS (log-noise reduction WITHOUT information loss)
---------------------------------------------------------------
Production evidence (2026-09-01, logs/*/2026/09/2026-09-01.log):
  * ``ORPHAN_CLASSIFIED_UNKNOWN`` was emitted 729 times for the SAME 243
    trade ids (once per trade per research-dataset build; the research
    worker rebuilds the dataset every ~60s and the classify-once cache is
    per-PROCESS, while the info-log file is per-DAY — so every restart or
    later cycle re-logs the whole corpus).
  * ``DATASET_REJECTED reason=MISSING_REALIZED_R`` behaves the same.
  * Both events carry identical semantics per row except the trade id, so
    N rows = N nearly-identical lines with near-zero marginal signal.

DESIGN (spec §13-§19)
---------------------
* Aggregation happens at the CALL SITE layer (a tiny, bounded helper), NOT
  by touching the logging pipeline, so severity, schema and rendering of
  every other event are unchanged.
* Logical signature = (event, reason, stage, recoverable). One aggregate
  per signature: ``count``, ``first_seen``, ``last_seen``, bounded
  ``sample_ids`` (first N, default 5).
* Flush policy: explicit flush by the producer at batch/cycle boundaries or
  on shutdown. Aggregates that are never flushed still cannot grow without
  bound: the store is capped at MAX_GROUPS signatures (LRU eviction of the
  OLDEST-TOUCHED group only after it has been flushed once, so no data is
  silently dropped on a path that logs its aggregates).
* Cost: dict lookup + counter update on the repeated path (no I/O, no lock
  beyond a re-entrant lock, safe to call from workers/tick-adjacent code).

The aggregated summary line keeps the same event name + ``reason`` +
``stage`` + ``recoverable`` fields (so log parsers and the observability
map keep working) and adds ``count``/``sample_ids``/``first_seen``/
``last_seen`` — MORE information per line, dramatically fewer lines.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: Bounded per-group sample ids in the summary line (spec §15).
DEFAULT_SAMPLE_IDS = 5

#: Maximum distinct signatures held. Bounded memory (spec §28/§29).
MAX_GROUPS = 64


@dataclass
class _Group:
    event: str
    reason: str
    stage: str
    recoverable: bool
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    sample_ids: list[str] = field(default_factory=list)
    #: True once this group has been summarized+flushed at least once.
    ever_flushed: bool = False


class EventBatchAggregator:
    """Bounded, thread-safe (event, reason, stage, recoverable) batcher.

    Usage (dataset-build boundary)::

        agg = EventBatchAggregator()
        for rec in records:
            ...
            agg.add(event="ORPHAN_CLASSIFIED_UNKNOWN", reason=reason,
                    stage="dataset", recoverable=False, trade_id=rec.id)
        agg.flush(logger.info)   # one summary line per distinct signature
    """

    def __init__(self, *, sample_ids: int = DEFAULT_SAMPLE_IDS, max_groups: int = MAX_GROUPS):
        self._sample_ids = max(1, int(sample_ids))
        self._max_groups = max(1, int(max_groups))
        self._groups: OrderedDict[tuple[Any, ...], _Group] = OrderedDict()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # collection (cheap path)
    # ------------------------------------------------------------------
    def add(
        self,
        *,
        event: str,
        reason: str,
        stage: str,
        recoverable: bool,
        trade_id: str | None = None,
        now: float | None = None,
    ) -> bool:
        """Record one occurrence. Returns True when this was the FIRST
        occurrence of its signature (callers typically log the first event
        immediately for timeliness and let the rest aggregate)."""
        now = time.time() if now is None else now
        key = (event, reason, stage, bool(recoverable))
        first = False
        with self._lock:
            g = self._groups.get(key)
            if g is None:
                first = True
                g = _Group(
                    event=event,
                    reason=reason,
                    stage=stage,
                    recoverable=bool(recoverable),
                    count=0,
                    first_seen=now,
                    last_seen=now,
                )
                # bounded store: drop the least-recently-used group ONLY if
                # it has been flushed at least once (never lose unflushed
                # evidence just to make room).
                while len(self._groups) >= self._max_groups:
                    self._evict_one_locked()
                self._groups[key] = g
            g.count += 1
            g.last_seen = now
            if trade_id and len(g.sample_ids) < self._sample_ids and trade_id not in g.sample_ids:
                g.sample_ids.append(trade_id)
            self._groups.move_to_end(key)
        return first

    def _evict_one_locked(self) -> None:
        for key, g in self._groups.items():
            if g.ever_flushed:
                del self._groups[key]
                return
        # pathological: > MAX_GROUPS live signatures without a flush. The
        # first occurrence of every signature is still logged immediately by
        # the producer's first=True path, and the count is surfaced via
        # pending(); the store stays bounded per spec §28/§29.
        self._groups.popitem(last=False)

    # ------------------------------------------------------------------
    # flush (batch boundary / shutdown)
    # ------------------------------------------------------------------
    def flush(self, log: Callable[..., Any], *, only_repeats: bool = False) -> int:
        """Emit one aggregate summary per signature; clears the store.

        ``log`` receives a single message string per group. Groups with
        count==1 can be skipped with ``only_repeats=True`` (their single
        event was already logged at first occurrence).

        Returns the number of summary lines emitted.
        """
        emitted = 0
        with self._lock:
            for _key, g in list(self._groups.items()):
                if only_repeats and g.count <= 1:
                    # still mark flushed so the store stays bounded
                    g.ever_flushed = True
                    continue
                sample = ",".join(g.sample_ids) if g.sample_ids else "-"
                log(
                    f"[STRATEGY_RESEARCH] event={g.event}_BATCH_SUMMARY "
                    f"count={g.count} stage={g.stage} reason={g.reason} "
                    f"recoverable={'true' if g.recoverable else 'false'} "
                    f"sample_ids=[{sample}] "
                    f"first_seen={_fmt(g.first_seen)} last_seen={_fmt(g.last_seen)}"
                )
                emitted += 1
                g.ever_flushed = True
            # keep flushed singletons for the eviction policy; clear counts
            for g in self._groups.values():
                g.count = 0
                g.sample_ids.clear()
            # drop flushed groups entirely (their next occurrences start fresh)
            for key in [k for k, g in self._groups.items() if g.ever_flushed and g.count == 0]:
                del self._groups[key]
        return emitted

    def pending(self) -> int:
        """Number of occurrences currently buffered (observability)."""
        with self._lock:
            return sum(g.count for g in self._groups.values())


def _fmt(ts: float) -> str:
    """Compact UTC timestamp for a summary line."""
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
