# 70D Post-Migration Bug Matrix (TASK-70D-SYSTEM-FLOW-FORENSICS)

> Agent: Hermes-Forensic-70D · 2026-08-19
> Only PROVEN bugs are listed. Scientific results (low accuracy, weak
> liquidity feature, PF < 1) are NOT bugs unless an engineering failure is
> proven (rule §61).

| ID | Severity | Component | Symptom | Root Cause | Evidence | Fix | Regression Test | Commit | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| BUG-105 | HIGH | live_engine 70D shadow hook | `shadow70_observations` empty in production; hook "RUNNING but doing nothing" | 1) Hook nested inside 50D-shadow `except` block (dead on happy path); 2) `build_70d_vector` imported under `if news_ctx is not None:` → UnboundLocalError when news disabled (default); 3) `_record_shadow_decision` early-returns without a 50D shadow; 4) `feature_schema_hash=""` silently skipped schema verification | `scratch/repro_shadow70_hook_dead_code.py` — happy path 0 obs; forced 50D failure → UnboundLocalError; after fix happy 1 / forced-fail 2; live DB had 1 fixture row only | New `LiveEngine._record_shadow70_observation()` called every tick (independent of 50D gate); imports hoisted; canonical `feature_schema_hash()` passed | TEST-SHADOW-36..39 (tests/unit/test_shadow70_runtime.py) | 14fff5a (absorbed) + 066a7ba (docs) | FIXED 🟢 |
| BUG-106 | MED | agents/bugs.md ledger | (documentation entry for BUG-105; see ledger) | — | — | — | — | ledger | FIXED 🟢 |
| — | MED (latent) | web/server.py attach_shadow70 (~4764) | validation_result forcing could pass a non-validated lifecycle status as VALIDATED_CANDIDATE | `validation_result = lifecycle_status if "VALIDATED" in str(...) else "VALIDATED_CANDIDATE"` — ANY status without "VALIDATED" (incl. REJECTED/ARCHIVED/empty) becomes VALIDATED_CANDIDATE | code inspection | NOT exploitable today: rows pre-filtered to status=CHALLENGER (validated by definition); load gate hash/dim checks still run. Harden by mapping CHALLENGER→VALIDATED_CANDIDATE explicitly and rejecting others | (none yet) | — | OPEN (hardening, LOW priority) |
| — | MED (latent) | governance/evidence.py:284 | drift-stat width capped at 50 | `width = min(len(feature_window[0]), 50)` — a 70D vector's tail (50..69) silently excluded from FEATURE drift stats | code inspection | NOT exploitable today: governance evidence consumes the 50D champion vector; 70D shadow has its own full-width drift monitor. Parametrize width | (none yet) | — | OPEN (hardening, LOW priority) |
| — | LOW (cosmetic) | shadow/shadow70/models.py:6 | docstring says "schema_id=scalp_v4" while SHADOW70_SCHEMA_ID = scalp_v3 | stale docstring after TASK-03 canonicalization | code inspection | update docstring | (none) | — | OPEN (cosmetic) |

## Classification of the full dimension audit (263 source hits)

- VALID_LEGACY (50D live contract): scalp_features, models/scalp_net default,
  experience CANONICAL_FEATURE_DIMENSION=50, audit_repository DEFAULT 50.
- ACTIVE_60D: governance/alignment (50+reserved+news math), schema_v2 60D
  builders, schema_augment validate_60d.
- ACTIVE_70D: schema_contract (canonical), features70, liquidity_runtime,
  inference_validator, shadow70 (runtime/models/store/liq_provider).
- DIMENSION-PARAMETERIZED (correct): ScalpNet ctor, architectures.input_dim,
  FEATURE_SCHEMAS registry.
- Trading constants (NOT dimension): 0.50 lots, 50-bar windows, 60-second
  intervals, 0.70 confidence thresholds — untouched.