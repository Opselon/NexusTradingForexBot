# src/nexus_scalp/strategies/factory/dsl.py

- PURPOSE: Strategy DSL — schema, canonical feature catalog, canonicaliza-
  tion, and deterministic candidate generation (STRATEGY FACTORY
  2026-08-20). The DSL is the ONLY strategy representation the factory
  (and the optional LLM provider) may produce. Features come EXCLUSIVELY
  from the canonical 70D schema contract (`features/schema_contract.py`) —
  the factory never invents features, never changes the feature vector
  dimension (spec 10), never lets the LLM hallucinate unsupported
  indicators (spec 9).
- ARCHITECTURE LAYER: Domain/generation (pure functions + pydantic; random
  usage is SEEDED and deterministic; no I/O; no order authority).
- RESPONSIBILITY: family templates, catalog derivation, canonical JSON/hash
  identity, canonicalize_dsl, and the four deterministic generators
  (template/diversity/regime/random) + the Generation-0 mixture.
- DEPENDENCIES: `features.schema_contract` (canonical_feature_names,
  family_of, FAMILY_BASE/NEWS/LIQUIDITY), `factory.models` (StrategyDsl,
  FactoryCandidate, enums), stdlib (hashlib, itertools, json, random).
- CONNECTS TO: validators.py (gates consume catalog + dsl_hash),
  evolution.py (mutate/crossover/explore reuse templates + hashing),
  orchestrator.py (population generation), provider.py (prompt lists
  feature_ids/timeframes), research candidates adapter.

- KEY CONCEPTS:
  - Constants: DSL_SCHEMA_VERSION "1.0" (bump on grammar change, spec 86);
    GENERATOR_VERSION "deterministic-v1" (recorded per candidate);
    SUPPORTED_TIMEFRAMES (M1..D1, spec 65); DEFAULT_SYMBOLS ("XAUUSD" —
    never hardcoded into logic; the orchestrator passes the configured
    universe); RANDOM_SEED 20260820 for reproducible exploration.
  - `_FAMILY_TEMPLATES` (58-129): 11 family→template map — context flags
    (htf_bias/volatility_filter/session_filter/range_state/regime/
    liquidity), entry logic + confirmation feature names (drawn from the
    70D catalog), filters with feature/op/value, exit mode (trailing/fixed
    rr/target/chandelier). This is the deterministic hypothesis family
    map that keeps Generation 0 hypothesis-driven.
  - Catalog derivation (137-175): `build_feature_catalog()` enumerates
    canonical_feature_names() with index + family_of(idx) — the catalog CAN
    NEVER drift from the real model vector; `feature_catalog_index` (fast
    id→entry lookup) and `feature_ids` feed validators/prompts/templates.
  - Canonicalization (182-205): `canonical_json` (sort_keys + fixed
    separators + default=str) → `dsl_hash` (sha256 of the canonical dump —
    the dedup + identity key, spec 13) → `candidate_id_from_hash`
    (SF-<10-hex-upper>); `canonicalize_dsl` normalizes dicts into the model
    (extra='forbid' rejects unknown fields structurally).
  - Deterministic generators (all seeded via module RANDOM_SEED +
    family-specific offsets so runs reproduce):
    - `_template_dsl` (229-255): template + bounded per-slot randomization
      (timeframe choice); hypothesis carries statement/market_mechanism/
      expected_regime/invalidation/abstain_conditions; constraints
      no_future_data + max_conditions; risk governance global.
    - `generate_template_candidates` (275-292): Generation-0 30% — cycles
      families round-robin, rotates filter thresholds via `_rotate_template`
      (slot-derived ±0.05 steps) for diversity; bounded loop (count*4).
    - `generate_diversity_candidates` (308-353): 20% — deterministic
      cartesian feature-pair exploration over base features [:10] with
      family-appropriate wiring.
    - `generate_regime_candidates` (356-386): 10% — regime→family map
      (TRENDING→TREND_FOLLOWING etc.), stamps context.regime.
      require=<regime> + regime_specialization constraint.
    - `generate_random_candidates` (389-432): 10% — CONTROLLED random:
      features only from catalog [:14], 1-4 filters with gt/lt ±0.5,
      complexity capped (max_conditions = len+3), still carries a coherent
      hypothesis. Exploration, NOT free-form hallucination.
    - `generate_generation_zero` (435-490): the G0 mixture —
      max(1, 30%/20%/10%/10%) template/diversity/regime/random + the
      remaining slot filled with templates labeled CandidateSource.LLM
      (the orchestrator/provider may substitute real LLM candidates);
      assigns SF- ids, sources, population_index 0..N under generation_id
      "G0" (the orchestrator rewrites to the real generation id).
- HOT PATH / PERFORMANCE: catalog build enumerates 70 features (fast);
  generators bounded (count*4 loops); dsl_hash on every candidate. Runs in
  the factory worker cycle (asyncio.to_thread), never the tick path.
- EDGE CASES & PITFALLS:
  - `generate_generation_zero` labels the SLACK slot as source LLM even
    when populated with TEMPLATE dsls (lines 458-476) — candidates can
    claim an LLM provenance they never had; the orchestrator relies on the
    provider to replace them, else the provenance is fabricated.
  - `_rotate_template` mutates filters IN PLACE on a model_dump copy (fine)
    but the `slot % 5 * 0.05 * (±1)` pattern produces only 10 threshold
    variants per family — template diversity is shallow by design.
  - `_template_dsl` picks the timeframe via rng from SUPPORTED_TIMEFRAMES
    [:5]; market always uses DEFAULT_SYMBOLS — a configured multi-symbol
    universe is IGNORED by the generators (orchestrator's `symbols` only
    reaches validation, and only as an allowlist).
  - `dsl_hash` hashes model_dump() which includes defaulted fields
    (schema_version, family HYBRID default): two DSLs differing only in an
    omitted default hash IDENTICALLY to one that spells it out — intended
    canonicalization, but means a DSL that omits family defaults to HYBRID
    and collides with an explicit HYBRID dsl.
  - `candidate_id_from_hash` truncates to 10 hex chars (~40-bit space) —
    collision risk across a large population is low but non-zero; the
    dedup key remains the full definition_hash.