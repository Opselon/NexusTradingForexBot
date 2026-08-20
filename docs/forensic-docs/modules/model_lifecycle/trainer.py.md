# src/nexus_scalp/model_lifecycle/trainer.py

- **PURPOSE:** ChallengerTrainer — controlled OFFLINE training producing a
  CANDIDATE model, never a production model (spec 16/25/33). Reuses the existing
  production-grade `WalkForwardTrainer` (schema-driven, purged walk-forward,
  class-balanced, anti-collapse) instead of duplicating training (spec 11). Its
  ONLY addition is the safety boundary: artifacts are written to
  candidate/staging paths; the Champion artifact is NEVER overwritten.
- **ARCHITECTURE LAYER:** Research/ML — offline training, no order authority.
- **RESPONSIBILITY:** Convert TrainingDataset → Polars frame → WalkForwardTrainer
  → inspect produced artifact → record immutable TrainingRun (COMPLETED/FAILED).
- **DEPENDENCIES:** polars, champion.ChampionManager, integrity.inspect_artifact,
  models, training.walk_forward_trainer.WalkForwardTrainer, uuid/time.
- **CONNECTS TO:** orchestrator (invokes train), store (persists the run),
  champion manager (candidate paths + parent lineage).

- **KEY CONCEPTS:**
  - `train()` (line 70): run_id default `tr_<uuid12>`; TrainingRun starts RUNNING
    with embargo/purge bars from hyperparameters (default 15); parent champion
    lineage filled from `champion_or_none()` (lines 98-105, empty when cold
    start). Candidate paths come from `champion_manager.candidate_artifact_path`
    (staging, line 107).
  - Frame conversion `_to_polars_frame` (line 202): deterministic labeled frame
    with label, zeroed OHLCV, timestamp and `feat_0..feat_{n-1}` from the ordered
    rows — zeroed OHLCV is a deliberate convention: WalkForwardTrainer consumes
    features not price (per its contract), kept here for shape compatibility.
  - Trainer wiring (lines 126-140): num_folds default 34, batch_size 256, lr 5e-4,
    epochs_per_fold 10, early stopping patience 3, purge_gap_bars 15; artifact
    save path = candidate path; `trainer._get_scaler_path = lambda: cand_scaler`
    (line 140, type-ignored monkey-patch) — the staging scaler path so the
    trainer never overwrites the champion's `model.scaler.npz`.
  - Post-train inspection (line 146): `inspect_artifact(cand_path, cand_scaler)`
    with model_id `candidate_<run_id>`; metrics filled from trainer attributes
    (last_val_loss, last_validation_accuracy, final_loss — whichever exist;
    defaults None). Completion ⇒ status COMPLETED.
  - FAILURE CONTRACT (lines 182-196): ANY exception (empty frame, missing
    columns, trainer/loss failure) ⇒ TrainingRun FAILED with failure_reason; an
    interrupted run is therefore never VALIDATED (the status ladder enforced
    here and in worker._restore_inflight_state).
  - `_validate_columns` (line 218): explicit missing-column and feat_-count
    mismatch errors (no silent reshape).
  - `summarize_run` (line 230): JSON-friendly run summary for APIs/dashboards.
- **HOT PATH / PERFORMANCE:** Offline only; `train_and_validate` is the heavy
  PyTorch bulk (never on the tick path — worker invokes it through to_thread at
  the orchestration level). Batches 256/10-epoch folds bounded by hyperparams.

- **EDGE CASES & PITFALLS:**
  - `metrics["final_loss"]/["validation_accuracy"]` stay None unless the trainer
    exposes them — GATE4/GATE5 then FAIL (correct: no evidence ⇒ fail), making a
    silent-victory run effectively REJECTED at validation despite COMPLETED
    status.
  - The monkey-patched scaler path (line 140) depends on the private
    `_get_scaler_path` attribute of WalkForwardTrainer — a trainer refactor
    silently breaks the staging guarantee.
  - `feature_cols` default `feat_0..feat_{dim-1}` must match the frame columns
    exactly; `_to_polars_frame` builds them in order, so column count mismatch is
    caught by `_validate_columns`.
  - Duplicate run_id (retry with same id) overwrites the staging dir — safe
    (candidate path) but the old candidate files persist.