"""BoundedLRUMap — one bounded, in-process mapping for guard/report caches.

BUG-290 / BUG-291 (perf-wave R7/R8, 2026-09-15, NSE-Swarm role 1): several
long-lived engine dicts grew without any eviction (`_processed_orders`,
`_ai_flip_warn_times`, `AccountingCore._report_cache`). This module is the
single owner of the "bounded in-process map" seam so growth caps never drift
into unbounded regressions again.

CONTRACT
--------
* Full ``MutableMapping`` surface (``in``, ``[]`` get/set, ``get``, ``len``,
  ``iter``, ``clear``, ``pop``). Callers may still receive a plain ``dict``
  (tests swap them in); all production use sites must stay on this surface.
* Insertion and read-through-``__getitem__``/``get`` move the key to the
  MRU end. ``__contains__`` deliberately does NOT touch ordering: the
  duplicate-dispatch guard lookup is ``in``-only and must never write on the
  hot path (INV-001: zero added blocking work per tick — every operation is
  O(1), no logging, no I/O).
* When the map exceeds ``maxsize``, the LEAST-recently-used entries are
  evicted (oldest end first) and the ``evictions`` counter advances.
* Eviction means the key loses its protection/memo: callers must ONLY use
  this for state where re-appearance after eviction is harmless
  (idempotency of already-final dispatches, rebuildable derived reports,
  log-throttle stamps). It is NOT a durability store and NOT a substitute
  for the durable audit ledger.

Import-safety: stdlib only, zero app imports (usable from any layer,
mirroring the discipline of storage/policy.py and executions_idempotency.py).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, MutableMapping
from typing import TypeVar

__all__ = ["BoundedLRUMap"]

_K = TypeVar("_K")
_V = TypeVar("_V")


class BoundedLRUMap(MutableMapping[_K, _V]):
    """LRU-capped mapping with an eviction counter for observability."""

    __slots__ = ("_data", "_maxsize", "evictions", "name")

    def __init__(self, name: str = "bounded_map", *, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError(f"maxsize must be >= 1, got {maxsize}")
        self._data: OrderedDict[_K, _V] = OrderedDict()
        self._maxsize = int(maxsize)
        #: Total entries evicted since construction (metric only, never read
        #: by trading/accounting logic).
        self.evictions = 0
        #: Short label used in logs/reports to name the guard instance.
        self.name = name

    # --- properties ---------------------------------------------------------
    @property
    def maxsize(self) -> int:
        return self._maxsize

    # --- Mapping surface ----------------------------------------------------
    def __getitem__(self, key: _K) -> _V:
        value = self._data[key]  # raises KeyError like a dict
        self._data.move_to_end(key)
        return value

    def __setitem__(self, key: _K, value: _V) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)
            self.evictions += 1

    def __delitem__(self, key: _K) -> None:
        del self._data[key]

    def __iter__(self) -> Iterator[_K]:
        # Oldest -> newest (LRU order), stable snapshot like dict iteration
        # is under the GIL for our single-writer usage.
        return iter(list(self._data))

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        # NO touch: membership checks on the dispatch guard path must not
        # reorder or write (keeps hot-path cost identical to dict lookup).
        return key in self._data

    def clear(self) -> None:
        self._data.clear()

    def __repr__(self) -> str:
        return (
            f"BoundedLRUMap({self.name!r}, size={len(self._data)}, "
            f"maxsize={self._maxsize}, evictions={self.evictions})"
        )
