# src/nexus_scalp/model_generation/sequence_training.py

- **PURPOSE:** `SequenceCandidateTrainer` — training for 3D sequence
  models (the TCN/attention candidates): takes sequence batches from
  SequenceBuilder, trains with the same quality discipline as the 2D
  CandidateTrainer (grad-norm guard `_grad_norm`, deterministic ids,
  early stopping), and produces sequence-model artifacts.
- **ARCHITECTURE LAYER:** ML research (sequence training track).
- **RESPONSIBILITY:** (a) dataloader assembly for (B, S, F) tensors;
  (b) epoch loop with validation; (c) quality gates then artifact save.
- **DEPENDENCIES:** torch, SequenceBuilder, validation factory,
  artifact store, logging.
- **CONNECTS TO:** experiment_factory (sequence experiments), benchmark
  (TCN cells), model registry, tests.
- **KEY CONCEPTS:** Sequence models are heavier — batch sizing and
  gradient accumulation matter; the grad-norm guard (abort > 5) matters
  MORE here (deep stacks diverge faster); artifacts record sequence
  length in the manifest so runtime serving knows the expected window.
- **EDGE CASES & PITFALLS:** A sequence model artifact loaded by a 2D
  runtime path must be rejected via manifest validation (dimension/
  shape mismatch); memory: (B, S, F) batches grow quadratically with S —
  batch resolution must account for it.