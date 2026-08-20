# src/nexus_scalp/adapters/database/broker_history.py

- **PURPOSE:** Broker history reconstruction — converts raw MT5
  history orders/deals into `LogicalTrade` objects (the canonical
  open→close trade lifecycle reconstructed from broker deal pairs),
  powering accounting-from-history and the trade lifecycle forensics.
- **ARCHITECTURE LAYER:** Adapters (history analytics over broker data;
  read-only, never writes financial truth).
- **RESPONSIBILITY:** (a) fetch + normalize history orders/deals;
  (b) match opening and closing deals into logical trades (by order id /
  position id / volume pairing); (c) `LogicalTrade` — the reconstructed
  lifecycle (entry ticket, exit ticket, prices, volumes, PnL
  decomposition, commissions/swaps, timestamps UTC).
- **DEPENDENCIES:** sqlite rows (broker history tables), providers
  snapshot types, logging.
- **CONNECTS TO:** BrokerHistorySyncWorker, accounting reconstruction,
  MT5 history APIs, tests (test_mt5_history_reconstruction,
  test_mt5_accounting_from_history).
- **KEY CONCEPTS:** Reconstruction is the recovery path when live
  capture missed data (restart gaps, pre-BUG-045 migration era) — the
  logic must handle: partial fills, split fills, netting vs hedging
  accounts, closes without explicit open (orphan legacy rows are
  EXPECTED and preserved, never deleted). Timestamps normalized UTC
  throughout.
- **EDGE CASES & PITFALLS:** A missing close leg → logical trade
  incomplete (flagged, not fabricated); commission/swap attribution must
  match the broker's deal records exactly (the ledger's net PnL is
  broker truth, not estimated); dedup on (ticket, order_id) pairs to
  survive repeated sync passes.