# Research (bounded context)

Candidate validation pipeline: discovery → backtest → walk-forward → OOS →
robustness → score → registry lifecycle (DISCOVERED…VALIDATED→SHADOW→ACTIVE).

- Invariants owned by the backend registry state machine; the UI only renders
  verdicts and asks for confirm-guarded commands (`api.ts` -> `@/api/client`).
- Availability is backend-decided: `{available:false, reason}` renders verbatim,
  never a fabricated zero (`model.ts::toStrategyVo`, `useCases.ts`).
- Promotion requires an explicit actor id (lineage auditability, RC4 rule).
- RESEARCH-class gate failures are never retryable — technical/data only.
- Evidence: `src/nexus_scalp/web/debug_research_routes.py` (routes),
  `Web/app.js` tab-research (behavior reference), `api_v1/research.py` (v1).
- Shared lane-5 UI helpers live in `ui/lane5Kit.tsx` (owned folder).
