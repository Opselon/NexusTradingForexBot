# src/nexus_scalp/shadow/shadow70/__init__.py

- PURPOSE: 70D Liquidity Shadow Runtime package entry (TASK-05-70D-SHADOW)
  — observability-only runtime evaluating a validated 70D candidate
  against the live Champion WITHOUT any execution authority. Facade
  re-exporting the full public API (health, models, runtime, store,
  worker).
- ARCHITECTURE LAYER: Domain (observation harness; INV-018: no adapter,
  no order manager, no risk engine, no execution/policy object).
- RESPONSIBILITY: Package API surface:
  SHADOW70_SCHEMA_ID / DRIFT_SEVERITY_* / MAX_INMEMORY_OBSERVATIONS /
  SHADOW70_LATENCY_BUDGET_MS, DisagreementClass + classify_disagreement,
  Shadow70CandidateContract/FeatureProvenance/Observation/VectorReport/
  RuntimeState/LoadStatus/VectorReport, Shadow70LoadResult/LoadValidator/
  Runtime, Shadow70BackpressurePolicy/Persistence/Store, Shadow70QueueItem/
  Worker, format_shadow70_status.
- DEPENDENCIES: sibling modules only.
- CONNECTS TO: LiveEngine shadow70 wiring, forensics liquidity checks,
  web dashboard (format_shadow70_status), tests.
- KEY CONCEPTS:
  - The docstring documents the five guarantees: SHADOW_70D v1 contract,
    load validation (manifest/hash/schema/dimension/scaler), 70D vector =
    50 Base + 10 News + 10 Liquidity (POST_70D contract),
    idempotent deterministic observations, 8-class disagreement taxonomy,
    feature health + drift (NORMAL/WATCH/WARNING/CRITICAL), bounded queue
    + async persistence (no sync DB on tick path).
  - __all__ is the governed public contract; changes here are visible
    diff surface for the INV-018 safety invariant.
- HOT PATH / PERFORMANCE: import-time only.
- EDGE CASES & PITFALLS: no logic; consistency of the exported names
  with the shadow70 modules is the only audit point.