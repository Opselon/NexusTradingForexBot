# src/nexus_scalp/model_generation/strategy_factory.py

- **PURPOSE:** `StrategyFactory` — the strategy-conditioned sample layer:
  `HunterStrategy` (the strategy contract for the Hunter family: entry
  logic, exit logic, risk assumptions), `best_strategy_for(setup)` —
  selects the strategy whose conditions match a detected setup, and
  `EntryDecision` (the strategy's trade intent).
- **ARCHITECTURE LAYER:** ML research (strategy-conditioned sampling;
  no order authority).
- **RESPONSIBILITY:** (a) define strategy logic in a form the sample
  maker can consume; (b) route setups to their best strategy;
  (c) produce EntryDecisions that label whether a setup is tradable per
  the strategy's assumptions.
- **DEPENDENCIES:** setup detection types, domain models, logging.
- **CONNECTS TO:** sample_maker, dataset builders, tests
  (test_hunter_setup_strategy_sample).
- **KEY CONCEPTS:** Strategies here are RESEARCH hypotheses encoded as
  deterministic conditions (not the live-order strategies — those live in
  strategies/ for the research worker); the factory makes dataset
  construction strategy-aware so candidates learn per-strategy
  behavior.
- **EDGE CASES & PITFALLS:** `best_strategy_for` must be total (every
  setup maps to SOME strategy or an explicit NONE — never a crash);
  strategy ids must match the research registry vocabulary.