# src/nexus_scalp/research/lifecycle.py

- PURPOSE: PHASE 09B strategy research lifecycle STATE MACHINE (spec
  19/30): DISCOVERED → BACKTESTING → VALIDATING → OOS_TESTING →
  ROBUSTNESS_TESTING → VALIDATED → SHADOW → ACTIVE; failure paths REJECTED /
  DEGRADED / RETIRED. A strategy MUST NOT skip validation and CANNOT reach
  ACTIVE without all gates plus an explicit operator approval — promotion is
  never automatic.
- ARCHITECTURE LAYER: Research Domain (pure state machine; no I/O; no order
  authority).
- RESPONSIBILITY: single source of allowed transitions + the two policy
  functions (approve_for_live, require_validation_gate) that encode the
  safety contract.
- DEPENDENCIES: `research.models` (CandidateLifecycle).
- CONNECTS TO: registry.transition_lifecycle (persisted transitions), the
  pipeline (which drives stage gates), operator/CLI promotion paths.

- KEY CONCEPTS:
  - `_TRANSITIONS` adjacency map (lines 21-52): the complete legal graph.
    Every forward state may also drop straight to REJECTED; VALIDATED →
    SHADOW → ACTIVE requires explicit steps; ACTIVE exits only to DEGRADED /
    RETIRED; DEGRADED may recover to VALIDATED or fall to RETIRED; REJECTED /
    RETIRED are terminal sinks (empty sets).
  - `can_transition` (lines 59-60): pure adjacency lookup.
  - `transition` (lines 63-71): returns the target or raises LifecycleError
    ("Illegal lifecycle transition") — used by registry.transition_lifecycle
    which catches ValueError and logs.
  - `approve_for_live` (lines 74-85): the operator gate (spec 21) — ONLY
    SHADOW (or previously-validated VALIDATED) may become ACTIVE; anything
    else raises LifecycleError; no candidate can auto-promote.
  - `require_validation_gate` (lines 88-97): trade-eligibility check —
    only VALIDATED / SHADOW / ACTIVE pass; DISCOVERED/BACKTESTING/
    VALIDATING/OOS_TESTING/ROBUSTNESS_TESTING/REJECTED/DEGRADED/RETIRED
    raise.
- HOT PATH / PERFORMANCE: constant-time dict lookups; called on worker/
  operator paths, never per tick.
- EDGE CASES & PITFALLS:
  - The graph is not enforced automatically anywhere — it is a library
    (transitions must be invoked); registry.transition_lifecycle enforces
    it on the persistence path, but the pipeline sets lifecycles directly
    (CandidateLifecycle.VALIDATED etc. in _register) WITHOUT calling
    transition — i.e. the pipeline can jump DISCOVERED → VALIDATED
    legally only because of score verdict logic, not the state machine.
  - `approve_for_live(VALIDATED)` is permitted — a validated-but-never-shadowed
    strategy can go live directly; the docstring calls SHADOW the normal
    route but does not require it.
  - DEGRADED → VALIDATED re-promotion is allowed, but nothing here checks
    whether the validation evidence is CURRENT (re-validation of the new
    date is the caller's duty).
  - REJECTED and RETIRED entries carry empty transition sets — a RETIRED
    strategy can never return (by design).