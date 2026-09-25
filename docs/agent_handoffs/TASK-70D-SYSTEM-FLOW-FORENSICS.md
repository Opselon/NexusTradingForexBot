# TASK-70D-SYSTEM-FLOW-FORENSICS — Handoff

> Agent: Hermes-Forensic-70D · Role: Full-System Data/Worker/Chart Flow
> Auditor & Bug-Fix Agent · 2026-08-19

## 1. Task summary

Full-system forensic audit after the 60D/70D feature-architecture transition:
reconstructed the real Data/Worker/Chart flows from code, audited every
50D/60D/70D dimension assumption (263 hits classified), proved and fixed the
one verified post-70D wiring bug (BUG-105), created the flow docs, the bug
matrix, and this report.

## 2. Deliverables (all committed)

- docs/70D_SYSTEM_FLOW_FORENSIC_MAP.md — real Data/Worker/Chart flows +
  dimension classification + bug matrix + risks
- docs/70D_WORKER_FLOW_FORENSICS.md — 9-worker inventory + state machine +
  no-progress detection
- docs/70D_CHART_FLOW.md — broker-first chart + overlays + SSE
- docs/70D_DATA_FLOW.md — one market event end-to-end
- docs/70D_API_UI_FLOW.md — UI element → endpoint map + 200-but-wrong checks
- docs/70D_FULL_APPLICATION_FLOW.md — full app flow + error paths
- docs/70D_POST_MIGRATION_BUG_MATRIX.md — proven bugs + dimension audit
- docs/70D_FULL_SYSTEM_FORENSIC_FINAL_REPORT.md — final report
- scratch/repro_shadow70_hook_dead_code.py — BUG-105 proof
- scratch/trace_70d_vector_assembly.py — 70D assembly determinism
- artifacts/forensics/feature_vector_trace.json — per-index mapping
  (gitignored — regenerate via the scratch probe)

## 3. Bugs

| ID | Severity | Status |
| :--- | :--- | :--- |
| BUG-105 — 70D shadow hook dead code (inside except) + UnboundLocalError + early-return gate + empty schema hash | HIGH | FIXED (regression TEST-SHADOW-36..39; fix absorbed into 14fff5a) |
| BUG-106 — ledger entry for BUG-105 | MED | FIXED (ledger) |

Latent hardening (documented, NOT exploitable today):
- web/server.py attach_shadow70 ~4764: validation_result forcing (any
  non-"VALIDATED" status → VALIDATED_CANDIDATE; safe because rows are
  pre-filtered CHALLENGER)
- governance/evidence.py:284: drift-stat width capped at 50 (70D shadow has
  its own full-width monitor)

## 4. Git

- Fix absorbed into swarm commit 14fff5a (verified byte-for-byte).
- My docs commit 066a7ba pushed; origin/main == 2babe15 (in sync).
- Working tree at handoff: 107 WIP files belong to parallel agents
  (TASK-07/12 etc.) — untouched.

## 5. Tests

75 shadow70+parity · 34 inference/replay · 31 parity/dataset · 4 BUG-105
regressions · 105 combined run — all green at absorbed HEAD.

## 6. EXACT NEXT-AGENT INSTRUCTIONS

1. Register a valid 70D candidate (TASK-04 series) → then
   `POST /api/models/shadow70/attach` — the load gate + hook are ready.
2. Collect ≥30 real shadow70_observations, then SET the drift reference from
   the 70D training dataset (Shadow70DriftMonitor.set_reference) and re-run
   drift (severity INSUFFICIENT_EVIDENCE → real).
3. If touching attach_shadow70: map CHALLENGER → VALIDATED_CANDIDATE
   explicitly and reject every other lifecycle status (hardening above).
4. If touching governance/evidence: parametrize the drift width to
   len(feature_window[0]) instead of the 50 cap.
5. Before any live-forensics run: measure the full-engine latency envelope
   (p50/p95/p99) — not measured in this task (no live MT5 session).
6. Keep the 70D hook independent of the 50D shadow gate (BUG-105 discipline).
7. Run beforePush (full gate) before any release; extend
   test_shadow70_runtime.py for new hook behavior (never fork).
8. Report to Telegram in Persian with real counters only.

## 7. Final verdict

RELEASE_HEALTHY_WITH_WARNINGS — 70D code path proven correct; one wiring
bug fixed with regressions; real candidate + live performance envelope
pending the TASK-04 series.