CHAMPION ARTIFACT INCIDENT — RECOVERY MARKER (READ FIRST)
========================================================
Date: 2026-08-19 ~03:58 IRST (UTC+0330)
Operator: Hermes-ModelValidation-04 (TASK-04-70D-MODEL-VALIDATION)

WHAT HAPPENED
-------------
A TASK-4 smoke probe instantiated `WalkForwardTrainer(num_folds=3, ...)`
WITHOUT an explicit `artifact_save_path`. The pre-BUG-104 default was:

    artifacts/models/scalp/XAUUSD/v1.0.0/model.pt   (the LIVE Champion path)

`train_and_validate()` therefore OVERWROTE the production Champion artifact
with a 70D scalp_v4 synthetic-walk-forward model. The frozen Champion bytes
(model.pt hash f0f70efb1b55855b..., scaler 811554e5...) are NOT recoverable
from this repository: artifacts/ is gitignored, no byte-identical copy exists
in any backup, pytest temp dir, WSL, or git object (verified by exhaustive
hash scan 2026-08-19 04:00).

BLAST RADIUS (verified)
-----------------------
- artifacts/models/scalp/XAUUSD/v1.0.0/model.pt ... CLOBBERED (was f0f70efb...)
- artifacts/models/scalp/XAUUSD/v1.0.0/model.scaler.npz ... CLOBBERED (was 811554e5...)
- artifacts/models/scalp/XAUUSD/v1.0.0/model.meta.json ... CLOBBERED (70D metadata)
- model registry row (audit.db experience_model_registry id=4,
  artifact_fingerprint=f0f70efb1b55855b) ... PRESERVED (DB untouched)
- docs/task5_champion_baseline.json ... PRESERVED (frozen evidence of identity)
- No live trading/orders/execution were affected (artifact file only).

CURRENT STATE (recovery applied 2026-08-19 04:05)
--------------------------------------------------
- model.pt        <- bench_a_v1/model.pt   (scalp_v1/50D, LEGACY_SCALPNET_V1,
                      dataset ds_cb30f87520e9e6a4, seed 42 — SAME RECIPE FAMILY
                      as the frozen Champion, NOT byte-identical)
- model.scaler.npz <- bench_a_v1/scaler.npz (50D)
- model.meta.json  <- rewritten with the restored 50D identity
- The live engine's dimension-mismatch quarantine would have caught the 70D
  artifact at next startup; the restore avoids that path entirely.

WHAT MUST HAPPEN (GOVERNANCE — operator decision, INV-015)
----------------------------------------------------------
1. This is a CHAMPION IDENTITY CHANGE. The restored artifact is a DIFFERENT
   set of weights than the frozen Champion f0f70efb...
2. Options, in order of preference:
   a. Restore the original model.pt/scaler.npz from any EXTERNAL backup you
      may hold (the repo has none); verify hashes f0f70efb.../811554e5...
   b. Operator-approved retrain + promotion per ModelGovernanceEngine
      (SHADOW -> READY_FOR_REVIEW -> APPROVED -> CHAMPION).
   c. Explicitly accept bench_a_v1-derived weights as the new active model
      and re-register provenance (NEW artifact fingerprint).
3. Until the operator decides, the active artifact is bench_a_v1-derived
   (50D, functional) — flagged as RESTORED_CANDIDATE, NOT the original
   Champion.

ROOT-CAUSE FIX (BUG-104) — ALREADY APPLIED
------------------------------------------
`WalkForwardTrainer.artifact_save_path` default changed to
`artifacts/model_generation/models/wf_candidate/model.pt`. A bare trainer
instance can no longer silently write the live Champion path. LiveEngine
passes the production path explicitly (deliberate, operator-authorized
retrain flow). BUG-103 (class-weight width crash) also fixed.

SEE ALSO
--------
- agents/bugs.md: BUG-103 (WalkForwardTrainer CrossEntropy weight-width
  crash), BUG-104 (default save path clobbering live Champion).
- This marker file is committed to the repo so the incident is permanent,
  auditable knowledge — never silently erased.