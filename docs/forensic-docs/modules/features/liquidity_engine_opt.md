# src/nexus_scalp/features/liquidity_engine_opt.py

- **PURPOSE:** The v1.1 optimized variant of the liquidity computation
  (TASK/liquidity OPT phase) — `compute_liquidity_features_v1_1` and friends
  (equal_high_low_strengths_v1_1, detect_reactive_sweep_v1_1,
  htf_liquidity_score_v1_1, liquidity_confluence_v1_1) with tunable
  `LiquidityParams`. Parallel/alternative lineage to the canonical
  `liquidity_engine`; the runtime picks one via configuration/benchmarking.
- **ARCHITECTURE LAYER:** Features (research/optimization track).
- **RESPONSIBILITY:** Faster/parameterized liquidity scoring (cluster
  dedup with explicit cutoff, reactive-sweep detection, confluence
  aggregation) while keeping the same output geometry as the base engine.
- **DEPENDENCIES:** numpy; `liquidity_engine` types reused where compatible.
- **CONNECTS TO:** performance benchmarks
  (test_liquidity_optimization_phase19.py), configurable runtime selection
  (params.as_dict → settings/pipeline), TASK-19 optimization reports.
- **KEY CONCEPTS:** `LiquidityParams` centralizes tunables (dedup cutoff,
  confluence thresholds); `_dedup_pools` merges near-equal pools before
  scoring (reduces double-counting of the same level); sweep detection
  (`detect_reactive_sweep_v1_1`) adds a reactive (post-sweep displacement)
  signal the base engine lacks.
- **EDGE CASES & PITFALLS:** This is the OPT TRACK, not the canonical
  producer — when both exist, the runtime must be explicit about which it
  uses (model input must match training; mixing engines across train/live
  breaks the 70D parity invariant).