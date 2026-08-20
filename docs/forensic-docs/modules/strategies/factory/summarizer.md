# src/nexus_scalp/strategies/factory/summarizer.py

- PURPOSE: Research Summarizer — Evolution Memory (2026-08-20): converts
  raw historical strategy results into a COMPACT learning context (spec
  24/25/81). The LLM (and the deterministic evolution planner) consume THIS
  summary — never thousands of raw rows. Covers: top/worst performers,
  most robust/unstable, common failure modes, successful/failed features
  and combinations, regime results, complexity effects, OOS degradation,
  drawdown, trade-count distributions, generation-to-generation
  improvement, diversity.
- ARCHITECTURE LAYER: Research/Factory pure computation (no I/O; no order
  authority).
- RESPONSIBILITY: build_summary (per-generation GenerationSummary),
  memory_summary (cross-generation EvolutionMemory dict), score_verdict,
  format_summary_for_prompt (compact textual rendering).
- DEPENDENCIES: `factory.models` (GenerationSummary, FailureReason,
  StrategyFamily), `factory.ranking` (population_diversity), stdlib json
  (lazy).
- CONNECTS TO: orchestrator.complete_generation (build_summary) and
  build_memory (memory_summary), provider._build_messages (research_memory
  context), UI/API summary views.

- KEY CONCEPTS:
  - `build_summary` (42-124): inputs = generation row, factory_candidates
    rows (structural + lifecycle), registry rows for THIS generation
    (score/backtest/oos/robustness). Computes: evaluated (score present),
    validated/rejected by score verdict; avg/best/median final_score;
    failure distribution from candidate failure_reasons; feature/family
    distributions from evaluated registry context_definition; operator
    survival copied from caller stats; diversity = population_diversity
    (family+feature mixed, averaged); structurally_valid count (defensive
    decode of the `structural` column — '{}'/''/'null' ⇒ not passed,
    131-151); elite count = VALIDATED AND final_score >= 0.6 (the same
    0.6 threshold as orchestrator elite selection).
  - `memory_summary` (154-231): top-5 / worst-5 by final_score;
    common_failures = top-8 failure distribution across summaries;
    successful_features = cumulative feature distribution (NOTE: it tallies
    ALL evaluated, not just successful — see pitfall); failed_features =
    features appearing in REJECTED candidates' context filters;
    operator_success = summed survived/generated ratios per operator;
    bounded windows — generations[-4:], elite[:10], complexity_trend
    generations[-6:]; stagnation_count passed through.
  - `format_summary_for_prompt` (234-258): bounded textual rendering —
    generation counts, stagnation, elite (5), common failures (5),
    successful/failed features (6 each) — the ONLY form the LLM prompt
    receives (spec 34/81; never raw rows).
- HOT PATH / PERFORMANCE: single pass per generation + cross-generation
  fold; runs once per generation completion and per worker memory build —
  off the tick path.
- EDGE CASES & PITFALLS:
  - `successful_features` in memory_summary accumulates the FREQUENCY of
    features across ALL evaluated entries (line 180-184) — despite the
    name it is a popularity count, not a success measure; a universally
    failing feature still tops "successful_features" if heavily used.
  - `failed_features` uses REJECTED candidates' context filters — a
    REJECTED candidate's context may be unreachable (family-select
    validation) or its filters irrelevant to why it failed (OOS), so the
    proxy is noisy; there is no feature-level outcome attribution.
  - `operator_success` sums ratios per operator across summaries —
    operators with more generations accumulate higher totals, biasing
    adapt_probabilities toward historical volume rather than recent
    performance.
  - `build_summary.elite` duplicates the orchestrator's elite threshold
    (≥ 0.6 AND VALIDATED) — two copies of the same policy constant; a
    change in one silently desynchronizes the other.
  - `median` = sorted[len//2] (upper-median convention for even counts),
    trivial but worth noting when comparing to other tools.