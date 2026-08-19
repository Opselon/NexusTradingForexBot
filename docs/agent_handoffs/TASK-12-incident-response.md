# TASK-12 Handoff — Forensic Incident Response & Root-Cause Automation

- **Agent**: Hermes-Forensic-12 (AGENT-12)
- **Role**: Incident Correlation / Root-Cause / Recovery Diagnostics Engineer
- **Task**: TASK-12-FORENSIC-INCIDENT-RESPONSE
- **Starting HEAD**: `c56d334` — **Ending HEAD**: see git log (commit after this doc)

## Executive summary

Built the incident-response layer on top of TASK-11's permanent monitoring:
a canonical incident model, a correlation engine that turns symptoms into
causal chains, value-lineage tracing that finds the FIRST divergence, WHY
workflows for the historical failure classes, impact analysis, non-destructive
quarantine, approval-gated recovery plans, machine+human incident reports with
secret masking, a read-only diagnostics API, a Forensic Incident Center web
tab, throttled Telegram CRITICAL/HIGH alerts, and a bounded background worker.
Everything is diagnostic-only: TEST-INCIDENT-34/35 prove the layer cannot
mutate trading, risk, models, or accounting.

## Deliverables

| Artifact | Purpose |
| :--- | :--- |
| `src/nexus_scalp/incidents/` (10 modules) | incident model/store/correlator/lineage/trace/impact/reports/worker/telegram |
| `src/nexus_scalp/database/registry.py` | AUDIT-0006 additive migration (incidents/events/traces/quarantine tables) |
| `src/nexus_scalp/cli/incident_commands.py` | `nexus incidents list/show/search/stats/report/export/scan/trace-why/lineage` |
| `src/nexus_scalp/web/server.py` | GET /api/diagnostics/{incidents,incidents/{id},health,lineage,search} |
| `Web/index.html`, `Web/app.js` | Forensic Incident Center tab (summary cards, incident list, one-click trace, detail) |
| `tests/unit/test_incident_response_task12.py` | TEST-INCIDENT-01..35 (62 tests) |
| `tests/integration/test_diagnostics_api.py` | diagnostics API round-trip + read-only enforcement |
| `docs/70D_INCIDENT_RESPONSE_MODEL.md` | canonical incident structure |
| `docs/70D_INITIAL_INCIDENT_FORENSIC_REPORT.md` | read-only real-data baseline (spec 56/57) |
| `docs/70D_INCIDENT_RESPONSE_FINAL_REPORT.md` | final report (spec 62) |

## Key findings (read-only real-data baseline)

1. **ACCOUNTING_DIVERGENCE (CRITICAL)**: 237 mapped broker/ledger PnL
   divergences; 151 ledger rows net_pnl_usd=0 while broker PnL ≠ 0;
   32 outcomes (2026-08-17) zero-PnL with no reconstruction_source and
   broker truth available → SUSPECT_OUTCOME. Root cause PLAUSIBLE (pre-
   BUG-045-era rows), NOT PROVEN — needs governed code audit.
2. **TIMEBASE_DIVERGENCE (HIGH)**: measured skew −5.1…−5.9 h on recent rows
   (batch backfill); stored timestamps UTC-normalized.
3. **OUTCOME_TO_RESEARCH_DROP (MEDIUM)**: research_runs=0; experience→outcome
   linkage healthy (74/74).
4. **SPLIT_FILL_GROUPING (MEDIUM)**: 23 families (one order, many tickets).
5. Healthy: no News all-neutral, no stale bundle, no migration drift
   (audit v7, integrity ok), Champion intact.

## Contracts (new, in agents/contracts.md)

- INCIDENT_RESPONSE v1 · INCIDENT_CORRELATION v1 · VALUE_LINEAGE v1 ·
  RECOVERY_GOVERNANCE v1 · AUDIT-0006 migration.

## Invariant added (agents/runtime_invariants.md)

- INV-019 — incident layer is diagnostic-only; recovery approval-gated;
  quarantine never deletes; no hot-path analysis.

## Quality gates

- ruff check: clean on incidents/, cli, server, registry, tests.
- ruff format: applied.
- mypy: Success on incidents/ + incident_commands.py.
- pytest tests/unit/test_incident_response_task12.py: 62/62.
- pytest tests/unit/test_database_migrations_phase18.py: 38/38 (audit v7).
- pytest tests/integration/test_diagnostics_api.py: 2/2.
- Full unit suite: see git/CI run (pre-existing parallel-agent failures
  documented: liquidity_task02/shadow70/70d_model_validation/behavior
  phase16 are other agents' WIP tests).

## Bugs

- NO new BUG-NNN: the baseline findings are incidents (INC- records) with
  PLAUSIBLE root causes — per contract only PROVEN issues enter bugs.md.
  The 151-zero-ledger finding is the highest-value follow-up.

## Known risks

- The 151 zero-PnL ledger rows need a governed audit of the outcome-recovery
  path (reconstruction source) — a future task, NOT auto-repaired.
- Live session timebase still UNKNOWN (needs a live MT5 probe).
- The IncidentWorker is built and tested but NOT yet wired into live_engine
  (by design: wiring is TASK-13's first action, off the tick path).
- Parallel agents may bump audit schema past v7 — the migration test expects
  `current == expected`, which adapts automatically.

## EXACT NEXT-AGENT INSTRUCTIONS (TASK-13)

1. **Wire the IncidentWorker into live_engine** (observability hook only):
   - construct `IncidentWorker(IncidentStore(db_path=audit))` next to the
     hygiene worker (live_engine `__init__`, ~line 195);
   - call `worker.tick()` in the run_loop periodic section via
     `asyncio.to_thread`, interval default 300s, failure-isolated
     (mirror the TASK-11 hygiene pattern at live_engine ~1086);
   - add `_stop_incident_worker` to shutdown; NEVER touch the tick path.
2. **Feed the worker real telemetry**: subscribe the incident worker to
   structured log events ([TELEGRAM]/[DB_HYGIENE]/[INTELLIGENCE_WORKER]/
   [EXECUTION_RECONCILIATION] error classes) via a bounded collector —
   convert to `TelemetryEvent` dicts and `worker.ingest()`.
3. **Persist the baseline incidents**: run
   `nexus incidents scan --write` once to record the 5 baseline incidents
   (1 CRITICAL accounting divergence, 1 HIGH timebase, 3 MEDIUM) as canonical
   rows; then verify /api/diagnostics/health and the Incident Center tab.
4. **Governed audit of the 151 zero-PnL ledger rows** (highest value):
   - code-level audit of `experience/outcome_recovery.py` reconstruction
     path; determine why realized_pnl_usd stayed 0 for tickets with broker
     PnL (2026-08-17 era); if PROVEN, open BUG-NNN linked to the
     ACCOUNTING_DIVERGENCE incident; the incident engine never rewrites —
     reconciliation must be a separate approved flow.
5. **Live timebase probe**: run a read-only MT5 probe comparing broker
   server time vs host UTC during an active session to resolve
   TIMEBASE_DIVERGENCE from UNKNOWN to PROVEN/HIGH_CONFIDENCE.
6. **Telegram**: wire `IncidentTelegramNotifier` to the live notifier so
   CRITICAL/HIGH incidents alert (throttled); verify one alert end-to-end.
7. **Incident console polish**: add incident export/ZIP buttons to the Web
   tab (backend endpoints exist; JS calls /api/diagnostics/*).
8. Keep additive: never rewrite incidents rows from earlier baselines;
   append new incidents; mark older ones CLOSED/FALSE_POSITIVE with
   evidence when resolved.
9. Run beforePush (quality gate) and report BUG/commit/regression-test
   linkage per the multi-agent git contract.

## Registries touched

- agents/contracts.md (4 new contract rows), agents/runtime_invariants.md
  (INV-019), agents/change_control.md (CHG-0017), agents/taskboard.md
  (TASK-12 row), docs/70D_*.md (3 new docs).