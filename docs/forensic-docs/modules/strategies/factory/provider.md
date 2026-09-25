# src/nexus_scalp/strategies/factory/provider.py

- PURPOSE: LLM Generation Provider — OPTIONAL assisted generation
  (2026-08-20, spec 33/34/69/70): the external LLM produces strategy DSL
  hypotheses; the factory NEVER depends on it for correctness (deterministic
  generators are always the base; `available()` False ⇒ orchestrator uses
  the deterministic path). Provider contract: config-driven credentials
  (api key from the SecureSecretStore, never hardcoded/logged, spec 33/91);
  NEVER raises into the orchestrator (every failure returns [] and records
  PROVIDER_FAILURE-class usage); returns ONLY structured JSON (invalid JSON
  repaired once, then rejected safely, spec 34/90); NEVER computes or
  claims performance (research pipeline is the only measured source, spec
  69/70); tracks requests/tokens/estimated cost/latency for the cost-control
  ledger (spec 45/97).
- ARCHITECTURE LAYER: Application integration (httpx I/O in a worker
  thread; no order authority, no performance claims).
- RESPONSIBILITY: build the prompt (system rules + feature catalog +
  budget constraints + research memory), call the OpenAI-compatible
  /chat/completions endpoint with response_format json_object, extract DSL
  dicts (repair-once), track usage.
- DEPENDENCIES: `settings.secret_store` (SecureSecretStore,
  LLM_API_KEY_SECRET="factory.llm_api_key"), httpx (imported lazily —
  missing dependency ⇒ []), observability.logging.
- CONNECTS TO: orchestrator (provider injected; `_generation_zero_population`
  leaves the LLM slice for this provider — the orchestrator itself does not
  currently call provider.generate_dsls; the wiring is present, the caller
  side is the orchestrator consumer), usage ledger (store.record_provider_
  usage), cost/API surfaces.

- KEY CONCEPTS:
  - Budget guards (43-45): DEFAULT_MAX_REQUESTS_PER_GENERATION=60,
    DEFAULT_TIMEOUT_SEC=45, DEFAULT_MAX_TOKENS=4096; `_budget_exhausted`
    (usage.requests >= max) → [] with last_error.
  - `ProviderUsage` (52-78): thread-safe counters — requests/failures,
    prompt/completion/total tokens, last + total latency, last_error;
    snapshot() computes estimated cost at a fixed blended price
    ($2/1M tokens: total_tokens × 0.000002).
  - `LLMGenerationProvider` (81-309): provider_name "openai-compatible",
    prompt_version "factory-dsl-v1" (scheme for recording which prompt
    version produced a candidate, spec 86).
    - `_load_key` (121-128): reads LLM_API_KEY_SECRET from the secret
      store at construction; failures → "" (unavailable).
    - `available()` (130-132): base URL + model + key all truthy.
    - `generate_dsls(prompt_context, n)` (141-224): unavailable/budget
      guards → []; httpx.post with Authorization Bearer; non-200 /
      JSON parse failure / missing choices ⇒ failures++ and [];
      usage.usage tokens accumulated; content extracted and passed to
      `_extract_dsl_list`, sliced to n. RETURNS RAW UNVALIDATED DSL dicts —
      orchestrator runs the full structural gate chain; invalid entries
      rejected with UNSUPPORTED_FEATURE / INVALID_SCHEMA / LOOKAHEAD_RISK.
    - `_extract_dsl_list` (230-246): try direct parse; dict with
      "strategies" list, or bare list; else `_repair` (strip ``` fences,
      keep text between first { and last }) and re-parse; still nothing
      → [] with last_error NO_VALID_STRATEGIES_IN_RESPONSE. Repair-once:
      repaired garbage is rejected, never retried.
    - `_build_messages` (274-309): system prompt = role definition +
      catalog-only rule + "never claim performance" + simplicity rule +
      hypothesis requirements (statement, market_mechanism, expected_regime,
      invalidation, abstain_conditions) + strict JSON-only + catalog/
      timeframes/symbols/complexity-limit context + DSL schema block +
      OOS-vs-simple preference; user = n distinct strategies + research
      memory (previous generations) + generation objective + diversity
      requirement.
- HOT PATH / PERFORMANCE: network call is 45s-timeout bounded; usage
  counters per call; the provider is only reached in the factory worker
  cycle (asyncio.to_thread), never the tick path.
- EDGE CASES & PITFALLS:
  - Cost model is a FIXED blended $2/1M tokens constant hardcoded in
    snapshot(); docstring says "configurable via constructor" but no
    constructor parameter exists — configuration promise unfulfilled.
  - `_window_requests` is incremented but NEVER used for rate limiting —
    the only budget is the cumulative usage.requests counter; the
    "per-generation window" is TOTALS since construction, so two
    generations in one provider instance share the 60-request budget.
  - `_extract_dsl_list` slices to n AFTER parsing; a model returning fewer
    valid dicts is accepted silently (no error), which the orchestrator
    absorbs via template fallback.
  - `temperature`/`seed` are sent? seed is stored but NOT included in the
    payload (only temperature/max_tokens/response_format) — the
    reproducibility intent of `seed` is not realized at the API level.
  - The token fields from the model `usage` may be absent (some providers);
    `int(... or 0)` guards — usage then misreports zero tokens at real
    cost.
  - No retry logic on network failure — a transient flap simply returns
    [] (mirrors news/analysis pattern; failure recorded in usage).