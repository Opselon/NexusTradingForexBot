# src/nexus_scalp/strategies/factory/validators.py

- PURPOSE: Strategy DSL VALIDATION — HARD GATES (spec 59): an LLM-generated
  candidate with an invalid schema, unsupported feature, lookahead risk or
  excessive complexity is REJECTED before it ever reaches the backtest
  queue. The LLM output is untrusted input; only the deterministic
  validator decides what may be scheduled.
- ARCHITECTURE LAYER: Research/Factory validation (pure functions; no I/O;
  no order authority).
- RESPONSIBILITY: four gates — validate_schema (structure + supported
  symbols/timeframes + operator whitelist), validate_features (every
  referenced feature exists in the canonical 70D catalog), validate_
  causality (no future-bar references; completed-bar semantics REQUIRED),
  validate_complexity (condition/feature/timeframe budgets, spec 12) —
  plus `validate_candidate` ANDing all gates with the population-dedup
  check (spec 13).
- DEPENDENCIES: `factory.dsl` (SUPPORTED_TIMEFRAMES, DEFAULT_SYMBOLS,
  dsl_hash, feature_catalog_index), `factory.models` (FactoryCandidate,
  StrategyDsl, FailureReason, FactoryStage, ValidationVerdict).
- CONNECTS TO: orchestrator.validate_population (the only front door into
  scheduling), evolution.mutate/crossover/explore (post-operator re-check),
  worker/LLM provider flow (provider output must pass these same gates).

- KEY CONCEPTS:
  - `_APPROVED_OPS` (line 41): gt/lt/gte/lte/eq/neq/between — the filter
    grammar whitelist; any other op REJECTS (INVALID_SCHEMA).
  - Gate 1 `validate_schema` (85-164): top-level key superset check
    (_KNOWN_TOP_KEYS — pydantic extra='forbid' already rejects unknown
    keys; this is defense in depth), market symbols/timeframes non-empty +
    within allowlists (UNSUPPORTED_SYMBOL / UNSUPPORTED_TIMEFRAME),
    hypothesis.statement required (spec 11), exit logic required (spec 8),
    every filter dict-shaped with an approved op.
  - Gate 2 `validate_features` (167-198): recursive scan of the whole DSL
    for any `feature` key; every feature must exist in
    feature_catalog_index() — unknown ⇒ UNSUPPORTED_FEATURE, never
    silently implemented.
  - Gate 3 `validate_causality` (201-234): repr-scan of the JSON dump for
    forbidden future references (future_bars / next_bar / future_open/
    close/high/low) ⇒ LOOKAHEAD_RISK (NON-NEGOTIABLE); then requires
    constraints.completed_bars_only or no_future_data OR a hypothesis
    completed_bars_only declaration — an LLM candidate omitting it is
    REJECTED rather than assumed safe (spec 15). The LLM cannot declare
    itself causal — causality is verified structurally.
  - Gate 4 `validate_complexity` (237-290): recursive condition counter
    (keys op/logic/confirmation/require each count), distinct feature count
    from filters, timeframe count — each vs budgets dict → EXCESSIVE_
    COMPLEXITY.
  - `validate_candidate` (293-325): sequential short-circuit across the four
    gates, then the dedup check — candidate.definition_hash already in
    existing_hashes ⇒ DUPLICATE at FactoryStage.DEDUPLICATION. Note the
    dedup relies on the CALLER accumulating passed hashes (orchestrator
    does exactly that, lines 501-511).
- HOT PATH / PERFORMANCE: recursive traversals are O(DSL size); called for
  every candidate in a population (400+) plus every evolution operator
  output — still cheap (millisecond scale) off the tick path.
- EDGE CASES & PITFALLS:
  - The causality gate's repr-scan can false-positive on feature NAMES
    containing "future" substrings (e.g. a hypothetical "future_vol"
    feature would trip LOOKAHEAD_RISK) and false-negative on
    multi-line/unicode-escaped keys (repr lowercases everything, key
    spelling with escapes could dodge the substring match) — today no
    catalog feature contains the forbidden tokens, so the scan is safe in
    practice but should ideally walk the structure like gate 2 does.
  - `validate_schema` default symbols/timeframes come from dsl constants —
    the orchestrator passes its configured symbols, so XAUUSD-only dsl
    defaults collide with a broader configured universe only in tests that
    call the gate directly.
  - Complexity counts ANY dict containing op/logic/confirmation/require as
    one condition, including nested filters' values — thresholds with a
    `value` key do not count, so a filter with `between` (op + value list)
    may undercount vs a human reading.
  - `validate_candidate` does not re-run validate_causality once mutated
    DSLs are rebuilt in evolution — it DOES (evolution checks schema/
    features/complexity only; causality is skipped there — see
    evolution.py pitfalls).