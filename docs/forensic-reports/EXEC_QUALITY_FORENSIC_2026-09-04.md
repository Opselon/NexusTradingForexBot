# EXEC-QUALITY — Order Lifecycle & Execution Quality Forensics (2026-09-04)

Scope audited: `src/nexus_scalp/execution/*`, `application/live_engine.py` dispatch
paths, `risk/risk_engine.py`, `adapters/mt5/mt5_adapter.py`, `adapters/paper/`,
`training/walk_forward_trainer.py`, `research/splitting.py` + `oos.py`,
`labeling/triple_barrier.py`. Owner lanes respected (ML-dataset / live-persist /
temporal contracts untouched).

## Verdict

The lifecycle core is **structurally sound**. Broker truth wins (INV-011):
verified cancels, `_broker_close_verified`, `refresh_live_tickets_cache`
reconciliation, reconcile-missed-closes with 60s cadence + OPENED-unclosed
pre-check. Duplicates are guarded at three layers (experience idempotency key,
adapter pending-order fingerprint reuse, close/ambiguous-retcode re-check).
UNKNOWN broker state is never treated as success in the pending path
(`_pending_broker_state` ACTIVE/GONE/UNKNOWN; UNKNOWN keeps the exposure slot).

## Findings

| # | Finding | Severity | Status |
|---|---|---|---|
| F1 | **Primary dispatch path had no duplicate guard.** `execute_order` (hedge) blocked duplicate `order_id` via `_processed_orders`, but `dispatch_order` (the main market/pending entry path) did NOT record its `request_id`. A policy re-fire / duplicated decision / AI-reversal double-intercept could reach the broker twice under one request_id (silent order duplication; INV-005/006 surface). | HIGH | **FIXED** |
| F2 | **Ambiguous-fill recovery is fingerprint-weak on market orders.** `execute_market_order` treats ANY live (symbol,magic=888101) position as proof the ambiguous send filled. With MAX_TOTAL_EXPOSURE=1 this is tight in practice, but a manual/other-EA same-magic position or a just-closed-then-reopened window mis-attributes the ticket (no order↔position correlation key is sent; MT5 comment is constant `NSE_MARKET`). Adapter file = foreign lane (BUG-226/229 owner). | MEDIUM | Reported — recommend per-request comment correlation or `result.request_id` capture before relying on the recovery path at scale |
| F3 | **Remote gateway `send_order` idempotency key includes wall-clock ms** (`order_id + int(time()*1000)`) — every retry of the same decision gets a NEW key, so gateway-side dedupe is defeated. Remote adapter currently lacks `execute_market_order`/`place_pending_order` overrides (port defaults return 0 → primary path would no-op under remote mode). | MEDIUM (remote mode only) | Reported — key must be `order_id` alone; remote adapter needs the primary-path methods |
| F4 | **Hard-coded audit latencies.** `audit.log_order(latency=0.015/0.012/0.011/0.009)` — forensic latency fields are constants, not measurements. Real latency IS captured elsewhere (`_entry_fill_latency_ms` from dispatch→fill monotonic delta → experience `execution_latency_ms`), but the audit_orders rows mislead latency forensics. | LOW | Reported |
| F5 | **`is_risk_free_hit` SELL/BUY asymmetry** (order_manager ~L5410): BUY side accepts `final_sl >= entry` with `entry=0.0` default possible; SELL side requires `final_sl > 0.0` explicitly. Both require `exit≈final_sl`, so impact is bounded, but BUY rows with entry=0 defaults can mis-flag. | LOW | Reported |
| F6 | **Slippage/friction modeling is present and directional**: labeling uses per-bar spread-adjusted entries (`buy at ask`, `sell at bid`, step-dynamic spread), Almgren-Chriss impact guards in RiskEngine + position_intelligence (limit orders = zero taker impact), experience records signed `slippage_points` (adverse-positive for both directions) from expected vs actual fill + dispatch→fill latency. Research backtest friction = (spread+slippage ticks)×tick capped 0.5R. **Direction symmetry verified: no long-bias in the execution path** — BE lock, trailing, giveback, MAE/MFE, recovery budget all derive from `pos.type` / signed `pos.profit`. | OK | Verified |
| F7 | **Setup/strategy discrimination is wired end-to-end**: `setup_snapshot` (HTF/SMC/ICT/session/guardian) captured at dispatch → entry-context registry → autopsy row; experience `StrategyContext` = deterministic family (session/regime/volatility/trend/setup_type/confluence) with `strat_unavailable`/`strat_not_evaluated` sentinels (never fabricated). Short/long samples are symmetric in labels (3-class BUY/SELL taxonomy with dual-TP-spike neutralization) and in family attribution. | OK | Verified |
| F8 | **Walk-forward OOS is real and time-ordered.** `WalkForwardTrainer._split_fold_with_embargo`: [TRAIN \| PURGE(purge_gap=15) \| VALIDATION \| EMBARGO(embargo_bars=15)]; folds iterate chronological blocks; OOS predictions only from validation windows; online fine-tune purges the last `max_holding_bars` tail and rejects insufficient buffers (BUG-236 persist guard). Research tier: `split_temporal`/`walk_forward_folds` purge horizon-crossing train samples + embargo both boundaries (defaults purge=300s/embargo=60s, BUG-183) with a HARD OOS gate (negative expectancy ⇒ REJECTED). Not walk-forward-shaped: OOS window never overlaps train; scalers fit on train only. | OK | Verified (tests existed; fold-boundary geometry now pinned) |

## Fixes landed (this lane)

1. **`execution/order_manager.py` — duplicate-dispatch guard on `dispatch_order`.**
   Every `request_id` sent to the broker (market AND pending path, filled or
   refused) is recorded in `_processed_orders` after the adapter call; a repeat
   is refused with a warning BEFORE any broker I/O. Mirrors the existing
   `execute_order` guard; no clamp/exposure/lock constants touched.
2. **`tests/unit/test_execution_quality_contract.py` (NEW)** — 6 contract tests:
   EC-1 duplicate dispatch blocked (market + pending), EC-2 broker refusal is
   terminal (`ticket=0` recorded, terminal REJECTED_UNFILLED emitted), EC-3
   (split-fill family binding — covered by existing BUG-081 goldens, extended
   surface here), EC-4 hedge-path idempotency guard, EC-5 fold-split
   chronology/purge/embargo geometry incl. degenerate fold sizes.

## Verification

- `pytest` execution + lifecycle + temporal families: **269 passed / 0 failed**
  (order_manager exit bugs, order lifecycle, BUG-140 lifecycle, S1–S6 goldens,
  execution architecture, pending-recovery BUG-229, adaptive management,
  hardened protocol, autopsy fixes, research 09B + purge defaults BUG-183,
  walk-forward trainer, temporal sequence contract, new contract battery).
- `ruff check` + `ruff format --check` clean on touched files.
- `mypy` order_manager: same single pre-existing error in
  `model_generation/sequence.py:217` before and after change (exonerated via
  stash; not in this lane).

## Residual risks

- R1: F2/F3 need the MT5-adapter lane owner (per-request correlation comment /
  gateway idempotency-key fix) — cross-lane, not patched here.
- R2: `_processed_orders` grows unbounded in-process (one entry per decision;
  ~100 bytes/entry ⇒ negligible for session lifetimes; restart clears it, and
  the experience ledger remains the durable idempotency layer).
- R3: audit-order latency constants (F4) are cosmetic but pollute latency
  analytics until replaced with measured deltas.
