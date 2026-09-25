# src/nexus_scalp/strategies/seeder.py

- PURPOSE: PHASE 15C Built-in Strategy Seeder — seeds the registered
  built-in strategies (ichimoku pair) into the research registry so they
  become first-class research candidates (DISCOVERED) that the pipeline can
  backtest / walk-forward / OOS-validate like any discovered candidate.
- ARCHITECTURE LAYER: Application/Research integration (writes only through
  the registry's queued upsert; no order authority, no validation, no
  promotion).
- RESPONSIBILITY: idempotent upsert of one StrategyRegistryEntry per
  (strategy_id, strategy_version) — PRESERVING whatever validation truth
  already exists for that version (registry immutability contract).
- DEPENDENCIES: `adapters.database.audit_repository` (AuditRepository),
  `research.models` (StrategyRegistryEntry), `research.registry`
  (StrategyRegistry), `strategies.base` (builtin_candidates), observability
  logging.
- CONNECTS TO: research.worker._refresh_seed (runs seed_builtin_candidates
  every cycle), the charted "seed builtin" API paths, and the pipeline's
  registry reads.

- KEY CONCEPTS:
  - `seed_builtin_candidates(audit_repo, registry=None)` (lines 28-90):
    for each builtin candidate (deterministic content-addressed versions):
    1. build a fresh StrategyRegistryEntry from the candidate with all
       results None, confidence 0, sample_count 0, empty lineage, lifecycle
       = candidate.lifecycle (DISCOVERED);
    2. `existing = registry.get(candidate.strategy_id, candidate.
       strategy_version)` — when an entry already exists, model_copy
       PRESERVES backtest / walkforward / oos / robustness / score /
       confidence / sample_count / validation_lineage / lifecycle /
       retirement_reason / created_at (lines 66-82). Re-seeding NEVER
       wipes validation results or downgrades lifecycle;
    3. registry.upsert(entry) — the TASK-21 immutability guards in
       registry.upsert additionally refuse definition mutation under the
       same version and (with the regression flag) lifecycle downgrade;
    4. logs `[STRATEGY_SEED] event=UPSERTED ...`.
    Returns the list of entries created/updated.
  - `seed_builtin_candidates_deferred` (93-99): thread-safe wrapper for the
    background research worker — any failure returns [] (isolated, logged).
  - Seeder semantics by design: never validates, never promotes, never
    touches the live path (docstring lines 34-35).
- HOT PATH / PERFORMANCE: called every worker cycle BEFORE the dataset step
  (worker._refresh_seed); 2-3 builtins → O(1) reads + queued writes; the
  per-cycle re-read of existing rows is cheap.
- EDGE CASES & PITFALLS:
  - Preservation copies existing.lifecycle wholesale — if a builtin was
    manually promoted to SHADOW/ACTIVE then re-seeded, its lifecycle is
    kept (good), but if the SAME version's definition changed in code
    (content hash changes → NEW version), the old row is untouched and a
    new DISCOVERED row appears — intended immutable-versioning behavior,
    yet the "new" strategy looks like a fresh candidate needing full
    revalidation.
  - `registry.get` without a version returns the newest-by-updated_at row —
    the seeder passes the exact version, so this path is not hit here.
  - The seeder does NOT verify strategy_version consistency
    (candidate.is_version_consistent) before seeding — a buggy builtin with
    a hardcoded version string would seed research with a mismatched
    identity.
  - Seeds are DISCOVERED and never auto-validated by the seeder itself;
    the worker's downstream validation only covers DISCOVERED candidates in
    the current cycle's dataset-driven flow.