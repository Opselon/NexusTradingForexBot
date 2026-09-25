# debug — bounded context

The full debug hub (legacy tab-debug), rebuilt as a SOLID decomposition:

- `ui/DebugPage.tsx` — page shell only: hero header + cache-mirror status
  rail + tab rail + A/B compare handoff state.
- `ui/tabs/*.tsx` — one backend surface per tab (SRP): State
  (/api/debug/state), Health, Features (/api/debug/features), Freshness,
  IPC telemetry, Compare (/api/debug/compare), Snapshots, Model test
  (POST /api/debug/model-test), Trace, Research read-only panels, and Ops
  (simulation injector + telegram notifier — the two legacy-parity panels
  the old page never mounted).
- `ui/sorting.tsx` — the single sorting primitive (useSortState / sortRows /
  SortTh). Nulls sink in both directions; `key: null` = backend order.
- `ui/debug.css` — the trading-terminal skin, namespaced `dbg-`, imported
  from the page component; tokens only, logical properties for RTL.
- `api.ts` (transport) → `useCases.ts` (queries/mutations) → `hooks.ts`
  (barrel) → `ui/` (presentation); `model.ts` holds the pure VOs/diff logic.

Backend contract notes (verified live on :59273, 2026-09-22):
- /api/debug/compare SUCCESS payloads carry NO `available` key; only the two
  failure paths send `available: false` + reason. The renderer checks
  `available === false` explicitly (a truthiness check mislabels every real
  diff as unavailable).
- IPC events carry `timestamp/action/reason/latency/execution_mode` and may
  have no state/status key — the state column falls back to execution_mode.
- /api/debug/trace answers 200 with `available: true` and a reason (e.g.
  NO_AUDIT_DB) when the join is empty — that reason is surfaced verbatim.
- POST /api/debug/model-test takes `{features: number[] | null,
  use_live_features: boolean}`; dimension comes from the live features read,
  never hardcoded.

EDD: debug_research_routes.py:81-300,800-816, debug_snapshot.py (store/diff),
config/validation.ts (vector gate). Polling is operator-controlled per tab;
every UNAVAILABLE section shows its backend reason + correlation id.
