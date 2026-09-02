# src/nexus_scalp/intelligence/__init__.py

- PURPOSE: Package facade for the Trade Intelligence Brain (PHASE 09 adaptive
  strategy evolution + position lifecycle intelligence) — declares the module
  map and re-exports the public API surface so consumers (LiveEngine worker
  wiring, web API) import one stable namespace.
- ARCHITECTURE LAYER: Application-boundary facade over the derived
  intelligence layer (lifecycle/autopsy/behavior/evolution/gate/worker/store).
- RESPONSIBILITY: (1) the normative safety statement — this package only
  analyzes, scores, recommends and rejects BEFORE execution; it holds no
  adapter, no order manager and no risk engine; it can never place, modify or
  close an order (docstring lines 16-18); (2) the `__all__` contract.
- DEPENDENCIES: re-exports from `intelligence.autopsy` (TradeAutopsyEngine),
  `intelligence.behavior` (BehaviorDetectionEngine),
  `intelligence.evolution` (StrategyEvolutionEngine),
  `intelligence.gate` (PreTradeIntelligenceGate, SuitabilityTier),
  `intelligence.lifecycle` (PositionLifecycleTracker),
  `intelligence.models` (all frozen derived contracts),
  `intelligence.worker` (IntelligenceWorker, format_intelligence_worker_status).
- CONNECTS TO: LiveEngine (wires PositionLifecycleTracker + IntelligenceWorker),
  web diagnostics (format_intelligence_worker_status, store reads), and the
  experience package (intelligence derives FROM the Phase 08 ledger — it never
  replaces it).
- KEY CONCEPTS:
  - The module map (docstring lines 6-14) is the layered design statement:
    models (immutable contracts) → lifecycle (position timeline) → autopsy
    (why did this trade win/lose) → behavior (measurable patterns) →
    evolution (controlled candidate discovery) → gate (WARN/suitability
    pre-trade) → worker (isolated background refresh) → store (bounded read
    facade).
  - `store.py` is intentionally NOT re-exported — it is a read-facade for
    internals, consumed via imports inside the package (e.g.
    `from nexus_scalp.intelligence.store import load_autopsy` in worker.py).
  - `__all__` (lines 45-69) is the public contract; note AnomalyEvent,
    BehaviorAnalysis/Status, BehaviorSeverity, etc. are exported models —
    adding a symbol here is a public-API event.
- HOT PATH / PERFORMANCE: None — pure import surface, no runtime work. The
  import pulls in numpy transitively via experience.evaluator (already loaded
  by any live run).
- EDGE CASES & PITFALLS:
  - The package imports worker.py at load time, which imports autopsy/
    behavior/evolution/lifecycle — so importing the facade initializes all
    engine classes; cheap, and required for wiring anyway.
  - Derived-layer caveat: interpretation tables (autopsies, behavior analysis,
    evolution candidates) are rebuildable and NEVER authoritative — the facade
    docs (lines 10-12) make the experience ledger the source of truth.