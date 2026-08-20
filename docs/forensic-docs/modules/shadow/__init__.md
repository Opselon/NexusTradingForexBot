# src/nexus_scalp/shadow/__init__.py

- PURPOSE: Shadow Trading & Champion Evaluation Engine package entry —
  PHASE 11 production-safe parallel model evaluation (spec 1/2). The
  Challenger is SHADOW-ONLY: it evaluates the SAME live market state as
  the production Champion but has ZERO order authority. Package facade
  re-exports the immutable domain models only.
- ARCHITECTURE LAYER: Features/domain (evaluation harness, no execution).
- RESPONSIBILITY: Public API of the shadow package (models only —
  runtime/comparison/engine/store/worker are imported by consumers
  directly).
- DEPENDENCIES: shadow.models (PromotionEvaluation, ShadowComparison,
  ShadowDecisionKind, ShadowDecisionRecord, ShadowEvidenceStatus,
  ShadowModelRef, ShadowRun, SharedInputRef).
- CONNECTS TO: LiveEngine wiring, governance.shadow_runtime (wraps
  ChallengerRuntime), web dashboard, forensics shadow checks (reads
  shadow_* tables).
- KEY CONCEPTS:
  - The __all__ list is the governed public contract; adding execution-
    capable exports here would break the package invariant ("holds no
    adapter, no order manager, no risk engine — cannot place, modify or
    close an order, and can never replace the Champion automatically").
  - Module layout documented for consumers: models (immutable shadow
    contracts), store (append-only persistence), challenger (shadow-only
    model runtime), comparison (multi-dimension comparer + promotion eval
    + vetoes), engine (bounded wiring), worker (isolated background
    shadow-aggregation worker).
- HOT PATH / PERFORMANCE: import-time only.
- EDGE CASES & PITFALLS: no logic; the module docstring is the contract
  an auditor can diff against (like governance/__init__.py).