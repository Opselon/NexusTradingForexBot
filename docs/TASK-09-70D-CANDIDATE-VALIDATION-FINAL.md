# TASK-09 — 70D CANDIDATE VALIDATION / SHADOW / GOVERNANCE READINESS — FINAL REPORT

> Agent: Hermes-70DValidation (AGENT-09) · TASK-09-70D-CANDIDATE-VALIDATION · 2026-08-19
> Role: 70D Validation, Candidate Lifecycle & Governance Readiness Engineer
> This task performs NO promotion. Promotion remains human-authorized only (INV-015).

## 1. GIT STATE (verified, not assumed)

| Item | Value |
| :--- | :--- |
| Branch | main |
| Local HEAD | `e7586f9ea4c86b8438696b2869ed0b36b20599c6` |
| origin/main | `e7586f9ea4c86b8438696b2869ed0b36b20599c6` |
| ls-remote refs/heads/main | `e7586f9ea4c86b8438696b2869ed0b36b20599c6` |
| ahead / behind | 0 / 0 (in sync at verification time) |
| Working tree | DIRTY — 112 entries (parallel swarm WIP: model_lifecycle AI-Hub diagnostics, liquidity_runtime, schema_v2, tests, scratch probes). NOT touched by this task. |

Parallel commits observed during this task (chronological): 3f3f3d9 (Forensic-70D-UI STEP-01/02),
987c550 (Forensic-70D-UI STEP-07 debug console), e7586f9 (AGENT-05 BUG-106 fix).
The TASK-13 report's `67b77e5` and TASK-08's `11e3402` are both OLD heads — superseded.

## 2. BUG-106 — FIRST TECHNICAL GATE

- Status: **FIXED** (committed e7586f9 by AGENT-05, verified independently by TASK-09).
- Root cause (verified in code at HEAD): `compute_70d_frame` passed `all_bars[:i+1]`
  (entire history) to `compute_liquidity_features` per row → O(n²). The live governor
  caps at 4000 bars; the dataset builder saw unbounded history (also broke train==live parity).
- Fix at HEAD: `LIQUIDITY_HISTORY_LIMIT = 4000` bounded causal tail
  (`all_bars[max(0, i+1-4000):i+1]`) in BOTH `compute_liquidity_frame` and `compute_70d_frame`.
  Matches the live engine's `_completed_bars` 4000-bar trim (train==live parity restored).
- Independent benchmark (TASK-09, this report — scratch tool outside repo):

| idx | unbounded | bounded (4000) | ratio |
| :--- | :--- | :--- | :--- |
| 4000 | 0.976s | 1.031s | 0.9x |
| 6000 | 2.420s | 1.224s | 2.0x |
| 10000 | 6.626s | 1.348s | 4.9x |
| 15000 | 14.898s | 1.561s | 9.5x |

  Asymptotics: unbounded ~quadratic, bounded ~linear after the 4000-bar crossover.
  Semantics preserved: same causal window, same engine, same features/clipping/normalization.
- Authoritative benchmark artifacts: `docs/BUG-106-PERFORMANCE-FIX.md` + scratch benches
  (committed e7586f9).

## 3. PARITY (dataset == replay == inference == runtime)

- **GREEN.** Full TASK-03 parity suites run by TASK-09 at HEAD:
  `test_70d_contract_parity_task3.py`, `test_70d_dataset_parity_task3.py`,
  `test_70d_parity_task3.py`, `test_70d_replay_parity_task3.py`,
  `test_70d_perf_task3.py`, `test_70d_runtime_hook_task3.py` → **68 passed, 3 skipped**
  (skips = explicit perf probes, by design).
- `artifacts/validation/70d_liquidity_parity.json` (existing, authoritative):
  real-broker probe on data/raw/XAUUSD_M5.parquet, 1000 bars, 25 timestamps checked,
  **0 mismatches, exact=True**; 6-scenario full-matrix; schema hash 235b8fcc…
  tolerance 1e-12 (deep-history rounding-only, documented).
- Golden fixtures: tests/golden/70d_liquidity_parity/parity_golden.json.

## 4. 70D CONTRACT VERIFICATION (from executable source, not docs)

- schema_id: `scalp_v3` (canonical 70D), registered in features/schema.py
  (Base 0..49 | News 50..59 | Liquidity 60..69).
- feature_schema_hash: `235b8fccc96b7e0e` (stamped at build, verified by parity).
- dimension 70, feature count 70, ordering Base→News→Liquidity, names canonical
  (bsl_distance_atr … post_sweep_displacement at 60..69).
- algorithm version: `liquidity_engine:70d-v1.0.0`.

## 5. DATASET (real)

- dataset_id: `ds_d3f35b12d63148da` · hash `aad73c8fde905223e1405a79ba9eb592397e93da4d09fc4e1e4e314f016465b0`
- source: data/raw/XAUUSD_M5.parquet (real broker, last 1200 bars)
- symbol XAUUSD · M5 · 1146 rows (train 802 / val 171 / test 173)
- schema scalp_v3 · feature_schema_hash 235b8fcc… · dimension 70
- label_schema triple_barrier_3class_v1 · purge 3 / embargo 3 · split hash 6acd7b73…
- DATA QUALITY (from 70d_liquidity_parity.json real_dataset block): all_finite True,
  all_in_range True, duplicate_timestamps 0, schema_hash_ok True.
- Manifest temporal_range shows 1970-01-01 — a KNOWN writer formatting artifact
  (naive-datetime serialization); real probe timestamps verified exact (0 mismatches).
  Flagged, not silently repaired.

## 6. A/B/C EXPERIMENT

- Status: **NOT_RUN** — no candidate model trained. The TASK-04 fair-benchmark protocol
  and TEST-70D-MODEL-01..25 (incl. fairness gates) exist and pass/skip truthfully, but
  the A/B/C run itself requires a real 70D candidate training cycle which remains
  unexecuted in the swarm. No protocol modification occurred.
- TEST-70D-MODEL-01..08 fairness gates verified present (same dataset/labels/split/purge).

## 7-9. WALK-FORWARD / OOS / ROBUSTNESS / CALIBRATION

- All **NOT_RUN** — no candidate. Infrastructure verified: WalkForwardTrainer
  (artifact_save_path default = wf_candidate, BUG-104 guard), verify_70d_artifact,
  governance verify_candidate 14 gates. No random temporal splits (purge/embargo enforced
  by dataset factory). No OOS observed → no OOS invalidation risk.

## 10. CANDIDATE

- **NO_CANDIDATE.** Registry (experience_model_registry) holds ONLY the 50D Champion
  (3 rows, all `primary_scalp_scalp_v1_50d`, CHAMPION). No 70D candidate artifact,
  manifest, or lifecycle entry exists anywhere. Candidate path separation: candidate
  artifacts land under artifacts/model_generation/models/ (wf_candidate etc.); Champion
  lives at artifacts/models/scalp/XAUUSD/v1.0.0/ — physically isolated, verified.

## 11. CHAMPION (BUG-104 incident state — unchanged by TASK-09)

- Path: artifacts/models/scalp/XAUUSD/v1.0.0/model.pt
- SHA256 (independently recomputed this task): `9105cef7d93e23b8a7d529ef797efba9283f36bc8190d2729909c1fc95634d2f`
- model.meta.json: RESTORED_CANDIDATE (bench_a_v1-derived 50D scalp_v1) — the frozen
  original f0f70efb… is UNRECOVERABLE (documented docs/CHAMPION_ARTIFACT_INCIDENT_20260819.md).
- Status: internally consistent (registry row, meta, and file hash agree on 9105cef7);
  the file's current hash MATCHES the recorded restore hash — TASK-09 did NOT alter it.
  Operator decision still pending per INV-015 (restore external / retrain / accept).
- Champion write-protection: **VERIFIED** — TEST-70D-MODEL-32 (default save path != live
  path) and TEST-70D-MODEL-31 (champion write protection) pass; WalkForwardTrainer default
  is artifacts/model_generation/models/wf_candidate/model.pt.

## 12. SHADOW

- Infrastructure: shadow70 package present (runtime/load-validator/health/drift/store/worker),
  import-verified; tests: test_shadow70_safety + test_shadow70_runtime → **45 passed**
  (after a transient parallel-edit flake; rerun clean).
- Runtime state: **NO_VALIDATED_CANDIDATE** (truthful — no 70D candidate exists to attach).
- Evidence: shadow70_observations = 2 rows, both `smoke_24` fixtures (not real candidate
  evidence). Shadow sample floor: **NOT SATISFIED** (no candidate → no observations).
- Safety: shadow package imports NO adapter/order_manager/risk engine (INV-018);
  test_shadow70_safety suite green. No broker execution path exists in shadow code.
- Virtual performance: none computed (no candidate).

## 13. GOVERNANCE

- verify_candidate: 14 gates (artifact_exists → liquidity_contract_valid), read-only.
- promotion_preview / rollback_preview / emergency controls: present, import-verified.
- model_governance_state: 0 rows. model_promotion_audit: 0 rows. **No promotion executed.**
- Required gates for THIS candidate: SKIP/INSUFFICIENT on training/walk-forward/OOS/
  robustness/calibration/shadow — under the contract (PASS=PASS, FAIL=FAIL, SKIP=NOT PASS,
  INSUFFICIENT=NOT PASS) this means **NOT_ELIGIBLE** for promotion, by design.

## 14. REGRESSIONS

- BUG-108 (AUDIT-0007 release_metadata fresh-DB): test_database_migrations_phase18 →
  **38 passed** (fresh DB reaches current version 7 incl. AUDIT-0007). Repair effective.
- Governance suite: test_model_governance_phase16 → **59 passed**.

## 15. GOVERNANCE STATUS MATRIX (TASK-09 verdict)

| Gate | Status | Explanation |
| :--- | :--- | :--- |
| Technical artifact | PASS | BUG-106 fixed + benchmarked (9.5x); parity green |
| Dataset | PASS | ds_d3f35b real, finite, in-range, no dup timestamps |
| Training | NOT_RUN | no 70D candidate trained (NOT PASS) |
| Walk-forward | NOT_RUN | no candidate (NOT PASS) |
| OOS | NOT_RUN | no candidate (NOT PASS) |
| Robustness | NOT_RUN | no candidate (NOT PASS) |
| Calibration | NOT_RUN | no candidate (NOT PASS) |
| Shadow | NO_VALIDATED_CANDIDATE | 2 smoke rows only; floor not met |
| Drift | NO_VALIDATED_CANDIDATE | no candidate to monitor |
| News contract | PASS | news_10d_from_context + neutral-zero when disabled; parity-verified |
| Liquidity contract | PASS | liquidity_engine 70d-v1.0.0; parity exact on real data |
| Champion protection | PASS | BUG-104 regression green; paths isolated |
| Migration (BUG-108) | PASS | 38 passed incl. fresh-DB AUDIT-0007 |

## 16. PROMOTION VERDICT

```
NO_CANDIDATE  (no 70D candidate artifact exists)
```
For the governance readiness of a candidate that does not exist:
```
INSUFFICIENT_EVIDENCE  (nothing trained/validated/shadowed)
```
**NEVER PROMOTED — and nothing CAN be promoted until a real candidate carries
evidence through the verify gates. Promotion remains human-authorized (INV-015).**

## 17. REQUIRED ARTIFACTS

- artifacts/validation/70d_governance_readiness.json (this task, committed)
- artifacts/validation/70d_candidate_manifest.json → N/A (no candidate — documented in
  readiness JSON; not fabricating a manifest)
- Existing authoritative artifacts reused (no duplicates created):
  artifacts/validation/70d_liquidity_parity.json, docs/BUG-106-PERFORMANCE-FIX.md,
  docs/CHAMPION_ARTIFACT_INCIDENT_20260819.md, docs/70D_LIQUIDITY_PARITY_REPORT.md,
  docs/MODEL_BENCHMARK_70D_LIQUIDITY.md.

## 18. QUALITY GATES

- Full gate NOT run (parallel WIP in tree; gate failures would be indistinguishable
  from swarm churn). Scoped verification run instead (all GREEN):
  parity 68/3, shadow70 45, governance 59, migrations 38, BUG-104 guard 1.
  The project gate will be exercised on the swarm's consolidated tree by its owners.
- Transient flake observed and reported honestly: shadow70 suite 4-fail on first run
  (parallel file edit), clean 45-pass on rerun — NOT hidden, NOT a real regression.

## 19. COMMIT LOG (TASK-09)

See git log: AGENT-09 commits carrying this report + readiness JSON + handoff
(docs only — no production code touched; swarm WIP preserved).
