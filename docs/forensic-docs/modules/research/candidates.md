# src/nexus_scalp/research/candidates.py

- PURPOSE: PHASE 09B StrategyCandidate contract + DETERMINISTIC content
  versioning (spec 9/27/28). Identity derives from the DEFINITION, not from
  a random id or the existence of any model artifact: any change to
  entry/exit/context/risk/schema produces a NEW strategy_version, and the
  old version's validation records stay attached to the old version
  (immutable versioning).
- ARCHITECTURE LAYER: Research Domain (frozen pydantic; no I/O; no order
  authority).
- RESPONSIBILITY: the typed hypothesis object that discovery, the pipeline,
  the strategies seeder and the factory all produce/consume; carries the
  idea that a candidate does NOT depend on a model artifact existing.
- DEPENDENCIES: pydantic; `experience.models` schema constants; `research.
  models` (CandidateLifecycle).
- CONNECTS TO: discovery.discover_candidates (creator), pipeline
  (validate_candidate, _select_family, _static_validation_problems, _register),
  strategies/base.make_candidate (builtin seeds), factory._to_strategy_
  candidate (adapter into research).

- KEY CONCEPTS:
  - Fields (lines 41-56): strategy_id, content-derived strategy_version,
    feature_schema_id/dimension (schema-compatibility guard), creation_
    timestamp, source_dataset_id, discovery_window, context_definition /
    entry_logic / exit_logic / risk_assumptions (dicts — the discovery and
    strategy code fill these with structured tokens), parent_strategy_ids,
    discovery_method, lifecycle, discovery_evidence (free-form evidence map;
    discovery uses it for sample_ids + tier, factory embeds the DSL).
  - `content_digest` (lines 67-81): sha256 over a canonicalized payload of
    {strategy_id, schema id/dimension, recursively sorted context/entry/
    exit/risk, sorted parents, discovery_method} with sort_keys + default=str
    JSON — timestamps and lifecycle deliberately EXCLUDED so identity is
    definition-only.
  - `canonical_version` (lines 83-85): `v<first-12-hex>`; `is_version_
    consistent` (87-89) checks a candidate's stored version matches.
  - `with_definition_change` (lines 91-115): frozen-object evolution —
    deep-merges dict changes, recomputes canonical_version, resets lifecycle
    to DISCOVERED, stamps a new creation_timestamp, and appends the OLD
    version to parent_strategy_ids (immutable lineage, spec 28).
  - `is_schema_compatible` (lines 121-125): a 50D candidate is never
    silently compared to a wider schema — equality on schema id + dimension.
  - Helpers `_sort` (deep canonical ordering) and `_merge_dicts` (deep merge
    for with_definition_change).

- HOT PATH / PERFORMANCE: hashing is per-candidate at creation; fine for
  discovery/seeding, worker-cycle only.

- EDGE CASES & PITFALLS:
  - `content_digest` includes `discovery_method` — two discoveries of the
    same logic under different methods (e.g. "context_family" vs
    "factory:template") produce DIFFERENT versions of the same definition,
    creating duplicate identities across pipelines (the factory builds its
    own SF- ids, so this is mostly theoretical between seeds).
  - `with_definition_change` does NOT re-verify the new definition against
    schema constraints — it only re-hashes; validation is the pipeline's
    job later.
  - `strategy_version` is a required field but discovery constructs
    candidates with "" then model_copies the canonical version; any code
    path that forgets the copy leaves an empty version and silently
    mismatches `is_version_consistent`.
  - Nested dict values that aren't JSON-serializable fall back to str()
    via default=str — two definitions differing only in repr are
    distinguishable, but non-deterministic reprs (e.g. dict ordering of
    unhashable values) would break identity determinism; _sort mitigates
    ordering at the top object levels.