# src/nexus_scalp/model_generation/sequence.py

- **PURPOSE:** `SequenceBuilder` — builds 3D SEQUENCE samples
  (Batch, Seq, Features) from bar history: each training sample becomes a
  window of feature vectors (e.g. 10 bars × 50D) so the causal TCN /
  self-attention path of ScalpNet (or the TCN_ATTENTION_V1 architecture)
  can be trained. `_ts_us` normalizes timestamps to microseconds UTC.
- **ARCHITECTURE LAYER:** ML research (sequence construction).
- **RESPONSIBILITY:** (a) window assembly (fixed sequence length,
  strictly past-ward — the last element is the decision bar);
  (b) label alignment (the sample's label comes from the decision bar's
  triple-barrier outcome); (c) sequence metadata (depth, coverage).
- **DEPENDENCIES:** numpy, feature frames, labeling, logging.
- **CONNECTS TO:** sequence_training (SequenceCandidateTrainer),
  dataset builders (sequence variants), tests
  (test_model_generation_phase13).
- **KEY CONCEPTS:** Causality at the sequence level: window elements are
  all ≤ the decision bar; the sequence dimension is the input to the 3D
  model path — the SAME causal guarantee the 2D path has, extended in
  time.
- **EDGE CASES & PITFALLS:** Short history at the dataset head must not
  produce partial windows (drop + log, or left-pad with the FIRST
  available vector — the choice must be identical between train and
  replay); sequence length is a manifest-level contract (train and
  runtime must agree).