# src/nexus_scalp/model_generation/runtime.py

- **PURPOSE:** `LocalModelRuntime` — the artifact-first INFERENCE runtime:
  loads a model artifact from its manifest (schema hash, feature
  dimension, class count, scaler), validates everything
  (`validate_and_load`, `ManifestValidationError` for any mismatch), and
  serves predictions — inference needs NO database (the Phase-13
  contract).
- **ARCHITECTURE LAYER:** ML research (runtime/serving for candidates;
  the LIVE path uses the LiveEngine bundle, this is the candidate/shadow
  serving runtime).
- **RESPONSIBILITY:** (a) manifest validation (schema hash vs canonical,
  feature_dim vs manifest dim, classes == 4, scaler presence);
  (b) model + scaler load; (c) predict (with the same tensor hygiene:
  finite, [-3,+3], float32).
- **DEPENDENCIES:** torch, schemas (schema_contract), artifact store,
  logging.
- **CONNECTS TO:** shadow70 runtime (candidate serving), benchmark,
  replay, model-lifecycle champion load verification, tests.
- **KEY CONCEPTS:** Fail-closed: ANY manifest mismatch → ManifestValidationError
  (never a partial load); provenance: every prediction can be traced to the
  exact artifact (id/hash).
- **EDGE CASES & PITFALLS:** The scaler must be the SAME one used on the
  training frame (scaler hash check); a manifest without scaler is rejected
  for scaler-dependent models (never silently un-scaled inference).