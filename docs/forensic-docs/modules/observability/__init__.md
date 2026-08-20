# src/nexus_scalp/observability/__init__.py

- **PURPOSE:** Observability package surface (telegram toolchain +
  logging).
- **ARCHITECTURE LAYER:** Observability.
- **RESPONSIBILITY:** stable exports (get_logger, setup_logging, notifier
  types).
- **DEPENDENCIES:** sibling modules.
- **CONNECTS TO:** whole system.
- **KEY CONCEPTS:** keep imports light (httpx/structlog only on demand).
- **EDGE CASES & PITFALLS:** none significant.