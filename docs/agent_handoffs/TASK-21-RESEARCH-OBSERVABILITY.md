# TASK-21-RESEARCH-OBSERVABILITY — AGENT HANDOFF

Agent: Hermes-ResearchObs
Task: Strategy Research & Validation Engine deep observability — fully observable,
evidence-driven, phase-by-phase research laboratory (75-point spec).

Date: 2026-08-20
Branch: main
Starting HEAD: 7ce7198 (pre-swarm) — final verified HEAD: f43e48d (mine)
Remote: origin/main https://github.com/Opselon/NexusTradingForexBot

## Commits (all pushed)

- af7eb2d — research/evidence.py (gates/snapshots/events/evidence vault models)
  + research/observability.py (persistence facade) + models/init exports
- c26b175 — research observability tables (research_gates/research_events/
  research_evidence/research_run_snapshots/research_worker_heartbeat) + research_runs
  status columns
- b48f580 — static validation quality gate helper + gate cache fix + 13-col run insert
- 8136704 — pipeline gate observability wiring + registry validation invariants
- 7bf1ee7 — observability API endpoints (detail/trace/gates/events/evidence/worker/
  queue/preflight/retry/cancel/analytics/diagnostics) + worker heartbeat + LiveEngine
- 9d2b3f6 — research observability UI (health summary/worker & queue/diagnostics/
  strategy detail one-click trace + preflight)
- f43e48d — 24-test suite + fixes (block_gate result persistence, events ordered by
  insertion id, scoring gate started)

## Files

- NEW src/nexus_scalp/research/evidence.py — domain models
- NEW src/nexus_scalp/research/observability.py — persistence facade
- MOD src/nexus_scalp/research/pipeline.py — per-gate observability wiring
- MOD src/nexus_scalp/research/registry.py — invariant_check
- MOD src/nexus_scalp/research/worker.py — heartbeat
- MOD src/nexus_scalp/research/models.py — ResearchRun status fields
- MOD src/nexus_scalp/research/__init__.py — exports
- MOD src/nexus_scalp/adapters/database/audit_repository.py — observability tables
- MOD src/nexus_scalp/application/live_engine.py — observability store wiring
- MOD src/nexus_scalp/web/server.py — 12 new API endpoints
- MOD Web/index.html, Web/app.js — observability UI
- NEW tests/unit/test_research_observability_phase21.py — 24 tests
- MOD agents/skill.md, agents/contracts.md, agents/taskboard.md — registries
- NEW scratch/probe_research_observability_smoke.py — end-to-end smoke probe

## Architecture

- ResearchGate / ResearchRunSnapshot / ResearchEvent / EvidenceArtifact / OutcomeLineage
  first-class entities with explicit statuses
- Gate chain: STATIC_VALIDATION -> BACKTEST -> WALK_FORWARD -> OOS -> ROBUSTNESS -> SCORING
- research_runs append-only with status/run_outcome/snapshot_id/gates/completed_at
- Worker heartbeat -> HEALTHY/DEGRADED/STUCK/FAILED classification
- Registry invariants: VALIDATED needs all gates + evidence; REJECTED needs a failed gate
- No auto-promotion to ACTIVE (inherited + re-verified)

## New invariants

- INV-T21-01: A strategy is VALIDATED only when BACKTEST/WALK_FORWARD/OOS/ROBUSTNESS
  PASSED + SCORING PASSED + evidence artifacts exist.
- INV-T21-02: A strategy is REJECTED only after a gate FAILED or a terminal verdict;
  unprocessed strategies stay DISCOVERED.
- INV-T21-03: Research runs are append-only; a new run_id never overwrites prior runs.
- INV-T21-04: RESEARCH failures (statistical) are never retried via the retry endpoint;
  only TECHNICAL/DATA-classified gates may be retried.

## Runtime verification

- Smoke probe: full pipeline with observability -> 6 gates PASSED, run COMPLETED,
  evidence x5, snapshot hash stable, worker health HEALTHY after beat.
- 24 unit tests green (test_research_observability_phase21.py).
- ruff check/format clean on all my files; mypy clean on research/.
- Full unit suite running at handoff; parallel-agent WIP in audit_repository.py
  (DatabaseDriver attrs) may fail mypy for THEIR files, not mine.

## Known risks / unfinished

- The parallel swarm refactored web/server.py research API mid-task; my endpoints
  were re-spliced on top of their structure; `_research()` helper re-added.
  Verify no duplicate routes at next rebase.
- scratch probe kept for reproduction.
- Registry `research_runs` old rows lack new columns (defaults ''/0) — backward
  compatible by construction (COALESCE-free reads).

## EXACT NEXT-AGENT INSTRUCTIONS

1. Verify origin/main has all 7 commits above (git log --oneline -7).
2. Run the full unit suite: `.venv/Scripts/python.exe -m pytest tests/unit/ --ignore=tests/unit/test_docker_startup_phase21.py -q`.
3. If the swarm's audit_repository.py Driver-attr mypy errors persist, do NOT fix
   them (not ours) — report only.
4. UI verification: open the Strategy Research tab; expand a registry row; click
   Trace to see the detail panel with gates/timeline/evidence; click Preflight.
5. Registry row convention: keep rows additive, never rewrite others.