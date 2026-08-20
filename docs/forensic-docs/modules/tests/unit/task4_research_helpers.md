# tests/unit/task4_research_helpers.py

- GUARDS: TASK-4 regression helpers: ledger seeding with AUTHORITATIVE broker outcomes — the bridge between raw ledger rows, execution ids, and the outcomes the accounting/forensics suites assert on.
- KEY ASSERTIONS: none (pure factory helpers, no asserts).
- PITFALLS IT ENCODES: outcome rows must be seeded with the authoritative broker PnL so tests asserting "no fabricated numbers" have a real source of truth; helpers keep the make_record/make_outcome pair in lockstep so idempotency chains (execution_id → outcome) stay consistent.
- NOTES: `make_record(...)` (ledger row), `make_outcome(...)` (broker outcome), `seed_experiences(...)`. Imported by the task4 research suites (test_research_task4_*, test_70d_model_validation_task4).
