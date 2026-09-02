# tests/unit/test_70d_dataset_parity_task3.py

- GUARDS: TASK-03-70D-PARITY — canonical 70D snapshot + dataset builder (TEST-70D-PARITY-06/07/08/09/10/19/39/40/41): dataset builder produces 70D; replay via canonical engine produces identical 70D; dataset == snapshot identity.
- KEY ASSERTIONS:
  - `compute_70d` output equals persisted dataset rows; replay of the same causal window is bit-exact; crafted datasets keep provenance + temporal split + purge + embargo (29 asserts).
- PITFALLS IT ENCODES: bit-exactness over the causal window is the invariant — any divergence between builder and replay is a parity break.
- NOTES: Uses golden70d corpus helpers; same split/purge/embargo preserved into the artifact.
