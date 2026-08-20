# tests/unit/test_model_benchmark_phase13b.py

- GUARDS: PHASE 13B TCN_ATTENTION_V1 benchmark — behavioral proof of the new architecture + sequence pipeline (spec 34): architecture builds, invalid config rejected, sequence ordering/boundary handling, causal temporality, 3-class labels.
- KEY ASSERTIONS:
  - `test_01_builds_and_outputs_3class`; `test_02_invalid_arch_rejected`; `test_07_3class_labels_only`; `test_05_causal_no_future` (sequence features never see future); News ablation: news-off input 50 dims vs news-on 62; dataset-parity hash across splits; calibration/ECE floor; champion never overwritten; DB-free load; corrupted artifact rejected (41 asserts).
- PITFALLS IT ENCODES: causal masking in sequences is non-negotiable; benchmark fairness = same dataset hash for both architectures.
- NOTES: 9 classes; the 13B challenger's full gate matrix.
