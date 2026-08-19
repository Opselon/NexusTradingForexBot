# TASK-13 Handoff — Incident Runtime Activation & Forensic Recovery

- **Agent**: AGENT-13 (Hermes-Forensic-13)
- **Role**: Incident Response Runtime Integration & Forensic Recovery Engineer
- **Task**: TASK-13-INCIDENT-RUNTIME-ACTIVATION
- **Final HEAD**: see git log (steps absorbed into parallel commits
  7a3528c/716c458); remote verified at each step

## What landed

| Step | Deliverable | Status |
| :--- | :--- | :--- |
| STEP-01/02 | IncidentWorker wired into live_engine (60s, to_thread, off tick path) + structured telemetry (collector + emit) | ABSORBED (parallel commit) |
| STEP-03 | Worker state machine (STARTING/RUNNING/DEGRADED/STOPPING/STOPPED/FAILED) + latency p50/p95/p99 + useful-work telemetry | ABSORBED |
| STEP-04 | Incident Center: worker health row, Accounting Audit / Timebase Probe buttons, Export Report / Evidence ZIP | ABSORBED |
| STEP-05 | AccountingForensicsEngine: first-divergence + classification + recovery candidates (read-only) | ABSORBED |
| STEP-07 | TimebaseProbe: host/DB/broker offsets + HISTORY_QUERY_ERROR classification | ABSORBED |
| STEP-08 | Telegram wired (CRITICAL/HIGH throttled) + end-to-end verification (1 alert, dedup works) | VERIFIED |
| STEP-09 | Export/ZIP endpoints + UI buttons + secret-masking verification | ABSORBED |
| STEP-10 | TEST-INCIDENT-RUNTIME-01..20 (26) + TEST-ACCOUNTING-01..08 + TEST-TIMEBASE-01..08 (17) | ABSORBED |
| BUGs | **BUG-115** (PROVEN accounting root cause) | APPENDED |

## Key evidence

- **151 ledger zero-PnL rows** — first divergence: LEDGER; classification
  RECONSTRUCTION_FAILURE ×151; 151 recovery candidates (RECOMMENDED only).
- **32 zero outcomes** — all RECOVERABLE_FROM_BROKER.
- **Timebase** — DB clock healthy (−0.7s); broker skew = bulk sync-lag
  (HISTORY_QUERY_ERROR); no live clock defect.
- **Telegram** — one real test alert delivered; repeat suppressed.

## Files

- `src/nexus_scalp/incidents/{accounting,telemetry,timebase,worker}.py`
- `src/nexus_scalp/web/server.py` (health worker state, forensics, report/zip)
- `src/nexus_scalp/application/live_engine.py` (worker lifecycle + telemetry)
- `tests/unit/test_incident_runtime_task13.py`, `test_incident_accounting_timebase_task13.py`
- `artifacts/forensics/{accounting_divergence,timebase_probe}.json`
- `agents/bugs.md` (BUG-115), `docs/TASK-13-INCIDENT-RUNTIME-FINAL.md`

## Registries

- bugs.md: BUG-115 PROVEN. Contracts: INCIDENT_RESPONSE v1 unchanged.
- runtime_invariants: INV-019 stands (diagnostic-only; recovery governed).

## Known risks / unfinished

- Governed recovery for the 151 rows is NOT executed (by design). TASK-14 or
  an operator-approved repair must: reconcile via append-only metadata,
  rebuild the 117 experience-no-outcome rows, guard split-fill double-count,
  re-run research dataset.
- Live-session timebase stability unverified across a full trading day.
- Parallel WIP continues (reports.py umask from CodeQL agent — not mine).

## EXACT NEXT-AGENT INSTRUCTIONS (TASK-14)

1. **Governed accounting repair** (highest value): implement
   `reconcile_zero_pnl_ledger()` using the 151 RECOMMENDED candidates
   (artifacts/forensics/accounting_divergence.json) with:
   - append-only reconstruction metadata (original_value, recovered_value,
     reconstruction_source, algorithm_version, timestamp, confidence);
   - operator approval gate (CLI `nexus accounting reconcile --approve`);
   - split-fill family protection (one economic execution → one outcome);
   - idempotent re-run + verification (financial aggregates unchanged or
     corrected exactly once);
   - regression tests TEST-ACCOUNTING-01..08 extended to the repair path.
2. **Close-path fix** (BUG-115 part b): when `reconstruction_source="NONE"`,
   persist the ledger row flagged UNKNOWN (net_pnl_usd NULL or UNKNOWN flag)
   instead of 0.0 — regression-tested; coordinate with order_manager owners
   (execution path — lock-aware).
3. **Research dataset rebuild**: re-run the dataset build so the 32 recovered
   outcomes + 117 new outcomes enter research eligibility; verify candidate
   discovery is no longer zero-deprived.
4. **Post-repair incident update**: mark INC-2026-D5659C10 RECOVERED with
   evidence; verify /api/diagnostics/health totals reflect the repair.
5. **Live-session timebase monitor**: run the TimebaseProbe during an active
   trading session (broker server time vs host UTC) to confirm
   HISTORY_QUERY_ERROR holds live; extend to SSE/UI timestamps.
6. Keep the diagnostics API read-only; never auto-apply recovery.
7. Run beforePush gate; report BUG/commit/regression linkage per contract.

## Final verdict

**INCIDENT_RUNTIME_ACTIVE_WITH_ACCOUNTING_FINDINGS**