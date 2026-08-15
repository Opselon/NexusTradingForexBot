"""
Unified Accounting & Performance Intelligence Core
===================================================
PHASE 08 / ACCOUNTING: the SINGLE canonical accounting authority for the
Nexus Scalp Engine.

One representation of financial truth is consumed by:

    REST API        -> /api/account/performance, /api/account/equity-curve, ...
    Dashboard       -> period tabs, curves, strategy panel, trade forensics
    Background worker -> incremental derived aggregation (AccountingWorker)
    Experience      -> trade / strategy attribution joins (identity chain)

Module map:

    models.py       canonical value objects (AccountSnapshot, TradeRecord,
                    PeriodReport, DrawdownReport, LiveAccountState, ...)
    periods.py      deterministic UTC DAY/WEEK/MONTH/YEAR boundaries (half-open)
    normalize.py    raw audit_ledger row -> canonical TradeRecord
    aggregation.py  pure period & drawdown aggregation math
    core.py         AccountingCore: single read facade over authoritative
                    SQLite tables + derived report cache
    worker.py       AccountingWorker: background, restartable, idempotent
                    derived-aggregation loop (never on the tick hot path)

INVARIANTS
----------
1. No synthetic numbers. A metric that cannot be derived from stored evidence
   is None, never a fabricated 0.0.
2. One boundary policy (UTC half-open periods) and one drawdown methodology;
   no consumer computes its own.
3. Closed trades are normalized exactly once (net = gross - commission - swap)
   and linked to their Experience decision via the outcome table's
   execution_id -> idempotency_key -> audit_experiences chain.
4. Derived aggregates are always rebuildable from authoritative records.
"""

from nexus_scalp.accounting.aggregation import aggregate_period, compute_drawdown
from nexus_scalp.accounting.core import AccountingCore
from nexus_scalp.accounting.models import (
    AccountSnapshot,
    DrawdownReport,
    ExitClassification,
    LiveAccountState,
    LossAttribution,
    PeriodReport,
    StrategyContribution,
    TradeForensicTrace,
    TradeOutcome,
    TradeRecord,
)
from nexus_scalp.accounting.normalize import classify_exit, classify_outcome, normalize_trade_row
from nexus_scalp.accounting.periods import (
    PeriodBounds,
    PeriodKind,
    ensure_utc,
    parse_sql_timestamp,
    period_bounds,
    recent_periods,
    utc_now,
)
from nexus_scalp.accounting.worker import AccountingWorker, format_worker_status

__all__ = [
    "AccountSnapshot",
    "AccountingCore",
    "AccountingWorker",
    "DrawdownReport",
    "ExitClassification",
    "LiveAccountState",
    "LossAttribution",
    "PeriodBounds",
    "PeriodKind",
    "PeriodReport",
    "StrategyContribution",
    "TradeForensicTrace",
    "TradeOutcome",
    "TradeRecord",
    "aggregate_period",
    "classify_exit",
    "classify_outcome",
    "compute_drawdown",
    "ensure_utc",
    "format_worker_status",
    "normalize_trade_row",
    "parse_sql_timestamp",
    "period_bounds",
    "recent_periods",
    "utc_now",
]
