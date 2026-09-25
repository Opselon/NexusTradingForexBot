# src/nexus_scalp/model_generation/training.py

- **PURPOSE:** `CandidateTrainer` — the artifact-track training loop for
  candidates (distinct from the walk_forward_trainer): dataset-hash
  verified input (`dataset_hash_value`), deterministic candidate ids
  (`deterministic_candidate_id`), gradient-norm guard (`_grad_norm`),
  train/validate split honoring news on/off, and quality-gated model
  saving.
- **ARCHITECTURE LAYER:** ML research (training track).
- **RESPONSIBILITY:** Train a candidate from a verified dataset frame;
  produce the candidate artifact ONLY if the quality gates pass
  (non-finite loss or grad norm > 5 → FAILED, never saved as a candidate).
- **DEPENDENCIES:** torch, polars, features schema, validation factory,
  artifact store, logging.
- **CONNECTS TO:** experiment_factory (which drives training),
  benchmark, model registry, tests (test_model_generation_phase13).
- **KEY CONCEPTS:** The dataset hash travels with the run (a candidate is
  meaningless without its exact training data identity); candidate id is
  content-derived (deterministic from dataset + config + seed);
  `_split_columns` separates base vs news feature columns per the schema
  family so news-ablation is a config switch, not a code change.
- **EDGE CASES & PITFALLS:** A failed/interrupted run stays FAILED/
  INCOMPLETE — never VALIDATED (governance invariant); the grad-norm
  guard catches divergent training EARLY (abort on norm > 5).