# HANDOFF: Provider rate-limit hardening (CHG-0034) — Nexus-Main, 2026-09-01

Agent: Nexus-Main
Role: Chief Orchestrator / Provider Hardening
Date: 2026-09-01
Branch: main
Starting HEAD: 912a539 (post Agent-3 rs16 sync)
Ending HEAD: 2948e50 (see git log for the full 8-commit series)

## Task
MASTER STEER 2026-09-01: provider rate-limit hardening + API/host health gate +
Strategy Factory toggle. Isolate the optional external-provider subsystem from
the core trading engine; kill the 429 retry storm; give the user ONE control.

## Scope (files changed)
- src/nexus_scalp/strategies/factory/provider_gate.py (NEW ~850L): global gate
- src/nexus_scalp/strategies/factory/provider.py: routed through gate
- src/nexus_scalp/settings/service.py: factory.* user-intent/auto-disable API
- src/nexus_scalp/web/factory_routes.py: provider-health/-toggle/-test routes
- src/nexus_scalp/news/ai_service.py: toggle guard + enabled_getter
- src/nexus_scalp/application/live_engine.py: ADDITIVE guard only (disclosed
  deferred-file touch: ledgered hardening fix, NOT decomposition; CHG-0032-A1)
- Web/index.html + Web/app.js: Strategy Factory feature card + JS functions
- tests/unit/test_provider_gate_hardening.py (NEW, 30 tests)
- agents/{change_control.md,taskboard.md,bugs.md,runtime_invariants.md}

## Functions/classes changed (function-level handoff)
- provider_gate.py: ProviderState, FailureCategory, DisableReason, GateConfig,
  GateResult, classify_config(), redact_url(), ProviderGate (validate_config /
  reconfigure / health_snapshot / execute -> _execute_inner -> _execute_gated ->
  _execute_paced -> _attempt_once), parse_retry_after(), classify_status(),
  execute_http_post(), get_provider_gate() singleton, _SingleFlightWaiter.
- provider.py: LLMGenerationProvider.__init__ (+gate/enabled_getter/config
  validation), available() (intent+gate aware), generate_dsls/complete_json
  (gate-routed), NEW _request_key/_absorb_gate_result; local httpx blocks removed.
- settings/service.py: get_factory_enabled, factory_auto_disable_state,
  factory_effective_enabled, set_factory_enabled, record_factory_auto_disabled,
  clear_factory_auto_disabled, factory_health_snapshot.
- factory_routes.py: GET /provider-health, POST /provider-toggle,
  POST /provider-test (+_factory_enabled_safe helper, llm-config hot-rebuild
  now wires enabled_getter + gate.reconfigure()).
- ai_service.py: resolve_factory_provider guard + enabled_getter.
- live_engine.py: _build_factory_llm_provider early-return guard.

## Contracts
- PROVIDER_HEALTH_GATE v1 (new), PROVIDER_USAGE v2 (category/reason fields via
  last_error), SETTINGS_DB factory.* keys v2, INV-024 (new invariant).
- NOT touched: 70D/scalp_v3/model artifacts, feature schema, execution, risk,
  research paths, thresholds (steer 53-57 honored).

## Verification (real execution evidence)
- pytest tests/unit/test_provider_gate_hardening.py: 30/30 PASS (16.5s).
- ruff check + format: clean on all touched py files; mypy: clean (5 files).
- node --check Web/app.js: PASS.
- Config matrix probe: missing key/bad scheme/valid -> correct DisableReason;
  auto-disable fires at construction with zero HTTP calls.
- Trading isolation: worst simulated tick 0.08-0.8s class vs provider storm
  0.3s+backoff — provider wait != trading-loop wait (assert < 0.2s hard bound).

## Live smoke
- NOT executed against the real provider (steer 73: safe-environment only; the
  provider is a live LLM endpoint). The /provider-test endpoint is the
  designated one-shot smoke instrument for the operator.

## Known absorbed work (disclosure)
- Commit e5b0e2f (amended 47c2843) absorbed AGENT-2's numeric-entropy
  redaction exemption (observability/logging.py + test) staged in the same
  window — disclosed in the commit body; content reviewed, additive, owned
  by AGENT-2.

## Bugs fixed: BUG-186, BUG-187. Bugs discovered: none new.
## Risks
- provider-test builds a throwaway provider when factory.provider is absent —
  intentional (probe must fire even pre-start), bounded by 30s timeout.
- No restart required: settings are hot; the gate is a process singleton.

## Next-agent instructions
- Runtime owners should run the /provider-test endpoint once against their
  real endpoint and record the result in the taskboard row.
- If the provider defines an official key format, extend classify_config with
  a format check (do NOT invent prefix rules — steer section 9).
