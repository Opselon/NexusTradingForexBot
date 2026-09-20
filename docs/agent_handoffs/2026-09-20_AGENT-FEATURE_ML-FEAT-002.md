# AGENT-FEATURE — ML-FEAT-002 Handoff Report

**Task:** ML-FEAT-002 — Feature Importance, Collinearity Clustering & Redundancy Pruning
**Stream:** STREAM B — FEATURE ENGINEERING
**Role:** AGENT-FEATURE
**Date:** 2026-09-20
**Status:** DONE — PR OPENED
**Branch:** `agent/feature/ml-feat-002` (off `origin/main` @ `463f5e1f`)

---

## 1. What was delivered

| Artifact | Path | Purpose |
|---|---|---|
| Analysis layer | `src/nexus_scalp/features/importance.py` | Pure-NumPy Spearman correlation, mutual information, out-of-sample permutation feature importance, single-linkage collinearity clustering |
| Evaluation CLI | `scripts/analysis/evaluate_feature_importance.py` | Builds a frame through the REAL feature engine + labeler, runs the battery, emits 3 artifacts |
| Audit report | `docs/research/FEATURE_IMPORTANCE_AUDIT.md` | The `EVIDENCE_REQUIRED` deliverable: top-10 alpha drivers, collinear pairs, clusters, prune table |
| Dense matrices | `artifacts/research/correlation_matrix.npz` | Spearman / MI / PFI arrays + feature names (the `artifacts/research` correlation artifact) |
| JSON ranking | `artifacts/research/feature_importance.json` | Machine-readable full 50-feature ranking |
| Test battery | `tests/unit/test_feature_importance.py` | 61 tests, registered in `tests/critical_suite.txt` |

**Task-file paths verified live before adoption** (the ML-PLAT-001 lesson): `FEATURE_NAMES` is at `scalp_features.py:164` (50 entries, exactly matching the `scalp_v1` registry), `ACTIVE_SCHEMA_ID` at `schema.py:12`. The task's `OWNERSHIP_SCOPE` was accurate.

---

## 2. Design decisions and why

### Pure NumPy, no scikit-learn

The slim Linux verification venv (`.venv-linux`) carries `numpy 2.4.6` + `polars 1.44.2` but **no scipy, no scikit-learn, no pyarrow**. An audit tool that cannot run in the repo's own verification environment is unverifiable. Spearman rank correlation (with average-tie handling), mutual information (equal-width binned joint-count estimator — the same estimator `mutual_info_classif` uses), and the PFI surrogate model are therefore implemented locally.

### PFI needs a model — the surrogate is honest about being a surrogate

Permutation feature importance measures the loss increase when a column is shuffled, which requires a *fitted* model. No trained ScalpNet checkpoint ships in git (weights are gitignored by design), so the default fits a **deterministic closed-form one-vs-rest ridge** on the training split only. This is documented in the report as the scoring model, not presented as ScalpNet's opinion. `analyze_features` accepts a caller-supplied `model` + `model_predict` for the real-checkpoint case.

### Why the split is chronological, not random

The samples are a time series. A random split leaks the autocorrelated neighbours on both sides of each validation row into the surrogate's fit, and the measured loss drop would no longer be out-of-sample — which is the exact word in the task's OBJECTIVE. `_train_val_split` takes the tail as validation, and the standardisation statistics are computed on train only (a distribution leak would defeat the same purpose).

### NaN for constant columns, never 0.0

Permuting a constant column is a mathematical no-op. Reporting 0.0 would be indistinguishable from a genuine "the model gets nothing from this column" reading in the audit table. Constant columns are named explicitly in the report and sorted last in the ranking.

### NON_GOAL honored — read-only on the schema

`scalp_v1` / 50D is the ACTIVE live contract. Nothing in this change deletes, renames or reorders a feature. `scalp_features.py` and `schema.py` are **untouched** — the diff is additive only. The output is the evidence a *future* schema version cites, which is the framing the task asks for.

---

## 3. Verification evidence (all run live, none from commit messages)

| Gate | Command | Result |
|---|---|---|
| New tests | `pytest tests/unit/test_feature_importance.py` | **61 passed** in 6.45s (Python 3.11.16, pytest 9.1.1, slim venv) |
| Lint | `ruff check .` (repo-wide) | **All checks passed!** |
| Format | `ruff format --check .` (repo-wide) | **2204 files already formatted** |
| Types | `mypy src/nexus_scalp/features/importance.py` | **Success: no issues found in 1 source file** |
| Manifest | `scripts/ci/verify_critical_suite_manifest.py` | **CRITICAL_SUITE_MANIFEST_OK: 211 paths all exist** |
| Drift | `scripts/ci/check_dependency_drift.py` | **OK — 98 pins, requirements.txt consistent** (no pyproject change) |
| End-to-end CLI | `evaluate_feature_importance.py --bar-count 6000 --seed 42 --repeats 5` | 5,945 labelled samples, 3 artifacts written, 74.3s |
| Determinism | two builds, same seed → identical matrix, labels and rankings | pass |

Test classes: `TestCorrelationMatrix` (11), `TestMutualInformation` (6), `TestCollinearity` (12), `TestPermutationImportance` (10), `TestAnalyzeFeatures` (9), `TestRealFeaturePipeline` (5), `TestCli` (5). The integration tests build features through the production `ScalpFeatureEngine` + `TripleBarrierLabeler`, never a hand-rolled matrix.

---

## 4. Bugs found and fixed during implementation

1. **Spearman tie-averaging wrote to the wrong array.** The first implementation averaged tied ranks back through a double-indirect index (`ranks[order][i:j+1]`), which is not a view of `ranks` — the assignment silently vanished, so tied runs kept their ordinal ranks and a **constant column reported |rho| = 1.0 against every other column instead of 0.0**. Caught by `test_constant_column_correlates_zero_not_nan`. Rewritten to operate on a sorted-ranks buffer with a single scatter back. This is exactly the failure shape that would have produced a wrong "everything is collinear" audit.
2. **structlog's UNCONFIGURED `PrintLogger` corrupted CLI stdout.** `structlog.is_configured()` is `False` in a fresh CLI process, so the fallback `PrintLogger` writes every INFO record to **stdout**, and the JSON summary became unparseable (`json.loads` hit the labeler's info line first). This is the documented Typer/Rich `--json` trap recurring in a plain-stdout tool. Fixed by calling `configure_logging(log_level="WARNING", log_to_file=False)` at the top of `main()` and writing the summary via `sys.stdout.write` — engine severity routing unchanged.
3. `int(round(...))` tripped RUF046 (value already integer) → `round` alone, whose return type widens correctly for the `np.arange` consumers.
4. mypy could not see through `dict[str, Any]` row values for NaN/negation → hoisted the float conversion out of the dict and used `math.isnan`.

---

## 5. Audit findings (substantive, from the real 50D contract)

5,945 labelled samples, Spearman threshold |rho| > 0.85, chronological 70/30 split:

**Top-5 out-of-sample PFI:** `norm_tk_diff` 0.00157, `dist_to_ema_50` 0.00155, `dist_to_swing_low_20` 0.00032, `dist_to_ema_21` 0.00025, `cross_asset_z_score` 0.00025.

**The headline result — a perfect collinear triple:**

| Cluster | Representative | Members | Max \|rho\| |
|---|---|---|---|
| 1 | `norm_tk_diff` | `norm_tk_diff`, `norm_dist_to_tenkan`, `norm_dist_to_kijun` | **1.0000** |
| 2 | `feat_ob_liquidity_swept` | `feat_ob_liquidity_swept`, `stop_hunt_depth` | 0.9640 |
| 3 | `dist_to_ema_50` | `dist_to_ema_50`, `dist_to_swing_low_20`, `dist_to_ema_21`, `cross_asset_z_score`, `dist_to_swing_high_20`, `kumo_sig` | 0.9633 |

`norm_dist_to_tenkan` and `norm_dist_to_kijun` are a **pure linear re-expression** of the Tenkan-Kijun difference (rho = +1.0000 / −1.0000) — the model receives the same information three times. 11 collinear pairs, 8 pruning candidates total.

**Honest limitation:** the frame is synthetic M1 through the *real* feature engine. Synthetic bars exercise every branch of the production feature computation but carry no real microstructure, so the *magnitudes* are indicative, not a live verdict. The tool's `--dataset <id>` path exists for the broker-history rerun, and the report states this in its interpretation section.

---

## 6. Hard-constraint compliance

- ✅ Worktree-isolated (`/tmp/wt-feature-ml-feat-002`), branch off `origin/main`, shared tree untouched
- ✅ No `.github/workflows/*` edit; no `RemoteMT5GatewayAdapter` / client contract touch
- ✅ `_process_tick_pipeline` untouched — the new module imports only `FEATURE_NAMES` (a constant tuple) from the feature package, so the audit cannot reach the tick path
- ✅ No frozen domain model mutated; no Telegram secrets; no cron interaction
- ✅ No destructive git operation; no stash; no force-push
- ✅ `agents/locks.yaml` had no conflicting lock on `src/nexus_scalp/features/`

---

## 7. Next-run candidate

18 of 30 tasks now DONE. Remaining unblocked candidates are exhausted from the non-human-decision pool:

- **ML-ARCH-002** (P2 | AGENT-ML-ARCH) — dep ML-ARCH-001 (**HUMAN DECISION**) + ML-DATA-001 (DONE): still blocked on operator sign-off for the 3-class head sunset.
- **ML-TRAIN-001** (P1 | AGENT-ML-TRAIN) — dep ML-ARCH-001 (**HUMAN DECISION**) + ML-DATA-002 (DONE): blocked on the same sign-off; would unblock ML-EXP-001 (P1) + ML-EXP-003 (P2).
- **ML-CI-001** (P2 | AGENT-QA) — `OWNERSHIP_SCOPE` is `.github/workflows/nightly-ml-benchmark.yml`, forbidden to the swarm by HARD CONSTRAINTS.
- **ML-ARCH-001 / ML-GOV-002 / ML-GOV-003 / ML-FEAT-003 / ML-EXP-002 / ML-OBS-002 / ML-CI-002** — all `HUMAN_DECISION_REQUIRED: YES` or transitively blocked behind one.

**Recommendation for the operator:** the swarm has now saturated the autonomously-adoptable backlog. Unblocking requires signing off on **ML-ARCH-001** (the P0 3-class head decision), which opens ML-TRAIN-001, ML-EXP-001, ML-EXP-003, ML-ARCH-002, ML-CI-002 and ML-GOV-003. Alternatively, direct the swarm toward a verification/adjudication cycle over the 6 open ML PRs (#314–#316, #321, #322, plus this one), which the backlog note flags as green and landing-ready.
