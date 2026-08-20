# src/nexus_scalp/__init__.py

- **PURPOSE:** The nexus_scalp package root — version + author identity
  and the package docstring describing the system.
- **ARCHITECTURE LAYER:** Package root.
- **RESPONSIBILITY:** minimal __init__ (no heavyweight imports — importing
  nexus_scalp must not pull torch/polars).
- **DEPENDENCIES:** none.
- **CONNECTS TO:** everything (implicitly).
- **KEY CONCEPTS:** `__version__ = "0.1.0"` — the source-tree version (the
  packaged release reads build-info.json for its real version, BUG-093
  discipline).
- **EDGE CASES & PITFALLS:** keep this file dependency-free so any
  submodule import stays cheap.