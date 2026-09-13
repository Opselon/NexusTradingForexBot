# features/account — Accounting bounded context

Replaces legacy `tab-account` (Web/app.js accounting block). All routes are
legacy raw-JSON (`/api/account/*`, `/api/live/accounting`) — typed calls live in
api.ts over the stable `@/api/client` exports (Lane 1 owns src/api/accountingApi).

- **types.ts** — DTOs mirroring debug_research_routes.py + diagnostics_state_routes.py.
- **model.ts** — row normalizer (broker OR ledger shape), plan-input validation,
  advanced-metrics display rows; nulls stay null (BUG-020: no synthetic numbers).
- **useCases.ts / hooks.ts** — `available:false` raises AccountingUnavailableError
  (an honest section error, never zeros); keys `["account", …]`.
- **ui/** — summary + period hero, equity/drawdown/growth/cumulative charts
  (components/viz), advanced metrics, series, intelligence report, strategies,
  trades + forensic drawer with PnL waterfall, live risk-plan panel.

Invariants: lot/risk math is server-side (RiskEngine); drawdown methodology is
the accounting core's; this console is read-only over financial truth.
