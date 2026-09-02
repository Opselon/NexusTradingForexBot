# TASK-7 — Exit Intelligence / Position Management / Adaptive Risk Protection
# Baseline capture — 2026-08-18 (session start). READ-ONLY evidence.
# Sole purpose: prove the working-tree state at task start and document TASK-2/3/4 parallel WIP so nothing is clobbered.

## Parallel-agent WIP present at baseline (DO NOT touch; commit outside TASK-7)
- agents/change_control.md  : CHG-0001 (Hermes-Behavior, TASK-2, IMPLEMENTING), CHG-0002 (Hermes-TradeLifecycle, TASK-3, IMPLEMENTING)
- agents/taskboard.md       : TASK-2/TASK-3/TASK-4 rows (row edits by other agents)
- src/nexus_scalp/intelligence/behavior.py, models.py, reporting/engine.py, reporting/models.py : TASK-2 work
- src/nexus_scalp/adapters/database/audit_repository.py : TASK-2 schema additions (behavior_analysis, anomaly_events)
- tests/unit/test_behavior_anomaly_intelligence_phase16.py : TASK-2 tests
- scratch/task4_* files, docs/task5_champion_baseline.json, scratch/task5_* : TASK-4/TASK-5 probes

## TASK-7 scope (this session)
- exit-decision traceability (final-reason capture, no silent HOLD)
- protective-SL monotonic invariant (SL may only move in protective direction)
- closed-position guards (no protective modification for broker-gone tickets)
- broker-verified close ordering (verify position gone BEFORE freed exposure/duplicate ops)
- BE/trailing dispatch verification truthfulness (no pseudo-verification notifications)
- hold/protection score forensics (PROFIT_SHIELD floor cannot suppress higher-priority safety)
- BE trigger distribution measurement + BE-state broker verification
- order-manager performance instrumentation (O(1) hot path)

## Baseline git state (pre-TASK-7 edits)
HEAD: b7f4a3f Hermes-Accounting: Performance Intelligence reporting + parallel-agent work snapshot