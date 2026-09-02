# tests/unit/test_research_task4_validation.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- TASK-4 data-integrity regression tests (part 2) — TEST-RS-15..RS-24: family-select validation, OOS gate, robustness, scoring, candidate identity, registry immutability, worker rebuild guard, health diagnostics.
- Guards: negative OOS ALWAYS rejects (RS-15); robustness degradation measurable (RS-16); NaN/Inf score rejected (RS-17) — no silent acceptance; candidate identity deterministic (RS-18); definition change → NEW version (RS-19); registry IMMUTABLE, no rewrite (RS-20); audit EXPOSES rejection reasons (RS-21).
- Worker: does REAL work when new experience exists (RS-22); NOOP when dataset unchanged (RS-23) — worker rebuild guard.
- Family-select validation (RS-24); no automatic activation of weak candidates.
- 14 defs / 274 lines; `repo` + `_candidate_with_family` fixtures.