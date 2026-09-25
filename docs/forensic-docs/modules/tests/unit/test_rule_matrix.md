# tests/unit/test_rule_matrix.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- RuleMatrixEngine behavioral suite (DB-seeded rules): TTL throttling, pre-trade entry rules (FVG sniper, Judas, order blocks), spread/liquidity/macro filters, in-trade exits, risk/safeguards, API endpoints, dynamic hold scores.
- Pitfall encoded: fresh test DBs default ALL trading rules DISABLED — a rule filter only fires after `toggle_trading_rule(..., True)` + `refresh_cache(force=True)`; a bare engine with no rules filter has `rule_matrix=None` semantics.
- Guards: pre-trade `fvg_sniper`/`judas`/`orderblock` block proposals (`proposal is None` with explicit `block_reason`); spread squeeze + liquidity + macro filters gate entries; in-trade exits evaluated; policy hooks blocked by filter; order-manager exit hooks respect rule verdicts.
- TTL throttling: cache TTL 5s behavior asserted via `mock_get_trading_rules`; database seeding and toggling roundtrip.
- API endpoints exposed by the rule subsystem return truthful rule status; dynamic hold-score calculation deterministic.
- 15 defs / 485 lines; AuditRepository-backed (`temp_audit_repo` fixture + flush).