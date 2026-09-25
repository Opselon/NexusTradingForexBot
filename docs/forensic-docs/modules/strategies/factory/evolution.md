# src/nexus_scalp/strategies/factory/evolution.py

- PURPOSE: Evolution Engine — Mutation, Crossover, Exploration (2026-08-20):
  evolutionary operators CONSTRAINED BY SEMANTIC VALIDITY (spec 7):
  MUTATION (bounded single-axis change preserving DSL validity),
  CROSSOVER (compatible parts of two validated strategies; rejects semantic
  contradictions + excessive complexity), EXPLORATION (controlled random
  exploration for genuinely new families — prevents local-optimum
  convergence). Every operator re-validates its output through the
  structural gates before the candidate is accepted; a failed operator
  produces NOTHING (never silently degrades into an invalid strategy).
- ARCHITECTURE LAYER: Research/Factory generation (pure; seeded random; no
  I/O; no order authority).
- RESPONSIBILITY: mutate / crossover / explore / adapt_probabilities
  (adaptive evolution, spec 98/99/100) over FactoryCandidates.
- DEPENDENCIES: `factory.dsl` (RANDOM_SEED, SUPPORTED_TIMEFRAMES,
  candidate_id_from_hash, dsl_hash, feature_ids, _template_dsl lazily),
  `factory.models` (enums + FactoryCandidate + StrategyDsl),
  `factory.validators` (validate_candidate, validate_complexity,
  validate_features, validate_schema).
- CONNECTS TO: orchestrator._evolved_population (adaptive operator
  application with per-op survival tallies), worker loop.

- KEY CONCEPTS:
  - `_MUTATION_ACTIONS` (51-59): add_filter, remove_filter,
    replace_indicator, change_threshold, change_timeframe, change_condition,
    simplify. Each is a small deterministic-ish mutation of the DSL raw
    dict (rng picks operands); all bounded by the catalog (feature_pool)
    and by existing content.
  - `mutate` (160-219): picks an action (default random from the pool),
    applies it, REBUILDS the StrategyDsl (any rebuild failure → None),
    then re-checks validate_schema + validate_features +
    validate_complexity — any failure ⇒ None (clean operator failure).
    NOTE: validate_causality is NOT re-run (see pitfalls). Child inherits
    parent's generation_id/population_index with parent candidate_id +
    hash[:12] in parent_ids; source=MUTATION, operator=MUTATION.
  - `_compatible` (222-232): crossover compatibility = overlapping symbol
    universes (both non-empty and intersecting); wildly different timeframes
    are NOT checked despite the docstring.
  - `crossover` (246-345): child = A's hypothesis (annotated with B's id) +
    family HYBRID + A's market/context/setup/risk + merged entry
    confirmation (dedup, capped 4) + unified filters (A then B, dedup by
    feature, capped 4) + B's exit; constraints merge with crossover=True +
    min(max_conditions). Rebuild + schema/features/complexity re-check;
    failure → None. Parents recorded in parent_ids.
  - `explore` (348-405): picks a FRESH family template from 5 families
    (trend/mean-rev/breakout/vol-expansion/liquidity-sweep), injects 1-2
    new features from the pool, filters capped at 4, context.regime.use
    True; re-validates; child marked source=RANDOM operator=NONE (design:
    exploration is a fresh direction, not an operator lineage) with the
    base as parent.
  - `adapt_probabilities` (408-437): adjusts mutation/crossover/exploration
    rates from historical operator success (survived/generated share vs
    1/3 expectation, bounded ±0.05/step) plus diversity pressure — when
    diversity < floor, exploration +0.05 and mutation/crossover −0.025;
    outputs renormalized to sum 1.0.
- HOT PATH / PERFORMANCE: per-operator O(DSL) rebuild + up to 3 validation
  passes with recursive traversals; up to 400 candidates/generation; off
  the tick path.
- EDGE CASES & PITFALLS:
  - mutate/crossover/explore skip validate_causality — they clone
    causality-safe parents and mutations never introduce future references
    (they only touch filters/market/entry/confirmations from the safe
    catalog), so the omission is safe in practice but the "re-validates
    its output" claim is only partial; a future mutation action touching
    hypothesis/constraints could slip a lookahead declaration in.
  - `_compatible` claims timeframe compatibility in its docstring but only
    checks symbols — M1 × H1 crossovers are permitted.
  - `explore` sets operator=NONE + source=RANDOM — rank/summary consumers
    see exploration children as "random" candidates, and operator survival
    stats never credit EXPLORATION (tally keyed by the candidate's
    operator), skewing adapt_probabilities inputs.
  - `crossover` merges confirmation/FILTERS with simple feature-name dedup —
  order-sensitive: `_merge_confirmation` caps at 4 by WAIVING later items,
    so B's confirmations beyond slot 4 are dropped silently.
  - All operators create NEW candidates with recomputed content hashes —
  mutation loops can converge to a cycle where mutate(A)→B and mutate(B)→A
  (threshold deltas are symmetric ±0.15), producing duplicate rounds
  that the dedup in validate_candidate/orchestrator then filters.