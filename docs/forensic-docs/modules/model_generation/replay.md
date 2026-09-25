# src/nexus_scalp/model_generation/replay.py

- **PURPOSE:** `SampleReplay` — the replay harness proving
  live=replay=training parity: replays a recorded sample stream through a
  model and compares predictions/drift against the recorded ones
  (`detect_feature_drift`, `detect_prediction_drift`, `replay_70d_vector`).
  The parity gate for the whole 70D contract.
- **ARCHITECTURE LAYER:** ML research (parity/verification).
- **RESPONSIBILITY:** (a) iterate recorded samples (same vectors the
  dataset builder produced); (b) run the runtime model; (c) compute drift
  metrics (feature-level and prediction-level distributions) with
  thresholds; (d) verdict: PARITY / DRIFT with evidence.
- **DEPENDENCIES:** runtime, sample frames, numpy, logging.
- **CONNECTS TO:** dataset builders (parity source), shadow70,
  tests (test_70d_replay_parity_task3).
- **KEY CONCEPTS:** Determinism is the assertion: identical input vectors
  must produce identical predictions regardless of path (dataset vs live);
  drift detection uses distribution distance (not raw diff) so benign
  noise doesn't false-positive.
- **EDGE CASES & PITFALLS:** Replay of a schema-mismatched sample set must
  fail loudly (parity on the WRONG geometry proves nothing); drift
  thresholds are configurable but must be identical between train and
  runtime evaluation.