# src/nexus_scalp/features/schema_contract.py

- **PURPOSE:** The immutable, hash-covered specification of the canonical 70D
  feature vector (`scalp_v3`) — the single source of truth that dataset builder,
  replay, inference validator, live engine and manifests ALL derive their
  expectations from. One market snapshot → one canonical 70D vector with
  identical semantics everywhere.
- **ARCHITECTURE LAYER:** Features (contract/specification layer). Pure
  constants + validation; no runtime state.
- **RESPONSIBILITY:** Fix the geometry (Base 50D | News 10D | Liquidity 10D),
  the ordered names, and a deterministic content hash so any drift — a reordered
  field, a renamed feature, a widened block — is detected at import/test time,
  never silently at inference.
- **DEPENDENCIES:** `features.scalp_features.FEATURE_NAMES` (the protected
  scalp_v1 50-name tuple), `features.schema` registry (ACTIVE_SCHEMA_ID +
  FEATURE_SCHEMAS), and (lazily, inside a function) the news context schema
  `model_generation.models.default_news_context_schema`.
- **CONNECTS TO:** `model_generation` dataset builders (schema_v2 and friends),
  `inference_validator` (schema hash comparison), `live_engine` 70D path,
  shadow70 runtime, `governance/alignment` hash conventions, Debug Hub feature
  matrix, tests (TEST-29/TEST-30 parity suites).
- **KEY CONCEPTS:**
  - **Geometry constants** `BASE 0..49 | NEWS 50..59 | LIQUIDITY 60..69` with
    explicit START/END pairs (end-exclusive — the repo convention).
  - **NEWS 10D selection is NOT a blind first-10 slice**: the canonical
    `news_context_v1` vector has 12 fields; the 70D block keeps fields 0..8 +
    news_state (index 10) — the model's decision-critical state flag. The two
    dropped fields (source_consensus idx 9, time_since_event_sec idx 11) stay
    in the 12-field context for the 60D path. The strict equality assertion
    (`selected != NEWS_10D_NAMES` → RuntimeError) prevents field-order drift.
  - **Deterministic hashing:** `canonical_registry_json` serializes
    {index, name, family} × 70 + schema identity with `sort_keys` and
    `separators=(",", ":")` — the ONLY representation hashed, so two schemas
    with same dimension but different ordering produce different hashes
    (schema identity, not just math). `feature_schema_hash()` default prefix 16
    matches the repo-wide `sha256_json` convention (governance/alignment).
  - **`validate_70d_vector`** — dimension, finiteness, [-3,+3] bounds, optional
    hash match; raises `SchemaContractError` with family-tagged diagnostics.
    Never repairs silently (the "no silent substitution" invariant; padding/
    truncation is forbidden per contract point 6).
  - **`assert_canonical_registry`** — fails loudly if registry scalp_v3
    dimension mismatches OR if `ACTIVE_SCHEMA_ID != "scalp_v1"` — the
    invariant that 70D stays CANDIDATE-ONLY and the LEGACY live contract
    (50D) is protected. This is the governance boundary of the whole 70D
    series.
  - Import-time guard at module bottom calls `canonical_feature_names()`
    immediately, so contract drift breaks the process at startup, not at
    first inference.
- **EDGE CASES & PITFALLS:**
  - The import-time guard creates a hard dependency on the news context schema
    module (`model_generation.models`) — importing schema_contract pulls the
    whole model-generation package's models; keep that module light.
  - `family_of(i)` raises IndexError outside [0,70) — callers must not catch
    it silently; an out-of-range index IS a contract violation.
  - Changing ANY name in `NEWS_10D_NAMES`/`LIQUIDITY_10D_NAMES` changes the
    schema hash → old artifacts fail validation → migration path required.
    This is deliberate (detect drift), but means name changes are breaking
    changes.