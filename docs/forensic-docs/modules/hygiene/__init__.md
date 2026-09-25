# src/nexus_scalp/hygiene/__init__.py

- PURPOSE: TASK-11 Database Hygiene Worker package facade — defines the
  TIER constants, cleanup confidence levels, worker modes/states and
  orphan classes used across the hygiene package. The FULL safety contract
  lives in docs/DATABASE_HYGIENE.md.
- ARCHITECTURE LAYER: Domain (shared enums/constants).
- RESPONSIBILITY: DataTier (spec §3 classification — TIER-0 BROKER_TRUTH
  … TIER-8 LEGACY_ARTIFACT), Confidence (spec §7 — EXACT_DUPLICATE /
  LIKELY_DUPLICATE / NOT_DUPLICATE / UNKNOWN), WorkerMode (spec §2 —
  AUDIT_ONLY / DRY_RUN / SAFE_CLEAN / AGGRESSIVE_CLEAN; production default
  SAFE_CLEAN, first-run AUDIT_ONLY), WorkerState (spec §51 — DISABLED/
  IDLE/SCANNING/PLANNING/CLEANING/VERIFYING/PAUSED/FAILED/DEGRADED),
  OrphanClass (spec §9 — EXPECTED_ORPHAN/RECOVERABLE/REBUILDABLE/
  CORRUPTION/UNKNOWN).
- DEPENDENCIES: stdlib StrEnum only.
- CONNECTS TO: every hygiene module (worker/retention/detectors/state/
  worker_runner…), web diagnostics, CLI.
- KEY CONCEPTS: single source of truth for the tier vocabulary — the
  retention registry's never_delete guards and cleanup class policy keys
  off these enums (TIER-0..4 never_delete, TIER-5 derived rebuildable,
  TIER-6 cache, TIER-7 temporary state); Confidence.EXACT_DUPLICATE is the
  ONLY confidence the CleanupExecutor auto-applies; WorkerState.PAUSED
  gates run_cycle; OrphanClass feeds orphan reports (orphans are never
  auto-deleted — classification informs the plan).
- HOT PATH / PERFORMANCE: enum constants only — no runtime cost.
- EDGE CASES & PITFALLS: WorkerMode docstring says "production default
  SAFE_CLEAN" while the worker's constructor default is AUDIT_ONLY
  (worker_runner.py line 71) and the scheduler resolves AUDIT_ONLY unless
  apply_deletes + non-dry-run + non-LIVE — the discrepancy is intentional
  (first-run audit-only posture) but the docstring reads as the opposite;
  OrphanClass.REBUILDABLE is defined but never emitted by the detectors
  (they emit EXPECTED_ORPHAN/RECOVERABLE/CORRUPTION/UNKNOWN); adding a
  new tier/class requires updating __all__, DOCUMENTED tier policy in
  docs/DATABASE_HYGIENE.md, and any hard-coded tier lists in retention.