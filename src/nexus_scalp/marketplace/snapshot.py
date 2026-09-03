"""
Marketplace runtime snapshot store — immutable enabled-set snapshots,
monotonic version, atomic swap, event-bus listener hook (CHG-0056).

Replicates the RuntimeConfigStore snapshot pattern (configuration/runtime_config.py):
  * enabled_set is an immutable frozenset
  * snapshots are immutable StrategyRuntimeSnapshot records
  * get_snapshot() is lock-free (single pointer read in the unlocked path)
  * publishes a snapshot revision to registered listeners on every mutation
  * versions are monotonic (never decremented)
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SnapshotListener = Callable[["StrategyRuntimeSnapshot"], None]


@dataclass(frozen=True)
class StrategyRuntimeSnapshot:
    version: int
    enabled_set: frozenset[str]
    created_at: str
    source: str = "marketplace"


class StrategyRuntimeSnapshotStore:
    """Immutable per-mode enabled-set store with atomic hot-reload contract.

    Consumers read a consistent (version, enabled_set) pair; install/enable/
    disable mutations build a NEW snapshot and swap it atomically under a lock
    (old consumers keep their snapshot). No live-engine wiring in this pass
    (ARCH_SPEC §2 §6: hotspot CONTRACT, not consumer).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot = StrategyRuntimeSnapshot(
            version=1, enabled_set=frozenset(), created_at=datetime.now(UTC).isoformat()
        )
        self._listeners: list[SnapshotListener] = []
        self._history: list[StrategyRuntimeSnapshot] = [self._snapshot]

    # -- reads (lock-free path mirrors RuntimeConfigStore) ------------------

    def get_snapshot(self) -> StrategyRuntimeSnapshot:
        return self._snapshot

    def get_version(self) -> int:
        return self._snapshot.version

    def history(self, limit: int = 50) -> list[StrategyRuntimeSnapshot]:
        return list(self._history[-max(1, limit) :])

    # -- listeners ----------------------------------------------------------

    def add_listener(self, fn: SnapshotListener) -> None:
        with self._lock:
            self._listeners.append(fn)

    def _notify(self, snap: StrategyRuntimeSnapshot) -> None:
        for fn in list(self._listeners):
            try:
                fn(snap)
            except Exception:
                pass

    # -- mutations (atomic swap) --------------------------------------------

    def publish(
        self, enabled_set: frozenset[str] | set[str], *, source: str = "marketplace"
    ) -> StrategyRuntimeSnapshot:
        enabled = frozenset(enabled_set)
        with self._lock:
            version = self._snapshot.version + 1
            snap = StrategyRuntimeSnapshot(
                version=version,
                enabled_set=enabled,
                created_at=datetime.now(UTC).isoformat(),
                source=source,
            )
            self._snapshot = snap
            self._history.append(snap)
        self._notify(snap)
        return snap

    def apply_enable(self, seed_id: str, *, source: str = "marketplace") -> StrategyRuntimeSnapshot:
        return self.publish(self._snapshot.enabled_set | {seed_id}, source=source)

    def apply_disable(
        self, seed_id: str, *, source: str = "marketplace"
    ) -> StrategyRuntimeSnapshot:
        return self.publish(self._snapshot.enabled_set - {seed_id}, source=source)

    def to_dict(self, snap: StrategyRuntimeSnapshot | None = None) -> dict[str, Any]:
        s = snap if snap is not None else self._snapshot
        return {
            "version": s.version,
            "enabled_set": sorted(s.enabled_set),
            "created_at": s.created_at,
            "source": s.source,
        }


__all__ = ["StrategyRuntimeSnapshot", "StrategyRuntimeSnapshotStore"]
