# src/nexus_scalp/configuration/__init__.py

- **PURPOSE:** Package exports for configuration (AppConfig + section
  models + runtime store).
- **ARCHITECTURE LAYER:** Configuration.
- **RESPONSIBILITY:** Stable import surface.
- **DEPENDENCIES:** sibling modules.
- **CONNECTS TO:** whole system (every consumer of config).
- **KEY CONCEPTS:** Importing configuration pulls pydantic_settings + yaml
  — keep it out of leaf modules; the exports are additive contract.
- **EDGE CASES & PITFALLS:** None beyond additive-export discipline.