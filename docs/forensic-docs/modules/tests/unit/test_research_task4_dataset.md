# tests/unit/test_research_task4_dataset.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- TASK-4 data-integrity regression tests (part 1) — TEST-RS-01..RS-14: dataset eligibility, zero-substitution, research-sample contract, discovery, family distribution.
- Guards: canonical economic trade reaches the research sample; missing PnL NEVER zero-substituted; missing R NEVER zero-substituted; duplicate economic trade counted ONCE; strategy context survives; schema version survives; 50D reproducible after 60D; invalid label rejected; future outcome rejected (causality).
- Family: grouping deterministic; distribution reports truthful; sample-floor rejection EXPLICIT (RS-14); small-sample tie handling (RS-14b).
- Fixture: AuditRepository-backed `repo` with queued-writer flush before reads.
- 15 defs / 301 lines. Companion: test_research_task4_validation.py (RS-15..24).