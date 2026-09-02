# tests/unit/test_research_observability_phase21.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- TASK-21 Strategy Research & Validation Engine observability behavioral suite (spec 5/6/8/9/10/11/12/14/26/29/30/31/44/45/55): gate observability, timeline events, evidence vault, research runs.
- Gate observability: one gate created per gate type; gate status lifecycle (pending→pass/fail); failed gate records reason AND class; blocked gate carries explicit reason; retry allowed ONLY for technical failures.
- Timeline: events persisted and ORDERED (monotonic append-only).
- Evidence vault: EVERY gate stores evidence; evidence immutable (hash-verified, tamper detectable).
- Research runs: append-only immutability (rows never rewritten).
- Fixtures: `temp_audit_repo` + `flush` (queued-writer `_queue.join()` contract — do NOT close() mid-test, BUG-058); `make_record`/`make_outcome`/`build_candidate` builders.
- 41 defs / 580 lines.