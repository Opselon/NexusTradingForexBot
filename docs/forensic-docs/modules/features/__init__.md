# src/nexus_scalp/features/__init__.py

- **PURPOSE:** Package exports for the features subsystem — the 50D/60D/70D/
  92D family (scalp_features, schema, schema_contract, schema_augment,
  liquidity_engine, liquidity_runtime, features70, runtime70, temporal) plus
  regime classification and latency instrumentation.
- **ARCHITECTURE LAYER:** Features.
- **RESPONSIBILITY:** Stable import surface so LiveEngine, datasets,
  shadow70 and tests import from `nexus_scalp.features` rather than deep
  module paths.
- **DEPENDENCIES:** the sibling modules in the package.
- **CONNECTS TO:** everything importing features.
- **KEY CONCEPTS:** Re-exports the canonical names (FEATURE_NAMES,
  ScalpFeatureEngine, FeatureVector, MarketRegimeClassifier,
  compute_liquidity_features, LiquidityGovernor, build_70d_vector,
  assemble_70d, canonical_feature_names, feature_schema_hash,
  validate_70d_vector, ...). The package is the single gate through which
  the engine's feature universe is addressed.
- **EDGE CASES & PITFALLS:** Keep the export list additive (contract §15);
  removing/renaming an exported name breaks every importer simultaneously.
  Note: importing this package triggers `schema_contract`'s import-time
  contract guard (news-context schema dependency) — a startup cost that is
  intentional (fail early on drift).