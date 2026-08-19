# TASK-11 Handoff — 70D Canonical Candidate

> Agent: AGENT-11 · TASK-11-70D-CANONICAL-CANDIDATE · 2026-08-19
> Result: **70D_CANDIDATE_NOT_ELIGIBLE** (honest OOS evidence)

---

## 1. Delivered

1. **SCHEMA RECONCILIATION** — `scalp_v3` canonical (dim 70, hash
   `235b8fccc96b7e0e`); `scalp_v4` legacy-blocked. Doc:
   `docs/70D_SCHEMA_RECONCILIATION.md`. Tests TEST-SCHEMA-70D-01..08
   (`tests/unit/test_schema_70d_reconciliation.py`).
2. **ACTIVE-RUNTIME CANONICALIZATION** — liquidity_runtime.SCHEMA_70D +
   web/server.py error path + snapshot indices → scalp_v3/60..69;
   integration expectations updated (47 tests green).
3. **FAIRNESS-GATE CORRECTION** — TEST-70D-MODEL-01/17 now assert
   timestamp+label identity across schema arms (sample_id embeds
   feature_schema_id); `_has_70d_artifact` recognizes canonical scalp_v3.
   TASK-4 suite: 33 passed.
4. **REAL DATASET VERIFIED** — ds_task5_real70d_2500 (2,446 real XAUUSD
   M5 rows 2025-03-12..03-25, scalp_v3 hash, 70/70 finite in [-3,3],
   news family honest zero/FEATURE_DISABLED, source continuity validated).
5. **WALK-FORWARD EVIDENCE** — canonical 4-fold purged WF on the real
   dataset: OOS PF=0.37 / win 23.7% → NOT_ELIGIBLE (negative OOS floor).
   Evidence frozen in scratch/task11_3_* (wf_candidate dir is swarm-
   contended; a parallel agent overwrote the artifact post-run).

## 2. Commits (pushed, origin verified)

- 8635c66 — canonicalize 70D schema (scalp_v3 everywhere active)
- 45515d5 — fairness-gate semantics + _has_70d_artifact fix
- d52b893 — walk-forward evidence (NOT_ELIGIBLE)

## 3. Honest limitations

1. 70D candidate fails OOS on the current small real sample → NOT_ELIGIBLE.
   A larger real M5 slice (the 100k source exists) or news-overlapping
   window could produce different evidence — next agent's decision.
2. Champion remains RESTORED_CANDIDATE (BUG-104 operator decision).
3. wf_candidate dir contention with the swarm — use a unique artifact dir
   per run.

## 4. Exact next-agent instructions

1. Operator: resolve BUG-104 Champion decision (restore or approve retrain).
2. If a bigger 70D dataset is wanted: build from data/raw/XAUUSD_M5.parquet
   (100k rows) with the CHUNKED builder (scratch/btwf_build_70d.py pattern —
   the full-history liquidity call is O(n²); chunk + checkpoint; use the
   time_utc column, never the int epoch as microseconds).
3. Re-run walk-forward on the bigger dataset; only if OOS passes the
   floors → run robustness + calibration, then register + shadow.
4. Keep scalp_v3 canonical; never reintroduce scalp_v4 as ACTIVE_RUNTIME.
5. Re-run: TEST-SCHEMA-70D-01..08 + test_70d_model_validation_task4 (33)
   + test_70d_contract_parity_task3 (14) + liquidity API (9) before push.

## 5. Traceability

- TASK-ID: TASK-11-70D-CANONICAL-CANDIDATE (AGENT-11)
- Reports: docs/70D_SCHEMA_RECONCILIATION.md,
  docs/TASK-11-70D-CANONICAL-CANDIDATE-FINAL.md
- Evidence: scratch/task11_1_schema_reconciliation_probe(.out),
  task11_2_build_real_70d_dataset(.out), task11_3_walkforward_70d(.out,
  _result.json)
- Root: HEAD 3f3f3d9 → pushed 8635c66..d52b893 on main