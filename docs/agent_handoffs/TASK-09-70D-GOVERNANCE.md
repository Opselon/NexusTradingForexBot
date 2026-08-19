# HANDOFF — TASK-09 70D CANDIDATE VALIDATION / GOVERNANCE READINESS

> Agent: Hermes-70DValidation (AGENT-09) · TASK-09-70D-CANDIDATE-VALIDATION · 2026-08-19
> Starting HEAD: 3f3f3d9 → observed swarm commits through e7586f9 (BUG-106 fix)
> Ending HEAD / pushed SHA: see git log -1 / git rev-parse origin/main

## WHAT THIS TASK ESTABLISHED

1. Real current state: HEAD==origin/main==e7586f9 at verification (BUG-106 fix committed
   by AGENT-05). Older reports' heads (67b77e5, 11e3402) superseded.
2. BUG-106: FIXED + committed (bounded 4000-bar window, O(n·4000)); independently
   benchmarked by TASK-09: 9.5x at idx 15000, quadratic→linear crossover.
3. Parity: GREEN (68 passed/3 skipped; real-data probe exact, 0 mismatches).
4. Dataset: real ds_d3f35b12d63148da (1146 rows, scalp_v3, hash aad73c8f…).
5. Champion: 9105cef7 restored-candidate state (BUG-104), unchanged; write-protection
   regression green.
6. Governance: 14-gate verify_candidate; preview/emergency controls import-verified;
   0 promotions; 0 governance_state rows.
7. BUG-108 fresh-DB migration: 38 passed.
8. Shadow: infra ready but NO_VALIDATED_CANDIDATE; 2 smoke rows only.
9. Verdict: NO_CANDIDATE → INSUFFICIENT_EVIDENCE → NOT_ELIGIBLE for promotion.
   NO PROMOTION performed or permitted by this task.

## CURRENT SOURCE OF TRUTH

- Branch main, HEAD e7586f9 (+ TASK-09 docs commit) — verify with git fetch + rev-parse.
- Active contract: scalp_v1 (50D) live; scalp_v3 (70D) canonical candidate contract;
  scalp_v4 (TASK-02 integration); scalp_liquidity_v1 (60D liquidity).
- Champion: artifacts/models/scalp/XAUUSD/v1.0.0/model.pt = 9105cef7… (RESTORED_CANDIDATE,
  operator decision pending per INV-015).
- Dataset: ds_d3f35b12d63148da (real, 1146 rows).
- Shadow: shadow70 runtime IDLE (NO_VALIDATED_CANDIDATE).

## DO NOT TOUCH (parallel owners)

- src/nexus_scalp/model_lifecycle/* (AI-Hub tensor diagnostics, BUG-110 — parallel WIP)
- src/nexus_scalp/features/liquidity_runtime.py, schema_v2.py (swarm WIP, modified in tree)
- tests/unit/test_model_lifecycle_phase10.py, test_liquidity_runtime_integration_phase18.py
- artifacts/models/scalp/XAUUSD/v1.0.0/* (Champion — read-only; operator decision pending)
- Web/, server.py shadow70/liquidity panels (TASK-02/05 UI WIP)

## NEXT-AGENT ACTIONS (the missing chain — what must happen before any promotion)

1. Train the real 70D candidate: CandidateTrainer (BUG-101 seed-before-model) on
   ds_d3f35b12d63148da (or a regenerated real dataset) → artifacts/model_generation/
   models/ (NEVER the Champion path).
2. Run fair A/B/C (TEST-70D-MODEL protocol, equal budgets/seeds/splits) + Purged
   Walk-Forward + OOS isolation + robustness + calibration.
3. Register the candidate lifecycle: DISCOVERED → VALIDATING → VALIDATED (governance
   registry; never Champion/Active).
4. Deploy to Shadow70 (attach via API /api/models/shadow70/attach + start); collect
   observations until the governance sample floor is met; verify lineage + disagreement
   taxonomy.
5. Run governance preview (read-only) — every required gate must be PASS.
6. STOP at PROMOTION_READY_FOR_OPERATOR. Human approval via POST
   /api/models/promotion/execute is a SEPARATE operator action (INV-015).
7. Re-verify BUG-108 on any fresh DB before release; keep quality gates green.

## DEPENDENCIES

- Training chain depends on: TASK-03 parity (GREEN), TASK-01 liquidity foundation
  (committed), TASK-02 integration (scalp_v4), BUG-106 fix (committed e7586f9).
- Shadow depends on: validated candidate (none yet) + shadow70 runtime (ready).
- Governance depends on: candidate evidence (none yet) + verify_candidate (ready).

## RISKS / KNOWN ISSUES

- ds_d3f35b manifest shows 1970-01-01 temporal_range — writer formatting artifact
  (flagged; real probe timestamps exact). Do not silently "repair" without checking
  the writer.
- Champion identity is RESTORED (not byte-original) — any future promotion must account
  for the operator decision on the Champion identity first.
- Working tree carries heavy parallel WIP (112 entries at last count) — commit only your
  own scope; re-verify staged names before every commit.

## EXACT NEXT-AGENT STARTUP

```bash
git fetch --prune && git status --branch --short && git log -10 --oneline
```
Read: docs/TASK-09-70D-CANDIDATE-VALIDATION-FINAL.md (this task), docs/BUG-106-PERFORMANCE-FIX.md,
docs/agent_handoffs/TASK-03-70D-PARITY.md, TASK-08-70D-GOVERNANCE.md,
docs/CHAMPION_ARTIFACT_INCIDENT_20260819.md. Then follow NEXT-AGENT ACTIONS above.
