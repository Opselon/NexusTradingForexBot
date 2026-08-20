# src/nexus_scalp/forensics/references.py

- PURPOSE: Frozen reference distributions for feature health (TASK-11).
  The Liquidity/News drift, deadness and flood checks compare LIVE
  observed statistics against FROZEN training/reference distributions.
  CRITICAL DISCIPLINE (§8/§55): the monitor NEVER rewrites a feature; it
  classifies NORMAL/WATCH/WARNING/CRITICAL and reports. When no frozen
  reference exists for a family, checks MUST return UNKNOWN — never
  fabricate a reference. References are registered ONLY by an explicit,
  governed action (dataset freeze / model train), never automatically.
- ARCHITECTURE LAYER: Domain (reference data registry).
- RESPONSIBILITY: FeatureReferenceRegistry (append-only, replace-guarded),
  FeatureReferenceStats (frozen per-feature distribution),
  compute_reference_stats (from a deterministic training sample),
  freeze_liquidity_references_from_golden (provenance-guarded load from
  the golden baseline doc), FEATURE_REFERENCES process-wide registry,
  LIQUIDITY_70D_FEATURE_NAMES, GOLDEN_BASELINE_PATH, NOT_FROZEN sentinel.
- DEPENDENCIES: dataclasses, pathlib, json, math (lazy); random via
  shadow70 health — NOT here.
- CONNECTS TO: forensics checks (CHECK-FCS-03, CHECK-LIQ-01,
  CHECK-NWS-03 availability), ForensicHealthEngine._auto_freeze_references,
  freeze CLI/agent action.
- KEY CONCEPTS:
  - FeatureReferenceStats: per-feature frozen summary — mean/std/
    min/max/missing_rate/zero_rate/saturation_rate/mode_value/
    mode_fraction/n/source; source identifies the provenance (dataset id).
  - Registry semantics: key = (family, feature_index); register() with
    replace=False raises ValueError when a DIFFERENT source is already
    frozen (a silent re-freeze can never hide drift — re-freezing needs
    explicit replace=True); same-source re-register is a no-op returning
    the existing ref.
  - compute_reference_stats: non-finite values count as missing;
    missing_rate over total (default len(values)); zero_rate |v|<1e-12;
    saturation at the clip bounds ±3; raises when no finite values.
  - GOLDEN BASELINE freeze (TASK-12 §23): only the proven doc
    docs/LIQUIDITY_70D_GOLDEN_BASELINE.json may be used
    (provenance guard: schema must be "scalp_liquidity_v1" — anything
    else raises); reads per_feature stats for the 10 canonical names;
    percentage values (>1) auto-converted to fractions (/100);
    source = "{file}@{git_head_commit}"; missing any feature → abort.
  - NOT_FROZEN sentinel is what checks pass when the registry is absent.
- HOT PATH / PERFORMANCE: reads O(1); freeze is a governed one-time
  action.
- EDGE CASES & PITFALLS: registry is process-wide and in-memory — NOT
  persisted to disk (a restart loses the frozen references until the
  auto-freeze re-runs from the golden baseline); the auto-freeze in the
  engine only fires when len(registry)==0 at construction;
  missing_rate conversion assumes values > 1 are percentages (0..1
  values pass through) — a 1.5 missing rate would be misread as 150%→
  0.015 (edge only for malformed baselines); mode_fraction/mode_value
  are optional (None mode OK); source string includes a git commit —
  changes to the baseline file without a commit bump produce a "new"
  source and would raise on re-register without replace=True.