# src/nexus_scalp/signals/rule_catalog.py

- **PURPOSE:** The declarative catalog of every configurable rule — name →
  `ParameterSpec` (default value, type, allowed range, timeframe field).
  The DB-config boundary: only rules present in this catalog can be enabled/
  parameterized via `trading_rules_config`; the UI rule toggles render from
  it; validation rejects unknown params.
- **ARCHITECTURE LAYER:** Signals (config schema).
- **RESPONSIBILITY:** Single registry of rule identity + parameter contract;
  `_tf(name, default)` helper builds a timeframe-flavored spec.
- **DEPENDENCIES:** pure data structures (dicts of ParameterSpecs), typing.
- **CONNECTS TO:** rule_matrix/_rule_engine (validation + params),
  web `/api/rules` endpoints (UI surface), tests.
- **KEY CONCEPTS:** The catalog is the source of truth for "what tunable
  exists" — adding a tunable to an evaluator WITHOUT a catalog entry is a
  silent dead knob; removing a catalog entry breaks configs that set it
  (validation must decide: ignore vs reject).
- **EDGE CASES & PITFALLS:** ParameterSpec ranges guard the UI sliders AND
  server-side validation — keep them in sync with the evaluator's actual
  thresholds (a spec range wider than the evaluator's sanity clamp creates
  confusing behavior).