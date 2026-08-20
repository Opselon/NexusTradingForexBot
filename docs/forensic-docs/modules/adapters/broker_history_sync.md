# src/nexus_scalp/adapters/database/broker_history_sync.py

- **PURPOSE:** Background broker-history synchronization worker —
  periodically pulls history orders/deals from the broker and reconciles
  them into the local tables so accounting/forensics stay current even
  when live capture missed events.
- **ARCHITECTURE LAYER:** Adapters (background sync; off the tick path).
- **RESPONSIBILITY:** (a) `start/stop` lifecycle (restart-safe);
  (b) `tick()` — the periodic sync step (idempotent; `_sync_once`
  bounded fetch + upsert); (c) `_warm_accounting` — kick the accounting
  core's derived refresh after new history lands.
- **DEPENDENCIES:** AuditRepository (target tables), broker adapter
  (history providers), accounting core, logging.
- **CONNECTS TO:** LiveEngine (owned worker), accounting, forensics,
  tests (test_mt5_database_persistence, test_mt5_accounting_from_history).
- **KEY CONCEPTS:** Idempotency: the same history rows fetched twice must
  upsert (dedup keys), never duplicate; the worker is monotonic-gated
  (no tight loop) and failure-isolated (a failed sync logs + retries next
  tick).
- **EDGE CASES & PITFALLS:** Time windows: history fetch window must be
  bounded per pass (memory + API limits); broker history calls are
  synchronous — the worker runs in its own thread, never on the tick
  loop; the warm-accounting kick must be idempotent too (a double kick is
  a no-op for AccountingCore's rebuild).