# src/nexus_scalp/model_generation/benchmark.py

- **PURPOSE:** `BenchmarkRunner` — the 8-cell MATRIX benchmark (50D/60D ×
  news on/off × LEGACY/TCN) that honestly compares candidate models on
  OOS metrics, renders a markdown report (`_render_md`), and concludes
  with per-cell verdicts (`_conclude`). Worker status is truthful
  (DISABLED when auto_train off).
- **ARCHITECTURE LAYER:** ML research (benchmarking).
- **RESPONSIBILITY:** (a) run each cell (dataset → train → validate →
  score) with determinism; (b) `_predict_probs` (post-scaler tensor +
  softmax); (c) aggregate the MATRIX with per-cell floors; (d) report
  markdown for the UI/CI.
- **DEPENDENCIES:** training + validation + factory, dataset builders,
  artifact store, logging.
- **CONNECTS TO:** model registry (benchmark results), CI/reporting,
  shadow70, tests (test_model_benchmark_phase13b,
  test_70d_model_validation_task4).
- **KEY CONCEPTS:** The 8 cells are the comparison scaffold the 70D
  promotion decision rests on — each cell must run the SAME pipeline
  (a skipped cell is reported SKIPPED/DISABLED, never silently dropped).
- **EDGE CASES & PITFALLS:** Cell runs are long — cancellation must be
  safe; a cell failing a floor is FAILED (not absent); the report must be
  deterministic (stable sort, fixed precision).