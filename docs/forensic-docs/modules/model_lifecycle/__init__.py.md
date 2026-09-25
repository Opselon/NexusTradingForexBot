# src/nexus_scalp/model_lifecycle/__init__.py

- **PURPOSE:** Package entry point for PHASE 10 — controlled model training and
  Champion/Challenger management (the "Model Lifecycle & Challenger Engine").
  Documents the package contract: VERIFIED EXPERIENCE → TRAINING DATASET →
  CANDIDATE MODEL → VALIDATION GATES → CHALLENGER (shadow-eligible).
- **ARCHITECTURE LAYER:** Research/ML — explicitly holds no adapter, no order
  manager, no risk engine; it cannot place, modify or close an order.
- **RESPONSIBILITY:** Re-export the immutable domain contracts so downstream
  callers import them from one place. Module map in the docstring names the 11
  modules (models, dataset, integrity, champion, trainer, gates, comparison,
  registry, store, orchestrator, worker).
- **DEPENDENCIES:** `nexus_scalp.model_lifecycle.models` only.
- **CONNECTS TO:** every other model_lifecycle module via the re-exported types;
  external consumers (LiveEngine periodic task, web/governance API) use these
  names.

- **KEY CONCEPTS:**
  - `__all__` re-exports exactly 8 names from `models.py`:
    `ChampionChallengerComparison`, `GateResult`, `ModelArtifactInfo`,
    `ModelStatus`, `TrainingDataset`, `TrainingDatasetRow`, `TrainingRun`,
    `TrainingRunStatus` — the complete immutable contract surface of the package.
  - The module docstring states the two hard safety invariants the rest of the
    package enforces: (a) production inference stays with the Champion and a
    validated Challenger NEVER replaces it automatically; (b) the package owns no
    execution capability.

- **EDGE CASES & PITFALLS:**
  - Nothing executable lives here — it is a pure facade; all behavior is in the
    submodules. Missing symbols surface as ImportError at import time (explicit
    failure, not a silent fallback).
