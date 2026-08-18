# GOLDEN TESTS — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §23 (see `agents/multi-agent-git-contract.md`).
> Real/controlled canonical examples for critical contracts. Golden tests
> protect against semantic drift.
> Any intentional golden change requires: explicit reason, contract/version
> update, changelog/handoff.

## Intended coverage

- FEATURE_VECTOR_50D (canonical 50-float vector, ordering, clipping)
- TRADE_OUTCOME (canonical outcome records, EXIT_CLASSIFICATION semantics)
- EXIT_CLASSIFICATION (UNKNOWN stays UNKNOWN — DEC-0001)
- ACCOUNT_SNAPSHOT / ACCOUNTING_SNAPSHOT
- NEWS_CONTEXT
- MODEL_MANIFEST

## Status

- (empty — directory initialized 2026-08-18; golden fixtures to be added
  with the tests/golden/ harness as contracts are touched.)

## Rules

1. Golden files are controlled canonical examples — treat as contracts.
2. A golden change requires an explicit reason + contract/version update +
   handoff note.
3. Reference golden fixtures from regression tests; do not silently drift.
