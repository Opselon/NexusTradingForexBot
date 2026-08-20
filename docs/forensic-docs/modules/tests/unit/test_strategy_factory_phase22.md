# tests/unit/test_strategy_factory_phase22.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- PHASE 22 STRATEGY FACTORY whole-cycle + failure-path behavioral suite: generate → structural validation → backtest → registry, with observable assertions (persisted rows, verdicts, gate failures).
- DSL validation: feature catalog matches canonical 70D EXACTLY (len == DIMENSION == 70, ids == canonical_feature_names(), all causal); canonical hash STABLE (`dsl_hash(dsl) == dsl_hash(StrategyDsl(**dsl.model_dump()))`); unsupported feature → `UNSUPPORTED_FEATURE`; lookahead declaration → `LOOKAHEAD_RISK`; complexity budget → `EXCESSIVE_COMPLEXITY`; duplicates rejected.
- Generation: deterministic AND diverse (zero-seed); persisted; mutation preserves validity; crossover merges parents; adaptive probabilistic parameters.
- 29 defs / 639 lines; fixtures `audit_repo`/`seed_experiences`/`make_candidate`.
- NOTE: generation-zero determinism vs diversity is the key tension this suite pins (reproducible seeds, varied outputs).