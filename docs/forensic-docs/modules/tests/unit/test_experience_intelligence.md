# tests/unit/test_experience_intelligence.py

- GUARDS: Phase 08 Experience Intelligence — behavioral suite: the Experience-Driven Strategy Intelligence subsystem. Every test asserts OBSERVABLE BEHAVIOUR (persisted rows, computed outcomes), not internals.
- KEY ASSERTIONS:
  - experience records produced from trades with authoritative outcomes; strategy health/ratings degrade and recover; trainings/backtests recorded with provenance; experience gate rejects harmful strategies before dispatch; Telegram renders truth states (219 asserts).
- PITFALLS IT ENCODES: behaviour-only assertion style; outcome ids must be authoritative (never invented); gate ordering (experience rejection happens before any order attempt).
- NOTES: Largest unit file in the slice (1698 lines); pairs with the integration boundary suite test_experience_execution_boundary.py.
