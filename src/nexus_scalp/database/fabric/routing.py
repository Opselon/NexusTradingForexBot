"""Consistency router — decides which connection a read lands on.

Phase 5 of the DATABASE FABRIC mission (DB-FABRIC-001).

Rules:
  * STRONG reads ALWAYS use the primary read authority — never a replica,
    even when one is configured.  This preserves read-after-write for
    accounting, positions, execution/risk state, governance and migration.
  * EVENTUAL reads use the replica ONLY when the domain config allows it
    AND a replica is configured.  Default (no replica) => primary read pool.
  * Replica lag is checked when a replica is configured; a lagging replica
    is treated as unavailable (falls back to primary, health flag raised).

The router never inspects provider identity — it only moves intent
(consistency class) into a connection choice.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from nexus_scalp.database.fabric.config import FabricDomainConfig, ReadConsistency
from nexus_scalp.database.fabric.consistency import ConsistencyClass
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.database.fabric.routing")

#: Maximum replica lag (seconds of WAL distance) before reads fall back.
MAX_REPLICA_LAG_SEC = 30.0

#: How long a cached lag reading is trusted.
LAG_CACHE_TTL_SEC = 5.0


@dataclass
class ReplicaState:
    """Current read-replica condition (populated by the health probe)."""

    configured: bool = False
    #: Estimated lag in seconds (None = unknown / unreachable).
    lag_sec: float | None = None
    available: bool = False
    last_checked: float | None = None

    def fresh(self) -> bool:
        if self.last_checked is None:
            return False
        return (time.monotonic() - self.last_checked) <= LAG_CACHE_TTL_SEC


@dataclass
class RoutingDecision:
    """The outcome of one routing call — auditable and testable."""

    consistency: ConsistencyClass
    used_replica: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "consistency": self.consistency.value,
            "used_replica": self.used_replica,
            "reason": self.reason,
        }


class ConsistencyRouter:
    """Routes a read to the correct plane by consistency class."""

    __slots__ = ("_cfg", "_fallbacks", "_lock", "_replica")

    def __init__(self, cfg: FabricDomainConfig) -> None:
        import threading

        self._cfg = cfg
        self._replica = ReplicaState(
            configured=bool(cfg.read_dsn),
            available=bool(cfg.read_dsn),
        )
        self._fallbacks = 0
        self._lock = threading.Lock()

    # -- replica state -----------------------------------------------------

    def update_replica_state(self, *, available: bool, lag_sec: float | None) -> None:
        """Called by the health probe after measuring the replica."""
        self._replica.available = available
        self._replica.lag_sec = lag_sec
        self._replica.last_checked = time.monotonic()

    @property
    def replica(self) -> ReplicaState:
        return self._replica

    # -- routing -----------------------------------------------------------

    def route(self, consistency: ConsistencyClass, *, method_name: str = "") -> RoutingDecision:
        """Decide where a read goes.  Never raises — falls back to primary."""
        if consistency is ConsistencyClass.STRONG:
            return RoutingDecision(
                consistency=consistency,
                used_replica=False,
                reason="STRONG read -> primary (read-after-write guarantee)",
            )

        # EVENTUAL
        if not self._replica.configured:
            return RoutingDecision(
                consistency=consistency,
                used_replica=False,
                reason="EVENTUAL read, no replica configured -> primary",
            )

        if self._cfg.read_consistency is ReadConsistency.PRIMARY_ONLY:
            return RoutingDecision(
                consistency=consistency,
                used_replica=False,
                reason="EVENTUAL read, consistency posture primary_only",
            )

        if not self._replica.available:
            self._fallbacks += 1
            return RoutingDecision(
                consistency=consistency,
                used_replica=False,
                reason="EVENTUAL read, replica unavailable -> primary fallback",
            )

        lag = self._replica.lag_sec
        if lag is not None and lag > MAX_REPLICA_LAG_SEC:
            self._fallbacks += 1
            return RoutingDecision(
                consistency=consistency,
                used_replica=False,
                reason=f"EVENTUAL read, replica lag {lag:.1f}s > {MAX_REPLICA_LAG_SEC}s -> primary",
            )

        return RoutingDecision(
            consistency=consistency,
            used_replica=True,
            reason="EVENTUAL read -> replica",
        )

    @property
    def fallback_count(self) -> int:
        return self._fallbacks

    # -- assertions (used by the regression guards) -------------------------

    def assert_strong_never_replica(self, consistency: ConsistencyClass) -> None:
        """Invariant: a STRONG read must never use the replica."""
        decision = self.route(consistency)
        if consistency is ConsistencyClass.STRONG and decision.used_replica:
            raise AssertionError(f"STRONG read routed to replica: {decision.to_dict()}")

    def assert_no_write_on_read(self, statement: str) -> None:
        """Invariant: no mutation statement may run on the read plane."""
        normalized = statement.strip().upper()
        if normalized.startswith(
            ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE", "VACUUM", "ATTACH")
        ):
            raise AssertionError(f"mutation statement on the read plane: {statement[:80]!r}")
