# src/nexus_scalp/intelligence/lifecycle.py

- PURPOSE: The immutable position-timeline recorder — turns every observed
  state of an open position into a permanent, self-describing
  `PositionLifecycleEvent` so the system can later reconstruct exactly WHY a
  position moved the way it did.
- ARCHITECTURE LAYER: Application (fed from the live path; persists via the
  AuditRepository background queue — the same pattern as the Phase 08 ledger).
- RESPONSIBILITY (docstring lines 10-18): events are IMMUTABLE and
  DEDUPLICATED by `event_key` (ticket + sequence + event type) —
  `ON CONFLICT DO NOTHING` makes a replayed tick stream or reconnect a no-op;
  the tracker records and asks questions, never executes; state is in memory +
  persisted via the audit queue, restartable from the persisted events.
- DEPENDENCIES: `audit_repository.AuditRepository` (private _queue/_db_path/
  _is_sqlite), `intelligence.models` (DecisionContext, MarketContext,
  PositionEventType, PositionLifecycleEvent, PositionPerformance,
  PositionSnapshot), stdlib (hashlib, json, sqlite3), observability.logging.
- CONNECTS TO: LiveEngine (observe_position per position-state update;
  finalize_exit at close); store.py (list_events_for_ticket → load_lifecycle_events
  reconstruction); `audit_ledger` closed rows for EXITED enrichment; worker.py
  (wiring, though the worker's cycle does not drive the tracker).
- KEY CONCEPTS:
  - Event thresholds (module constants lines 43-49): expectation confirmed at
    MFE ≥ 0.25R; MFE_REACHED at MFE ≥ 0.40R (high-water only); giveback notice
    at ≥ 35% of peak profit surrendered; DEGRADING at MAE ≥ 0.55R while still
    held.
  - `observe_position` (lines 115-335): first sighting initializes ticket_meta
    (entry price, created_at, trade/experience ids, peak profit/loss),
    resets the sequence, emits POSITION_CREATED and (when entry_price > 0)
    POSITION_OPENED. Subsequent observations: update high-water marks, then
    emit MFE_REACHED (monotonic mfe_seen high-water), EXPECTATION_CONFIRMED
    (once), PROFIT_GIVEBACK (once, requires peak > 0 AND floating ≥ 0 AND
    giveback_pct ≥ threshold), DEGRADING (once), RECOVERY_ATTEMPT (once —
    only after DEGRADING state and floating > 0).
  - POSITION_MOVING throttling (lines 275-335, BUG-054 discipline): persisted
    only when ≥60s since last, OR SL changed, OR TP changed, OR price drift ≥
    15% of planned risk — bounded storage while keeping SL/TP/state-change
    evidence; `_last_emitted` 5-tuple guard with backward-compatible unpacking
    of legacy 2-/3-tuples.
  - `emit` (lines 337-409): sequence per ticket (monotonic, restart-safe),
    event_key = `lev_<sha256(ticket|seq|type)[:16]>`, payload JSON snapshot of
    the full self-describing event, queued insert with ON CONFLICT DO NOTHING.
    NOT on the tick hot path — only queue + counter + debug log.
  - `finalize_exit` (lines 411-462): emits POSITION_EXITED; when the caller
    supplied no realized numbers (both 0.0), enriches from the authoritative
    `audit_ledger` closed row (`_read_closed_ledger` — net PnL + exit_mechanism;
    R approximated as ±max(mfe, mae) from performance because planned risk is
    not on the row). Clears per-ticket in-memory state so re-observation after
    cleanup starts fresh.
  - `_build_event_key` (lines 497-501): deterministic dedup key.
- HOT PATH / PERFORMANCE: `observe_position` is called from the live engine on
  position-state updates, NOT per tick; emit() is purely in-memory + one queued
  write. MOVING throttling (60s / SL-TP deltas / price-drift) bounds steady-state
  rows (~587 POSITION_MOVING/day per prior auditorium measurements).
- EDGE CASES & PITFALLS:
  - `_read_closed_ledger` R approximation (line 491) treats
    `r_mult = perf.mfe if perf.mfe > perf.mae else -perf.mae` — an ESTIMATE
    (not net/risk), and it selects columns (ticket, exit_price, net_pnl_usd,
    gross_pnl_usd, exit_mechanism, duration_seconds, MFE_usd, MAE_usd) that
    are NOT all used: exit_price/gross_pnl_usd/duration_seconds/MFE_usd/MAE_usd
    are fetched but never read — dead columns, harmless but confusing.
  - `finalize_exit` enrichment triggers only when BOTH realized_pnl_usd == 0.0
    AND realized_r == 0.0 — a genuine break-even close (0.0 PnL but nonzero R
    inputs) skips the ledger enrichment; acceptable because R is passed
    separately.
  - The event_key includes the per-ticket sequence which resets on
    finalize_exit cleanup — a re-opened ticket (same ticket number reused by
    the broker) would collide padded by sequence; improbable in MT5.
  - MOVING's `meaningful` uses `price_drift >= 0.15 * (planned_risk or 1.0)`
    — when planned_risk is 0 (no SL), drift threshold becomes 0.15 price units,
    which for XAUUSD is a tiny amount and can un-throttle MOVING events; the
    60s window remains the dominant bound.