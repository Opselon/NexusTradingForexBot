# Factory (bounded context)

Autonomous strategy evolution control room: loop state + provider health,
generate/evaluate/complete, generations/candidates/benchmarks/failures/
ranking/evolution-memory/event console, safe LLM config status (masked-only).

- Invariant (module contract): the factory NEVER touches the live trading
  path; candidates enter validation only. The UI states this in its banner.
- Every section renders the backend's {available:false, reason} verbatim when
  the factory is not mounted — no fabricated boards. Loop commands and the
  CHG-0034 provider enable/disable are confirm-guarded; the response decides.
- API-key editing intentionally absent (secrets never round-trip in this UI).
- Evidence: factory_routes.py, strategies/factory/*; Web/app.js tab-factory.
