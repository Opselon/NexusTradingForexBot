"""Consistency classes and read/write intent for the database fabric.

Phase 4/5 of the DATABASE FABRIC mission (DB-FABRIC-001).

Every repository method declares a :class:`ConsistencyClass`. The
:class:`~nexus_scalp.database.fabric.routing.ConsistencyRouter` uses it to
decide which connection the call lands on:

* ``STRONG``    -> the primary read pool.  Never a replica.  Same authority as
                   the write plane, so a read immediately following a write
                   sees it (BUG-140 read-after-write pattern).
* ``EVENTUAL``  -> the read pool; a configured read replica may serve these.

The classes are the ONLY routing signal domain code is allowed to carry.
Provider identity, pool selection and replica lag stay inside the fabric.
"""

from __future__ import annotations

from enum import StrEnum


class ConsistencyClass(StrEnum):
    """Routing intent for a read operation."""

    #: Strong consistency: the read must reflect all committed writes,
    #: including writes made moments earlier on the same logical path.
    #: Routed to the primary read pool — never to a replica.
    STRONG = "STRONG"

    #: Eventual consistency: the read may observe a slightly stale snapshot.
    #: Analytics, dashboards, aggregated research views, reporting.
    #: A configured read replica may serve these.
    EVENTUAL = "EVENTUAL"

    @property
    def allows_replica(self) -> bool:
        """True when this class may be routed to a read replica."""
        return self is ConsistencyClass.EVENTUAL


#: Read operations whose correctness depends on seeing a write that just
#: happened.  Kept as a module-level contract so the strong-consistency
#: surface is auditable in one place.
STRONG_READ_OPERATIONS: frozenset[str] = frozenset(
    {
        # accounting / execution truth
        "has_ledger_opened",
        "count_ledger_opened_unclosed",
        "get_trade",
        "get_execution",
        "get_broker_trade",
        "get_broker_deal",
        "get_broker_order",
        # experience read-after-write (BUG-140)
        "get_experience",
        "get_experience_outcome",
        # risk state
        "get_runtime_risk_state",
        "get_breaker_anchors",
        # governance / promotion
        "get_governance_state",
        "get_promotion_audit",
        "get_model_governance_events",
        # migration state
        "get_migration_state",
        "get_schema_version",
        # settings read-after-write
        "get_setting",
    }
)


class WriteIntentRequiredError(RuntimeError):
    """A write statement was issued outside a write-scoped context.

    The read plane is read-only by construction; reaching this error means a
    caller tried to mutate through it.
    """


class ReadOnlyViolationError(RuntimeError):
    """The provider rejected a mutation attempt on a read-only connection.

    On SQLite this is raised by the C-level authorizer; on PostgreSQL by
    ``default_transaction_read_only = on``.
    """


def classify_read(method_name: str, declared: ConsistencyClass | None = None) -> ConsistencyClass:
    """Resolve the consistency class for a repository read method.

    An explicit declaration always wins.  Otherwise a method listed in
    :data:`STRONG_READ_OPERATIONS` is STRONG; the default for unlisted
    methods is EVENTUAL (fail-open on latency, never on safety for the
    known-strong set).
    """
    if declared is not None:
        return declared
    if method_name in STRONG_READ_OPERATIONS:
        return ConsistencyClass.STRONG
    return ConsistencyClass.EVENTUAL
