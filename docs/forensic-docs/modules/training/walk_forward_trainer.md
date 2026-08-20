# src/nexus_scalp/training/walk_forward_trainer.py

- **PURPOSE:** The training engine: (a) purged blocked walk-forward validation
  (34 folds, purge+embargo, per-fold fresh model) → out-of-sample metrics;
  (b) final production training on the full trainable set; (c) ONLINE
  FINE-TUNING of a live model clone from the recent rolling buffer (the
  continuous-learning loop); (d) artifact persistence (model/scaler/metadata).
- **ARCHITECTURE LAYER:** Training/ML — OFFLINE/BACKGROUND only. Heavy torch
  work is NEVER on the tick path (LiveEngine invokes via `asyncio.to_thread`).
- **RESPONSIBILITY:** Turn a labeled Polars frame into a validated ScalpNet +
  ScalerBundle, with explicit quality gates and a safe artifact path
  (BUG-104: default save path is the CANDIDATE path — a bare trainer run can
  no longer silently overwrite the live Champion artifact; only an explicit
  operator/LiveEngine-supplied path may target production).
- **DEPENDENCIES:** torch (AdamW, CosineAnnealingLR, CrossEntropyLoss),
  polars, numpy, sklearn (scaler fit/transform), `features.schema`
  (active_dimension / resolve_schema — SCHEMA-DRIVEN geometry), domain enums,
  labeling via external callers. `FocalLossWithSmoothing` defined here.
- **CONNECTS TO:** CLI training, LiveEngine online fine-tune trigger,
  model_generation experiment build, model_lifecycle orchestrator,
  tests (test_walk_forward_trainer, test_model_generation_phase13).
- **KEY CONCEPTS:**
  - **Schema-driven geometry:** `NUM_FEATURES = active_dimension()` (50 today)
    and per-instance `feature_schema` resolved from `feature_schema_id` —
    training a 60/70/92D model is a constructor arg + retrain, NOT a code
    change. `NUM_CLASSES=3` (label taxonomy) vs `MODEL_HEAD_CLASSES=4`
    (ScalpNet head incl. WAIT). `label_map`/`inverse_label_map` bridge
    ActionType strings ↔ ints.
  - **Walk-forward loop:** blocked folds (fold_size = n/34, last fold takes
    the remainder), per-fold: `_split_fold_with_embargo` (train block +
    purge + validation + embargo tail dropped so label horizons can't run
    into the next fold), fit scaler on TRAIN ONLY, transform test with the
    same scaler, fresh model per fold (generalization honesty), AdamW +
    CosineAnnealingLR + early stopping (patience 3) on val loss, best-state
    restore, OOS predictions accumulated across folds →
    `_evaluate_global_performance` (min validation accuracy 0.35, +3%
    improvement over baseline, max sell dominance 0.58 gates).
  - **Final training phase:** retrains on ALL trainable rows with a
    full-data scaler and class weights, then `_save_checkpoint` /
    `_save_scaler` / `_save_metadata` (feature cols recorded → future
    loaders can verify contract). Post-training verification logs raw
    logits vs softmax probs (diagnostics per TASK-1: class mapping checks).
  - **Online fine-tune (`fine_tune_online`):** clones the live model,
    trains on the labeled rolling buffer with time-decay sample weights
    (`_compute_time_decay_weights`, 120-bar half-life — recent experience
    matters more), class weighting built from the ACTUAL buffer distribution
    (`_build_class_weights` with active_class_boost=3.0), oversampling of
    minority BUY/SELL, focal loss with label smoothing. Returns the
    fine-tuned model + metrics; the LIVE model is untouched until the caller
    (LiveEngine quality gate + bundle_lock) hot-swaps it. Note the
    intentional `self.focal_gamma = 1.0` override (ctor default 2.0 reduced
    for small-buffer stability — documented in-code). Oversampling
    (`_balance_oversample_dataset`) balances the tiny live buffer.
  - `ScalpWeightedDataset` yields (features, label, weight) triples for the
    weighted fine-tune path; `_resolve_batch_size` shrinks the batch for
    small samples (256 default → down to small buffers).
- **HOT PATH / PERFORMANCE:** NOT on the hot path. Training is
  to_thread'd; fold count 34 × 15 epochs is heavy — the trainer logs
  per-fold durations; online fine-tune is bounded to the rolling buffer
  (~300 rows) to keep the background task short.
- **EDGE CASES & PITFALLS:**
  - BUG-104 guard: default `artifact_save_path` = candidate path — never
    the live champion path without explicit intent.
  - `fold_size < 100` raises — small datasets can't produce honest folds.
  - Logs at lines 329-340 note the TASK-1 diagnostic mapping ("0=BUY,
    1=SELL, 2=NO_TRADE") which CONTRADICTS the actual label_map
    (0=NO_TRADE, 1=BUY, 2=SELL) — a stale diagnostic-only comment block;
    the authoritative mapping is `self.label_map` (verified in
    `_extract_X_y` / `_build_class_weights`). Documented as a doc-vs-code
    inconsistency (see issues ledger).
  - CrossEntropyLoss weight tensor is built against the 3-class label space;
    the 4-class head's WAIT column gets weight 1.0 implicitly (see
    `_build_class_weights`).