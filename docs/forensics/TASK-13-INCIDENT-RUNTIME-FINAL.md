# TASK-13 — Incident Runtime Activation + Accounting Forensics + Timebase Resolution — FINAL

> Agent: AGENT-13 (Hermes-Forensic-13)
> Task: TASK-13-INCIDENT-RUNTIME-ACTIVATION
> Role: Incident Response Runtime Integration & Forensic Recovery Engineer

## CURRENT GIT

- HEAD: `41be7a8` Hermes-AIHubForensic STEP-08 (at start of final phase)
- Final HEAD / origin/main: see git log (TASK-13 steps absorbed into parallel
  commits 7a3528c AGENT-12 STEP-06/09 + 716c458 AGENT-12 STEP-01/02)
- Remote-verified: all pushes verified (git log origin/main == HEAD at each step)
- Parallel WIP: Web/app.js, agents/bugs.md (BUG-113+), reports.py umask
  (CodeQL), scratch probes — never touched/committed by AGENT-13
- TASK-13's own commits: STEP-01..04 absorbed; working-tree deltas then
  committed as AGENT-13 steps

## INCIDENT WORKER

- State machine: STARTING → RUNNING → DEGRADED (≥2 consecutive failures) →
  FAILED (≥5); STOPPING → STOPPED. DEGRADED still ticks (recovers).
- Health (spec 39): last_start / last_success / last_failure /
  last_useful_work / cycle_count / queue_size / incidents_created /
  incidents_deduplicated / latency p50/p95/p99.
- Coverage: wired into live_engine run_loop (60s throttle, asyncio.to_thread,
  off tick path), lazily constructed (`_ensure_incident_worker`), stopped in
  shutdown (`_stop_incident_worker`), fed by `emit_incident_telemetry()` from
  engine error handlers (MT5 connect fail, startup reconciliation fail, tick
  stall, position-track failure).
- Latency: cycle p50/p95/p99 tracked; bounded ≤2000 events, ≤50 saves/cycle.

## ACCOUNTING (BUG-115 — PROVEN)

| Metric | Value |
| :--- | :--- |
| Zero-PnL ledger rows (broker PnL ≠ 0) | **151** |
| First divergence stage | LEDGER (broker correct) |
| Classification | RECONSTRUCTION_FAILURE ×151 |
| Zero-outcome rows | **32** (all RECOVERABLE_FROM_BROKER) |
| Experience-present-no-outcome | 117 |
| Recovery candidates (read-only) | **151** |
| Artifact | artifacts/forensics/accounting_divergence.json |

**Root cause (PROVEN)**: `order_manager` close path — when the broker deal
wasn't visible locally at close time, `reconstruct_broker_outcome()` returned
`reconstruction_source="NONE"` with `net_pnl_usd=0.0`; the caller kept
`profit_usd=0.0` and persisted it as the FINAL ledger value. The later
broker-history sync filled `audit_broker_trades` with real PnL but NO
post-sync ledger reconciliation exists. First divergence is at the LEDGER
write, downstream of a reconstruction fallback — not the broker sync.

**Recovery (governed, NOT executed)**: 151 RECOMMENDED candidates with
original/recovered value, source BROKER_DEALS, confidence 0.95, algorithm
version `agent13-reconcile-v1`. Any repair requires operator approval +
append-only reconstruction metadata (spec 18/19) + regression tests
(TEST-ACCOUNTING-01..08) + double-count protection (split fills).

## TIMEBASE

| Measure | Value |
| :--- | :--- |
| host_to_db | **−0.7s** (DB clock = correct UTC) |
| host_to_broker (median, 12h window) | −9.0h (11 live rows) |
| host_to_broker (all synced) | −23.2h (bulk backfill) |
| Classification | **HISTORY_QUERY_ERROR** (sync-lag, not live clock bug) |
| Artifact | artifacts/forensics/timebase_probe.json |

**Conclusion**: no live timebase defect. The −5.1…−5.9h TASK-12 observation
was the broker-history bulk sync lag. DB timestamps are UTC-correct; the
TIMEBASE_DIVERGENCE incident is resolved HIGH_CONFIDENCE →
HISTORY_QUERY_ERROR explanation (not a code fix needed; matching/history
queries remain the only surface where lag matters).

## TELEGRAM

- Wired: `IncidentWorker(telegram_notifier=engine.notifier)` — CRITICAL/HIGH
  alert automatically, throttled per incident (900s cooldown / 3600s repeat).
- **Verified end-to-end**: deterministic synthetic incident sent exactly ONE
  alert; repeat suppressed (dedup) — no spam. Message includes incident_id,
  severity, component, operation, root cause, confidence, first/last seen,
  repeat_count, impact, correlation_id. Secrets never included.

## EXPORT

- `/api/diagnostics/incidents/{id}/report` — JSON+Markdown (secret-masked).
- `/api/diagnostics/incidents/{id}/zip` — evidence ZIP (secret-masked,
  verified no bot tokens/API keys in content).
- Web buttons: Export Report + Evidence ZIP in the Incident Center.

## BUGS

- **BUG-115** (PROVEN) — Zero-PnL ledger rows from NONE-fallback
  reconstruction persisted as final (151 real broker tickets). Evidence:
  reproducible query + code-path audit + 151-row artifact.
- No unproven hypothesis entered the ledger (TEST-INCIDENT-RUNTIME-19/20).

## TESTS

- `test_incident_runtime_task13.py` — 26 (TEST-INCIDENT-RUNTIME-01..20)
- `test_incident_accounting_timebase_task13.py` — 17 (TEST-ACCOUNTING-01..08,
  TEST-TIMEBASE-01..08)
- Existing: 62 response + 2 integration + 38 migration + 16 live_state
- **161 incident/migration/live tests green**; ruff clean; mypy clean.

## PERFORMANCE

- Worker: off tick path (asyncio.to_thread), ≤2000 events/cycle, ≤50
  saves/cycle, no network I/O on tick, Telegram async queue.
- PROVEN no hot-path impact: live_state contract suite green; worker has no
  execution/risk imports (TEST-INCIDENT-RUNTIME-02).

## REMAINING RISKS

- **PROVEN**: BUG-115 root cause; TIMEbase = sync-lag (no live bug);
  151/32 forensics complete; Telegram delivery/dedup verified.
- **NOT PROVEN**: none outstanding for the accounting finding.
- **UNKNOWN**: live-session broker offset stability across a real trading day
  (probe ran on a quiet window); whether the 117 experience-no-outcome rows
  will create outcomes after recovery (governed decision).

## FINAL VERDICT

**INCIDENT_RUNTIME_ACTIVE_WITH_ACCOUNTING_FINDINGS** — the incident runtime is
live-wired with real telemetry; accounting divergence is PROVEN with a
governed recovery path ready (not executed); timebase is resolved as
sync-lag (no code defect).