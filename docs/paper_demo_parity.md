# PAPER → DEMO Execution Parity (mission P0-1)

Status: MEASUREMENT INFRASTRUCTURE LANDED. Parity itself is currently
INSUFFICIENT_DATA — and that is the honest finding, not a failure.

## What exists (verified at implementation time)

| Side | Canonical source | Durability |
|---|---|---|
| PAPER execution | `PaperMT5Adapter` execution ledger (in-memory rows: requested vs fill price, bid/ask at request, spread, slippage, rejection_reason, per attempt) | Was MEMORY-ONLY + `paper_state.json` balance; now mirrored every ≤6h into `audit_paper_executions` (SQLite, `artifacts/audit.db`) by `MaintenanceCycle` → `risk/paper_parity.export_paper_ledger` |
| DEMO/LIVE execution | `audit_broker_deals` / `audit_broker_orders` / `audit_broker_trades` (normalized copy of MT5 history, synced by `BrokerHistorySyncWorker`, server-timezone corrected, idempotent by broker ticket) | Durable |

Both sides live in the same audit DB, so one weekly job can compare them
without any new storage layer.

## What was measured (2026-09-07)

- `audit_paper_executions` did NOT exist; the 2026-09-03 paper sessions
  left zero durable execution rows (only ~80 PAPER account snapshots and
  a balance file). PAPER parity had no data to consume — fixed by the
  durable export.
- Signal/decision provenance is split: `audit_signals` (7-day retention,
  carries `spread_usd`) joins to `audit_experiences`/`audit_ledger` by
  `request_id == order_id` for only ~43/163 closed ledger rows; the
  experience outcomes table is the richer join (158 executed outcomes
  with `spread_at_execution`, slippage, latency, MAE/MFE, R).
- Broker truth: 3879 closed XAUUSD trades 2026-07-15..2026-09-04; all
  `BROKER_DEALS` sourced; magic 888101 (engine) + 99999 + 0 (manual/
  external) — magic filtering is REQUIRED in any parity or cost study.

## Same-signal comparison — current design

`risk/paper_parity.build_parity_snapshot(audit, lookback_days=7)`:

- PAPER stats: attempts, fills, fill_rate, mean spread, mean slippage
- BROKER stats: trades, win_rate, mean duration, net PnL (total)
- Status: `INSUFFICIENT_DATA` until BOTH sides have samples in-window;
  otherwise `MEASURED` with an observational comparison only.

The weekly snapshot runs inside `MaintenanceCycle` (fail-isolated, off the
tick path, INV-001 preserved) and is logged as `[PARITY] event=SNAPSHOT_BUILT`.

## Parity status vocabulary

`GOOD | DEGRADED | INVALID | INSUFFICIENT_DATA` — the job currently emits
`INSUFFICIENT_DATA` / `MEASURED`. Mapping MEASURED → GOOD/DEGRADED/INVALID
requires paired same-signal outcomes on both sides, which requires paper
sessions to actually place trades (paper fills) while the broker side
records the same window. This is observational-only: no Demo trades are
placed by this job.

## Calibration rules (binding)

- No bias factor may be produced without: source data range, sample
  count, methodology, uncertainty, and a calibration version — none of
  which the current sample supports. The snapshot refuses to emit one.
- Calibration factors must never be applied retroactively to completed
  research; they apply forward from their version timestamp only.

## What is still needed before a GOOD/DEGRADED verdict

1. Run PAPER sessions long enough to accumulate fills (the engine's
   paper mode with NEXUS_PAPER_SEED_XAUUSD set to a real price).
2. Let `BrokerHistorySyncWorker` cover the same window on the Demo/LIVE
   account.
3. Pair by signal: request_id-level join (experience outcome
   `spread_at_execution`/slippage vs paper ledger rows in the same
   session) — sample counts decide feasibility.
4. Only then compute per-metric deltas (spread, slippage, fill rate,
   rejection rate) with confidence intervals.
