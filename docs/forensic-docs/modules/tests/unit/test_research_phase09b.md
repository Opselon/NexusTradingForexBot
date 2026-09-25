# tests/unit/test_research_phase09b.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- PHASE 09B Strategy Research, Backtest & Validation engine behavioral suite — evidence-driven research layer with real observable assertions.
- Dataset builder: preserves temporal order; preserves provenance; FUTURE outcomes cannot enter discovery (`test_future_outcomes_cannot_enter_discovery`); future normalization cannot leak backward.
- Candidates: identity deterministic; version IMMUTABLE; creation reproducible (content-addressed).
- Backtest: deterministic; execution assumptions AFFECT the result (honest modeling); SL/TP behavior respected; friction modeled.
- Validation: negative OOS always rejects; robustness degradation measurable; NaN/Inf scores rejected (no silent pass).
- Registry: audit exposes rejection reasons; immutability of records.
- Fixtures: `seed_experiences` from AuditRepository with flush contract; `make_outcome` builders.
- 68 defs / 909 lines.