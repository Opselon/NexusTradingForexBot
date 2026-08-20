# src/nexus_scalp/application/__init__.py

- **PURPOSE:** Package surface for the application layer (LiveEngine).
- **ARCHITECTURE LAYER:** Application (composition root).
- **RESPONSIBILITY:** Export the orchestrator types (`LiveEngine`,
  `ModelBundle`, `ScalerBundle`) so entrypoints (CLI) import from one
  place.
- **DEPENDENCIES:** `application.live_engine`.
- **CONNECTS TO:** CLI main, docker entrypoint, tests.
- **KEY CONCEPTS:** Importing the application package pulls the ENTIRE
  subsystem graph (features/signals/risk/execution/news/...). Keep it out
  of leaf-module imports to avoid circular-import land; the package is
  loaded by the process root.
- **EDGE CASES & PITFALLS:** heavyweight import — startup cost paid once;
  tests import it via fixtures to build the engine graph.