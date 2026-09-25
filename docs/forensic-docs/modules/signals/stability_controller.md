# src/nexus_scalp/signals/stability_controller.py

- **PURPOSE:** `DecisionStabilityController` — a post-policy stability
  arbitration layer: given a proposed action and the recent decision
  history, it decides whether to STABILIZE (confirm), BLOCK, or switch to a
  stable alternative — stopping model/policy flip-flop from becoming order
  spam at the source. Emits `StabilityDecision` + `StabilityEvent` records
  for audit.
- **ARCHITECTURE LAYER:** Signals (stability gate).
- **RESPONSIBILITY:** (a) maintain a bounded event history (`events()`,
  `last_event()`); (b) `decide(...)` — apply stability logic to the incoming
  action: same-direction persistence check (a direction change needs N
  confirmations or a confidence delta), frequency/cooldown check, and
  hysteresis against `StableDirection` state; (c) reset on regime/mode
  change.
- **DEPENDENCIES:** dataclasses, `StrEnum` states (StableDirection/
  StabilityState), logging.
- **CONNECTS TO:** SignalPolicy (orchestrator of the cascade; stability is
  evaluated after the numeric gates), audit/telemetry consumers, tests.
- **KEY CONCEPTS:** The controller is deliberately STATE-FULL across ticks —
  the persistence/hysteresis memory the pure policy lacks; its thresholds
  and window are constructor-tunable (testable determinism: same tick
  sequence → same decisions).
- **EDGE CASES & PITFALLS:** Reset semantics must fire on engine
  pause/resume and regime switches (else stale direction memory biases
  post-resume decisions); the events list must be bounded (unbounded growth
  on a 24/7 stream would be a memory leak by design flaw).