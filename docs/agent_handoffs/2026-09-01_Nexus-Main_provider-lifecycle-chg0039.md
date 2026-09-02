# HANDOFF: Provider Gate Lifecycle Hardening (CHG-0039) — Nexus-Main, 2026-09-01

Agent: Nexus-Main
Role: Provider Lifecycle Hardening
Date: 2026-09-01
Branch: main
Starting HEAD: 6f5d29e (CHG-0034 live smoke PASS)
Ending HEAD: f3f9654 (my series), main tip 4d468ee (parallel agents)
CI: run 33552426447 (main, contains f3f9654) = SUCCESS (Code Quality & Tests: success)

## Task
Post-certification lifecycle layer per user steer: state ownership, credential
rotation recovery, restart semantics, operator UX consistency. CHG-0034
rate-limit/circuit implementation NOT reopened (no evidence to do so).

## DEFECT FOUND AND FIXED — BUG-189 (live-confirmed)
- Forensic phase reproduced ON THE LIVE ENGINE: /api/factory/provider-health
  reported top-level user_enabled=true / auto_disabled=false /
  effective_enabled=true while the gate block reported
  provider_state=AUTO_DISABLED reason=AUTH_FAILED (real 401).
- Root cause: the settings DB persisted an auto_disabled flag that NO
  production path ever wrote (record_factory_auto_disabled: zero production
  callers), while the runtime disable lived only in the ProviderGate singleton.
  The UI label read the settings layer.

## State machine (documented from source, no new states invented)
- configured:        key+base+model present (settings)
- enabled:           factory.enabled (PERSISTED user intent, default true)
- auto_disabled:     RUNTIME ONLY (gate singleton; never persisted)
- effective_enabled: enabled AND NOT gate.auto_disabled (merged at read time)
- provider_state:    AVAILABLE/RATE_LIMITED/DEGRADED/CIRCUIT_OPEN/HALF_OPEN/
                     AUTO_DISABLED (gate runtime)
- circuit_state:     transient, inside gate, never persisted
Contradiction class eliminated: one runtime authority + one intent store.

## Persistence split (steer §7/§14)
- PERSISTED: factory.enabled (user intent) — survives restart.
- RUNTIME-ONLY: auth failure, auto-disable, circuit, backoff — deliberately
  NOT persisted so a rotated key can never inherit stale AUTH_FAILED and a
  transient outage can never become sticky configuration.
- record_/clear_factory_auto_disabled remain for API compat (documented).

## Credential rotation lifecycle (deterministic)
401 -> AUTO_DISABLED (exactly one provider hit; later requests short-circuit
with zero traffic) -> operator replaces key -> Save & Reload (reconfigures the
process singleton, NO network, also in web-only mode) -> Test Provider (ONE
gated probe; reconfigures first) -> READY -> Enable -> ACTIVE.
Rotation resets ONLY provider state; unrelated settings/counters/history kept.

## Restart matrix (all covered by tests A-E)
A auth-failed + fixed key -> boot re-validates, AVAILABLE.
B auth-failed + still-bad key -> re-auto-disables at construction, zero hits.
C valid credential -> AVAILABLE.
D manual disable -> SURVIVES restart (persisted intent).
E circuit-open -> never persisted; fresh gate clean.

## provider-test contract
Still operator-triggered, bounded (one gated probe, single-flight, no retry
amplification), state-aware, credential-redacted. Pre-probe reconfigure added
so a rotated key is verifiable without Enable first (still one request).

## Files changed (this task)
- src/nexus_scalp/settings/service.py: factory_health_snapshot(runtime_override)
- src/nexus_scalp/web/factory_routes.py: authoritative health merge (gate ->
  top-level fields), web-only save reconfigure, probe pre-reconfigure
- tests/unit/test_provider_lifecycle_hardening.py (NEW, 18 tests)
- agents/{change_control.md,bugs.md,taskboard.md}: CHG-0039, BUG-189, row

## Intentionally NOT implemented
- No persistence of runtime auto-disable (would be sticky — forbidden).
- No new states/reasons (existing taxonomy reused).
- No observability-log-contract.md edits (not required — edge-triggered
  logging already compliant; AUTO_DISABLED/RECOVERED single events verified).
- No CLI provider surface (nexus doctor/health do not surface provider state;
  creating one would duplicate the engine — recorded as future option).
- CHG-0034 rate-limit/circuit internals untouched.

## Verification
- 18/18 lifecycle + 30/30 gate + bug131 + settings/phase22 suites PASS.
- ruff check/format, mypy, py_compile: clean.
- CI run 33552426447 (main incl. f3f9654): SUCCESS.
- Live engine (PAPER) re-verified post-fix: engine_running=true.

## Residual risks
- UI wording still maps reason strings directly (AUTH_FAILED renders as the
  code value); a human-readable reason map is a cosmetic follow-up.
- Concurrent Save&Reload + toggle are serialized per-key by the settings DB
  lock; the gate reconfigure is idempotent, so worst case is a redundant
  reconfigure — no contradictory state possible (tested).
