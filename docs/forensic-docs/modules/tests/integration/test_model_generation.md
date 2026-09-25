# tests/integration/test_model_generation.py

- GUARDS: PHASE 13 Model Generation Migration — artifact-first flow end-to-end (spec 46): dataset artifact build + manifest inspection, legacy-baseline training through the new pipeline, champion artifact lifecycle.
- KEY ASSERTIONS:
  - `TestModelGenerationEndToEnd`: deterministic dataset artifact (manifest + hash), training produces a valid model artifact, artifact loads and classifies, corrupt/tampered artifacts rejected (26 asserts).
- PITFALLS IT ENCODES: artifact-first means hash/schema integrity gates ship with generation; tests exercise the SAME artifact store the runtime loads from.
- NOTES: Integration counterpart of the unit behavioral suite test_model_generation_phase13.py.
