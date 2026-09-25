# src/nexus_scalp/strategies/__init__.py

- PURPOSE: PHASE 15C package surface for Seedable Built-in Strategies —
  importing the package RUNS the import-time registration side effect
  (base + ichimoku), then re-exports the public API so
  `from nexus_scalp.strategies import builtin_candidates()` yields the
  seeded candidates for research/backtesting.
- ARCHITECTURE LAYER: package root (registration side effect at import;
  no logic of its own; no I/O; no order authority).
- RESPONSIBILITY: guarantee that `import nexus_scalp.strategies` registers
  both Ichimili variants into BUILTIN_STRATEGIES (base, ichimoku modules
  flagged noqa: F401 to suppress unused-import lint), then export the
  contracts and the concrete strategies.
- DEPENDENCIES: `strategies.base`, `strategies.ichimoku`.
- CONNECTS TO: seeder (builtin_candidates), research worker seeding, any
  consumer importing nexus_scalp.strategies; the factory subpackage is the
  separate `nexus_scalp.strategies.factory` surface (imported independently).

- KEY CONCEPTS:
  - Import-time registration (lines 14-18): the `from nexus_scalp.strategies
    import (base, ichimoku)` inside the package __init__ is what makes
    BUILTIN_STRATEGIES non-empty after ANY `import nexus_scalp.strategies`
    — including indirect imports (e.g. via seeder or the research worker).
  - Exports (lines 19-33): BUILTIN_STRATEGIES, BarLike, Strategy,
    StrategySignal, builtin_candidates, make_candidate, register_strategy,
    and the two strategies (STRATEGY_ID_FINAL/SPACED,
    IchimiliFinalStrategy, IchimiliSpacedStrategy).
  - Note the empty `from nexus_scalp.strategies import base` line 15-18 is a
    self-import pattern: the submodule imports are what carry the side
    effect; the docstring documents this explicitly.
- HOT PATH / PERFORMANCE: import-time only.
- EDGE CASES & PITFALLS:
  - The package does NOT import or re-export the factory subpackage — a
    consumer wanting `StrategyFactory` must import
    `nexus_scalp.strategies.factory` explicitly (separate surface).
  - BUILTIN_STRATEGIES order is dict insertion order of register_strategy
    calls (ichimoku registers FINAL then SPACED) — builtin_candidates()
    order is stable but implied, not guaranteed by an explicit sort.
  - A strategy module that fails at import (e.g. schema_contract missing)
    will break ANY import of nexus_scalp.strategies — the registration side
    effect is not isolated.