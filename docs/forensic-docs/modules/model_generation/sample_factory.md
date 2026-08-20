# src/nexus_scalp/model_generation/sample_factory.py

- **PURPOSE:** `SampleFactory` — the deterministic sample pipeline:
  `deterministic_sample_id` (content-addressed sample identity),
  feature extraction + label assignment per sample contract, and
  `samples_to_frame` (samples → polars training frame with the canonical
  feat_0..feat_{n-1} + label columns).
- **ARCHITECTURE LAYER:** ML research (sample construction).
- **RESPONSIBILITY:** (a) content-addressed IDs (`deterministic_sample_id`
  — hash of the sample's market context) enabling dedup/reproducibility;
  (b) build SampleContract objects (features, label, metadata,
  provenance); (c) assemble the training frame with exact column geometry
  for the schema in use.
- **DEPENDENCIES:** polars, feature engines, labeling, domain models.
- **CONNECTS TO:** dataset builders, trainers, replay
  (replay consumes the same sample frame), tests.
- **KEY CONCEPTS:** The content address IS the invariance check — the same
  market snapshot always yields the same sample id (duplicate samples are
  detectable); the frame assembly enforces the schema's column arity
  (a 51-column frame for a 50D schema fails loudly).
- **EDGE CASES & PITFALLS:** Column-order stability (dict→frame must be
  ordered by the schema, never by insertion); sample ids must be stable
  across POLARS VERSIONS (hash the canonical serialization, not object
  repr).