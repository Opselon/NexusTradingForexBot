# tests/unit/test_70d_bug106_incremental_phase19.py

- GUARDS: TASK-09 — BUG-106 verification + incremental 70D builder: byte-identical parity between canonical `compute_70d_frame` and the fast incremental `compute_70d_frame_fast` on the same real bars → ZERO feature diffs (proves the optimization preserves the contract).
- KEY ASSERTIONS:
  - Incremental vs canonical frame equality across scenarios; cache/hot-path reuse does not drift; incremental path returns identical 70D vectors (25 asserts).
- PITFALLS IT ENCODES: an optimization may only differ in SPEED, never in output — any feature diff is a regression; parity must hold over real bars, not toy inputs.
- NOTES: Regression guard for the BUG-106 incremental engine shipped in phase 19.
