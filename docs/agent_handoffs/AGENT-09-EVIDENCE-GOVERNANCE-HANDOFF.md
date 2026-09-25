# TASK-09-70D-GOVERNANCE — HANDOFF

**Agent:** AGENT-09 (Hermes-70DValidation)
**Role:** 70D Validation Completion / Candidate Lifecycle / Governance Readiness
**Date:** 2026-08-19
**Branch:** `main`

---

## Summary

Executed the missing evidence-chain steps on top of the parallel AGENT-09
documentation phase (NO_CANDIDATE verdict). This session:

1. **BUG-106 verified FIXED** (bounded 4000 + incremental alternative,
   byte-identical parity 0 diffs/24220 cells, profile evidence).
2. **Manifest pipeline fixed (BUG-117)** — governance provenance fields now
   written top-level by CandidateTrainer; ag09_oos_C_v1 passes every
   technical gate.
3. **TRUE temporal OOS executed** — the 70D candidate degenerates on unseen
   data (acc 0.1451, macro-F1 0.1013, ECE 0.3207; all floors FAIL); OOS
   LOCKED.
4. **Governance preview** — read-only; promotion never called.
5. Final verdict: **NOT_ELIGIBLE / NO_CANDIDATE** — the scientific evidence
   is now COMPLETE and NEGATIVE.

## What was delivered

| Item | Where |
| :--- | :--- |
| BUG-106 profile + parity evidence | artifacts/benchmarks/bug106_profile.json (gitignored) + scratch/bug106_*.py/.out.txt (committed) + docs/AGENT-09-EVIDENCE-EXECUTION.md |
| Incremental builder parity test | tests/unit/test_70d_bug106_incremental_phase19.py (4 passed) |
| Temporal OOS result | artifacts/validation/70d_oos_results.json (LOCKED) |
| Robustness sweep | artifacts/validation/70d_oos_results.json (robustness_sweep block) |
| Manifest provenance fix | src/nexus_scalp/model_generation/{models.py, training.py} — BUG-117 |
| Governance preview | artifacts/validation/70d_governance_preview.json + 70d_governance_readiness.json (updated) |
| Evidence report | docs/AGENT-09-EVIDENCE-EXECUTION.md |
| Bug ledger | agents/bugs.md BUG-117 |

## Honest verdict

```
NO_CANDIDATE     (no scientifically-viable 70D candidate)
INSUFFICIENT_EVIDENCE (shadow floor not met — but science already fails)
NOT_ELIGIBLE     (temporal OOS fails all validation floors)
```

The A/B/C fair benchmark (AGENT-05) already showed 70D ≤ 60D (−0.026
macro-F1); the temporal OOS now proves the 70D candidate degenerates on
unseen data. The evidence chain is SUFFICIENT for governance to decide:
**do not promote**. Promotion remains human-only (INV-015); no promotion
API was called.

## Commits

- 02aae46 AGENT-09: BUG-106 verification — incremental builder tests +
  profile evidence
- (pending) AGENT-09: BUG-117 manifest provenance + evidence execution
  reports

## Risks / remaining

- The 20K-bar real dataset build (ag09_real_70d_v1, incremental) did not
  complete in-session (~21 min, pool-state recompute still cost-dominant).
  The 2446-row verified dataset is the evidence basis. A future
  optimization (bounded pool-state window like the 4000-bar fix) would let
  the incremental builder scale to 100K.
- Walk-forward (34-fold) NOT_RUN on the small slice — temporal OOS is the
  equivalent honest evidence at this size.
- Champion: BUG-104 restored state (9105cef7) untouched; operator decision
  on champion restoration still pending (docs/CHAMPION_ARTIFACT_INCIDENT_20260819.md).

## EXACT NEXT-AGENT INSTRUCTIONS (TASK-10)

1. Do NOT promote any 70D candidate — the evidence is negative
   (docs/AGENT-09-EVIDENCE-EXECUTION.md §10, OOS LOCKED).
2. If 70D science is revisited: first scale the incremental builder
   (bounded pool-state recompute) so a 100K-row dataset is buildable in
   minutes, THEN re-run A/B/C on the full history, THEN a true temporal OOS
   on the last 2 weeks.
3. If any OTHER schema/candidate is proposed: verify its manifest carries
   top-level feature_schema_hash / liquidity_algorithm_version /
   training_commit / oos_artifact (BUG-117 regression) and re-run the
   governance preview (scratch/ag09_governance_preview.py).
4. Maintain the honest status: NO_CANDIDATE / NOT_ELIGIBLE until a
   candidate passes temporal OOS floors (acc ≥ 0.30, macro-F1 > 0.34,
   bal-acc > 0.34, ECE ≤ 0.15).
5. Champion restoration decision (BUG-104) remains with the operator —
   document any change in change_control + bugs.md.