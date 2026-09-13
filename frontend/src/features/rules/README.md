# rules — bounded context

Trading rule matrix (legacy tab-rules): enable/disable + parameter edits.
Invariants: backend is authoritative — every toggle/parameter save is decided
by the `{success}` envelope and refetched on result; invalid parameter payloads
are blocked client-side via features/config/validation (numeric/required rules);
`parameters` stored JSON is surfaced verbatim (parse errors shown on the row).
Transport: ./api.ts (GET /api/rules, POST /api/rules/toggle).
EDD: diagnostics_state_routes.py:548-578; audit_repository.get_trading_rules.
