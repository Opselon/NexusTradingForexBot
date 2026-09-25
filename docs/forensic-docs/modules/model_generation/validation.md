# src/nexus_scalp/model_generation/validation.py

- **PURPOSE:** `ValidationFactory` — the quality-gate suite for trained
  candidates: class-collapse detection, balanced accuracy, calibration
  (ECE), regime performance decomposition, confusion metrics,
  head-to-head comparison, and news-ablation comparison.
- **ARCHITECTURE LAYER:** ML research (validation gates).
- **RESPONSIBILITY:** (a) `detect_class_collapse` — a class predicted
  < floor → candidate rejected (the anti-collapse gate); (b)
  `_balanced_accuracy` (macro F1-style); (c) `compute_calibration` —
  expected calibration error ≤ 0.15 floor; (d) `evaluate_regime_...`
  per-regime performance; (e) `head_to_head` (champion vs challenger
  matrix); (f) `compare_news_ablation` (news on/off delta).
- **DEPENDENCIES:** numpy, sklearn-style metrics helpers, logging.
- **CONNECTS TO:** training (post-train validation), benchmark MATRIX
  (8 cells), comparison/reporting, tests (test_70d_model_validation_task4).
- **KEY CONCEPTS:** The floors (OOS macro-F1 > 0.34, balanced-acc >
  0.34, ECE ≤ 0.15, min-evidence 100 rows, non-finite loss / grad norm >
  5 → FAILED) are the enforceable contract between trainer and promotion
  — a candidate that doesn't clear them never reaches comparison.
- **EDGE CASES & PITFALLS:** ECE computed on the candidate's own
  calibration set must not be confused with the OOS ECE (report both);
  class-collapse detection must use the predicted distribution (a model
  that never predicts SELL is collapsed even if train data had sells).