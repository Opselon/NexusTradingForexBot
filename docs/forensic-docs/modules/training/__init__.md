# src/nexus_scalp/training/__init__.py

- **PURPOSE:** Package surface for the training subsystem (walk-forward
  trainer, datasets, loss, scaler bundle).
- **ARCHITECTURE LAYER:** Training/ML.
- **RESPONSIBILITY:** Re-export the trainer and its dataset/loss types so
  callers (CLI, LiveEngine, model_lifecycle) import from one place.
- **DEPENDENCIES:** the module in the package.
- **CONNECTS TO:** any consumer of training.
- **KEY CONCEPTS:** Stability of the export names is a contract (SHARED API
  CHANGED discipline); the heavy torch import cost is paid here — importers
  of `nexus_scalp.training` pull torch, so keep it out of hot-path imports.
- **EDGE CASES & PITFALLS:** Importing this package at engine startup adds
  torch load time; LiveEngine imports lazily/inside the to_thread call path
  to avoid paying it on the critical boot path.