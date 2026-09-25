# src/nexus_scalp/governance/lock.py

- PURPOSE: Cross-process promotion serialization (TASK-08 spec 37) —
  two agents/processes must not promote simultaneously. Exclusive-create
  lock file (same pattern as the DB migration engine) + process-local
  re-entrant guard; a concurrent attempt reports PROMOTION_CONFLICT
  instead of a partial overwrite.
- ARCHITECTURE LAYER: Domain (concurrency control).
- RESPONSIBILITY: PromotionLock (try_acquire/release, stale reclaim),
  PromotionLockError.
- DEPENDENCIES: os, time, pathlib, logging. No DB.
- CONNECTS TO: governance.transaction.execute_promotion_transaction
  (locked step), migration engine pattern.
- KEY CONCEPTS:
  - Acquisition is O_CREAT|O_EXCL file creation containing the PID —
    the OS guarantees atomicity; FileExistsError means someone else
    holds it.
  - CRASH SAFETY: _reclaim_if_stale — reads owner PID; if the PID is
    dead (os.kill(pid, 0) probe, Windows-OK per comment) OR the lock is
    older than LOCK_STALE_AFTER_SEC=120s, the lock file is unlinked and
    the caller retries. Distinguishes dead-owner (reclaim) from live
    long-running promotion (no reclaim despite age).
  - release() unlinks only when we acquired (idempotent, never deletes
    someone else's lock — acquired flag guards).
  - Context-manager friendly: __enter__ tries acquire, __exit__ releases.
  - Lock lifetime is short, bounded by the promotion transaction itself.
- HOT PATH / PERFORMANCE: not on tick path; one open/write/close per
  promotion (~µs).
- EDGE CASES & PITFALLS: PID reuse could reclaim a live lock whose owner
  died and the PID was recycled — mitigated by the 120s age bound only
  for the "alive" branch (alive PID → never reclaimed); on Windows the
  os.kill(0) probe is documented as OK; unlink races are best-effort
  (missing_ok). A stale lock with an ALIVE but unrelated PID is never
  reclaimed — promotions may be blocked until manual removal.