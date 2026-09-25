# src/nexus_scalp/features/schema.py

- **PURPOSE:** Kills the "magic number 50" problem. Every consumer of feature
  geometry (trainer, live engine, model factory, persistence, memory layer)
  reads its dimension/columns from THIS registry instead of hard-coding widths.
- **ARCHITECTURE LAYER:** Features (registry).
- **RESPONSIBILITY:** Declare the ACTIVE schema (`scalp_v1`, 50D — the live
  contract, UNCHANGED by this module) and an append-only registry of forward
  schemas (60D/70D/92D research contracts) so a future migration is a config
  switch + retrain, not a repo-wide refactor.
- **DEPENDENCIES:** `observability.logging` (get_logger), stdlib dataclasses.
- **CONNECTS TO:** `schema_contract` (asserts registry consistency), dataset
  builders (`model_generation`), `experience.models.FeatureSnapshot`
  (experiences resolve against the schema they were produced under),
  train/test code that selects `feat_0..feat_{n-1}` columns.
- **KEY CONCEPTS:**
  - `FeatureSchema` — frozen dataclass (schema_id, dimension, description,
    is_active, supersedes). `columns` yields canonical `feat_0..feat_{n-1}`
    names (invariant 2: column naming stays stable across schemas so Polars
    selections/scalers keep working). `validate_vector`/`validate_columns`
    fail LOUD on arity mismatch (a silently truncated vector would corrupt
    inference AND every stored experience).
  - `FeatureSchemaRegistry` — append-only by design: re-registering an existing
    id requires `replace=True`, and a dimension change on an existing id is
    REFUSED (typo can't silently redefine the live contract). `resolve()`
    raises KeyError for unknown ids — never guesses.
  - `ACTIVE_SCHEMA_ID = "scalp_v1"` — the single switch that migrates the live
    contract.
  - Registered schemas: scalp_v1 (50D, ACTIVE), scalp_v2 (60D momentum
    augmentation, candidate), scalp_v3 (70D canonical — Base|News|Liquidity,
    candidate), scalp_v4 (70D integration contract, candidate), 
    scalp_liquidity_v1 (60D liquidity-only semantics at 50..59, candidate),
    scalp_v4_temporal_candidate (92D = 70D + 22 temporal liquidity dims,
    research only). The docstrings carry the important forensics: the old
    "350D forward-declared" contract NEVER materialized (no artifact ever
    existed — superseded per TEST-29) — the registry now says so explicitly.
  - `schema_for_dimension` — reverse lookup for legacy artifacts that recorded
    only a width; returns None (caller decides to refuse) rather than
    misattribute.
- **EDGE CASES & PITFALLS:**
  - `resolve()` KeyError is a feature, not a bug: callers must handle it
    (a silent 50D default would let a 60D-trained model run against 50D input).
  - The registry deliberately holds schemas with NO artifact yet — tests
    exercise the geometry; confusing "registered" with "production" is a
    known source of confusion (worker-status honesty rules apply, e.g.
    benchmark MATRIX cells report DISABLED truthfully).
  - Registering a schema never mutates a historical one — legacy experiences
    keep resolving against their original geometry (invariant 3).