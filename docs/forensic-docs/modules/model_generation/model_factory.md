# src/nexus_scalp/model_generation/model_factory.py

- **PURPOSE:** `ModelFactory` — the artifact-first model builder:
  constructs neural nets (incl. the lightweight `SimpleMLP` baseline,
  plus the ScalpNet family) from a parameter manifest, with
  `infer_feature_dim` resolving the feature width from the schema
  (scalp_v1=50, v3=70, ...). The factory is how a dataset manifest's
  feature_dimension becomes a real torch module.
- **ARCHITECTURE LAYER:** ML research (model construction).
- **RESPONSIBILITY:** (a) `SimpleMLP` — the baseline/ablation network;
  (b) build by architecture key (LEGACY=ScalpNet, TCN_ATTENTION_V1, ...)
  with the manifest's hyperparameters; (c) dimension inference from
  schema_id so a 70D artifact manifest yields a 70-input model
  automatically.
- **DEPENDENCIES:** torch, architectures module, features/schema.
- **CONNECTS TO:** training (CandidateTrainer builds models here),
  benchmark (LEGACY vs TCN cells), model_lifecycle comparison, runtime
  (load via manifest), tests.
- **KEY CONCEPTS:** The manifest is the single source of model truth —
  a model is fully rebuildable from (schema_id, architecture,
  hyperparameters) + dataset hash; dimension inference removes the
  hand-wired 50/70 constants from the factory path.
- **EDGE CASES & PITFALLS:** An unknown architecture key must fail loudly
  (never silently fall back to ScalpNet — a TCN request becoming MLP
  would invalidate comparisons); width mismatches (infer_feature_dim vs
  actual manifest) must raise at build time.