# src/nexus_scalp/accounting/__init__.py

- PURPOSE: Package facade for the canonical Accounting & Performance Intelligence Core (PHASE 08) — re-exports the public API surface so consumers (REST API, dashboard, worker, Experience Intelligence) import one stable namespace.
- ARCHITECTURE LAYER: Application (read facade over authoritative SQLite tables).
- RESPONSIBILITY: Declares the accounting module map, the invariants (no synthetic numbers, one boundary policy, one drawdown methodology, one normalization, rebuildable derived aggregates), and the `__all__` contract used by `from nexus_scalp.accounting import *`.
- DEPENDENCIES:
  - `accounting.aggregation` → `aggregate_period`, `compute_drawdown` (pure aggregation math).
  - `accounting.core` → `AccountingCore` (single read facade).
  - `accounting.models` → the canonical value objects (AccountSnapshot, TradeRecord, PeriodReport, DrawdownReport, LiveAccountState, StrategyContribution, TradeForensicTrace, ExitClassification, TradeOutcome, LossAttribution).
  - `accounting.normalize` → `normalize_trade_row`, `classify_exit`, `classify_outcome` (ledger-row normalization).
  - `accounting.periods` → `PeriodBounds`, `PeriodKind`, `ensure_utc`, `parse_sql_timestamp`, `period_bounds`, `recent_periods`, `utc_now` (UTC half-open boundaries).
  - `accounting.worker` → `AccountingWorker`, `format_worker_status` (background refresher).
- CONNECTS TO: Everything that needs performance truth imports from here: the REST API (`/api/account/performance`, `/equity-curve`, ...), the dashboard tabs, the worker wiring, and Experience Intelligence joins. It is the single import chokepoint for the package.
- KEY CONCEPTS:
  - The module docstring is the normative statement of the four invariants (lines 26-35): (1) a metric that cannot be derived is `None`, never a fabricated 0.0; (2) one UTC half-open period policy and one drawdown methodology, no consumer computes its own; (3) closed trades normalized exactly once (net = gross - commission - swap) and linked to the Experience decision via the outcome table's `execution_id -> idempotency_key -> audit_experiences` chain; (4) derived aggregates are always rebuildable.
  - The `__all__` list (lines 64-90) is the public contract; adding a symbol here is a public-API event.
- HOT PATH / PERFORMANCE: None — pure import surface, no runtime work.
- EDGE CASES & PITFALLS:
  - `normalize_trade_row` is intentionally the ONLY exported path from ledger rows to `TradeRecord`; consumers must never build `TradeRecord` by hand or the "net PnL computed exactly once" invariant degrades.