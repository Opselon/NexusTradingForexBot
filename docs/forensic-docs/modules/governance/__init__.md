# src/nexus_scalp/governance/__init__.py

- PURPOSE: Model Governance package entry — re-exports the canonical
  governance API surface (engine, load gate, lock, models, reporting,
  shadow runtime, store, transaction, verify) so consumers import from one
  place. Re-export only, no execution imports by contract.
- ARCHITECTURE LAYER: Domain / control plane (model lifecycle & audit).
- RESPONSIBILITY: Public package facade; the `__all__` list is the
  governed public contract of the governance package.
- DEPENDENCIES: sibling modules only (engine, load_gate, lock, models,
  reporting, shadow_runtime, store, transaction, verify).
- CONNECTS TO: every consumer of governance (LiveEngine wiring, web API,
  promotion UX, Telegram reporting).
- KEY CONCEPTS:
  - Central export point for: ModelGovernanceEngine, ModelLoadGate,
    evaluate_load_gate, read_manifest_file, read_registry_lifecycle,
    PromotionLock(+Error), all domain models (GovernanceEvent,
    LoadGateResult, LoadGateStep, PromotionState/Transition, Registry
    models, ShadowParity, CalibrationBucket, DriftAlert),
    build_governance_report, model_shadow_update_text,
    GovernanceShadowRuntime, GovernanceStore,
    execute_promotion_transaction, verify_candidate.
  - The `__all__` list doubles as the API contract (also used by
    star-importing tools); keeping it explicit prevents accidental public
    surface growth.
- HOT PATH / PERFORMANCE: none — import-time only.
- EDGE CASES & PITFALLS: the module docstring contract ("no execution
  imports") means any new import of adapters/order managers here would be
  a governance-integrity violation (INV-002/003/004) — visible by a
  simple diff of this file.