# src/nexus_scalp/experience/__init__.py

- PURPOSE: Package facade for the Experience Intelligence Subsystem (PHASE 08
  experience-driven strategy intelligence) — declares the module map and
  re-exports the public API surface so consumers (LiveEngine, web API,
  intelligence/*) import one stable namespace.
- ARCHITECTURE LAYER: Application-boundary facade over the Domain (models.py)
  and Application (ledger/evaluator/retriever/intelligence) classes of the
  experience package.
- RESPONSIBILITY: (1) normative statement of the subsystem's safety contract —
  it may DOWN-RANK or REJECT proposals but never executes orders and never
  bypasses RiskEngine or OrderManager (docstring lines 18-19); (2) the `__all__`
  contract used by `from nexus_scalp.experience import *`.
- DEPENDENCIES: re-exports from `experience.evaluator` (StrategyEvaluator),
  `experience.intelligence` (ExperienceIntelligenceEngine), `experience.ledger`
  (ExperienceLedger), `experience.models` (all frozen domain contracts),
  `experience.provenance` (ModelRegistry), `experience.quality`
  (OutcomeAnalyzer, compute_behavior_metrics), `experience.retriever`
  (ExperienceRetriever).
- CONNECTS TO: consumers of the experience subsystem — LiveEngine's pre-trade
  pipeline and outcome recorder, the intelligence/* package (autopsy reads
  `ExperienceRecord`/`OutcomeDecomposition`), web status/forensic endpoints.
  It is the single import chokepoint for the package.
- KEY CONCEPTS:
  - The module docstring (lines 6-16) is the package map and the normative
    statement of invariants: immutable memory contracts (models.py),
    deterministic outcome decomposition (quality.py), append-only persistence
    with dedup (ledger.py), statistical scoring/confidence/lifecycle with
    self-healing rebuild (evaluator.py), bounded context fingerprinting and
    top-K retrieval (retriever.py), model registry that is metadata-only —
    never weights (provenance.py), and the pre-trade boundary + post-trade
    recorder (intelligence.py).
  - Note the omitted modules: `outcome_recovery.py` and `outcome_repair.py`
    are NOT re-exported here (they are internal helpers to intelligence.py and
    the repair job respectively), so the facade deliberately stays stable.
  - `__all__` (lines 51-79) is the public contract; adding a symbol here is a
    public-API event. Constants exported: CANONICAL_FEATURE_SCHEMA_ID /
    CANONICAL_FEATURE_DIMENSION (50D live contract) and MAX_STRATEGY_CONFIDENCE
    (0.95 hard cap).
- HOT PATH / PERFORMANCE: None — pure import surface, no runtime work.
- EDGE CASES & PITFALLS:
  - Importing this package pulls in evaluator.py (numpy import) and
    intelligence.py, so cold-start import cost includes numpy; acceptable
    because these modules are required by any live run anyway.
  - The stale docstring line 12 references `replay validation` which lives in
    evaluator.py; line 16 lists `intelligence.py` last while it is the primary
    entry point — cosmetic ordering only.