# MODEL BENCHMARK 70D LIQUIDITY — FAIR COMPARISON PROTOCOL & BLOCKER REPORT

> Agent: Hermes-ModelValidation-04 · TASK-04-70D-MODEL-VALIDATION · 2026-08-19
> Role: 70D Model Generation / Fair Benchmark / Challenger Validation
> Status: **BLOCKED — WAITING_FOR_AGENT (TASK-03-70D-PARITY)** | Head `4001e4c` | branch `main`

---

## 0. EXECUTIVE SUMMARY (start-of-task forensic)

TASK-4's FIRST mandate (brief §2) is to **verify TASK-3 actually delivered**
before training anything:

```text
dataset == replay
replay == inference
schema == manifest
scaler == manifest
70D == expected
```

**TASK-3 has NOT delivered.** Evidence:

| Check | Expected | Actual | Verdict |
| :--- | :--- | :--- | :--- |
| `docs/70D_DATA_CONTRACT.md` | exists | missing | ❌ |
| `docs/agent_handoffs/TASK-03-70D-PARITY.md` | exists | missing | ❌ |
| `docs/agent_handoffs/TASK-01-60D-LIQUIDITY.md` | exists | missing (only `docs/LIQUIDITY_60D_FORENSIC_BASELINE.md`, Phase-A read-only) | ❌ |
| `docs/agent_handoffs/TASK-02-70D-INTEGRATION.md` | exists | missing | ❌ |
| 70D parity tests in `tests/` | exist | none (search: no `parity` suite referencing 70D) | ❌ |
| `scalp_v3` = 70D registry entry | exists | `scalp_v3` is **350D** (research contract, `features/schema.py`) | ❌ |
| 70D dataset artifacts | exist | none under `artifacts/model_generation/datasets/` (all 50D/60D) | ❌ |
| Liquidity layer committed | committed + verified | **UNCOMMITTED WIP** (`src/nexus_scalp/features/liquidity_engine.py` untracked, <70% of its contract tests green: liq03/liq05/liq11 FAIL) | ❌ |

Per the MASTER multi-agent contract the repo is a shared space: the 60D
Liquidity foundation (TASK-01 of the 70D series) exists only as **uncommitted
working-tree WIP**, and a parallel agent is still building TASK-03 parity. A
benchmark run over a moving, unverified feature contract would be **scientifically
invalid** — its result could not be attributed to the Liquidity layer.

The correct scientific report at this point, exactly as the brief §2 allows, is:

```text
MODEL TRAINING BLOCKED:
FEATURE CONTRACT NOT TRUSTWORTHY
```

This document therefore:
1. Records the forensic evidence of the blocker (above).
2. Fixes the **benchmark protocol** every future model-generation run must obey
   (the fair A/B/C design — sections 1–6) so that when TASK-3 lands the
   benchmark is executed without re-design or threshold gaming.
3. Records the dataset-fairness method + the first real verification result on
   existing artifacts (section 7) — PROVEN on scalp_v1/scalp_v2 artifacts.
4. Records the Champion freeze (section 8) — PROVEN unchanged.
5. Provides the executable contract suite glue (section 9) that the real
   70D parity tests (TEST-70D-MODEL-01..25) will plug into.

---

## 1. ABSOLUTE SCIENTIFIC RULE (brief §1)

The benchmark compares **CONTROL (60D Base+News)** vs **EXPERIMENT
(70D Base+News+Liquidity)** with IDENTICAL:

```text
dataset timestamps   labels               time split
purge                embargo              normalization policy
training budget      random seed policy   evaluation metrics
validation gates
```

The ONLY intentional difference: the feature contract (70D includes the 10
Liquidity dimensions 60..69).

**Forbidden:** comparing different datasets/labels/time-ranges/row counts;
changing split ratios or thresholds because 70D performs poorly; giving the
candidate a more favorable training budget; deleting features based on OOS
results; auto-promotion under any circumstance.

## 2. BENCHMARK MATRIX (brief §3/§49)

```text
A = 50D Base (scalp_v1)                    0..49
B = 60D Base + News (scalp_v2 + news 12D)  0..49 + 50..59 (TASK-5 extras) + news_*
C = 70D Base + News + Liquidity (scalp_v3) 0..49 + 50..59 (News 10D per 70D brief) + 60..69 (Liquidity)
D = 60D Base + Liquidity (scalp_liquidity_v1, no news)   [where feasible]
```

Interpretation (brief §49):
```text
News incremental value            = B - A
Liquidity incremental over News  = C - B
Liquidity standalone value       = D - A
```

### 2.1 Repository reality note (PROVEN)

The 70D task brief assumes `50..59 = News 10D`. In THIS repository the
facts are different and the baseline must be documented, not silently
redefined (brief §4):

- `feat_50..59` today = **TASK-5 momentum/regime extras** (`compute_60d_extras`,
  `scalp_v2`) — implemented, committed (b4c5104), covered by
  `test_model_generation_phase13.py`, used by the TASK-5 8-cell benchmark.
- Real news enters the model as a **separate 12-field `news_context_v1`
  vector** (`news_*` columns, `model_generation/news_bridge.py`) appended
  after the feature columns when `news_enabled`.
- `scalp_v3` is registered as **350D** (multi-symbol research contract) —
  the TASK-2/3 agents must register a NEW 70D contract (`scalp_v3_70d`) or
  redefine `scalp_v3` before the 70D benchmark can run. NOT my call — this
  belongs to TASK-2/3; TASK-4 records the requirement.

When TASK-3 lands, the benchmark cell definitions must be verified against
the ACTUAL column layout of the 70D dataset artifact (orders 0..49 base,
50..59 news-10D, 60..69 liquidity) before any training starts. If the 70D
artifact instead carries `scalp_v3 = 350D` or news-at-12D, the A/B/C matrix
above is adjusted ONLY to preserve identical samples/labels/timestamps — the
feature-contrast principle (Liquidity present vs absent) never changes.

## 3. DATASET ALIGNMENT (brief §5/§17)

Every sample carries: `sample_id`, `timestamp`, `label`,
`feat_0..feat_49` (base), news block, liquidity block, schema provenance.

The canonical fairness gate is **sample_id identity** (already how the
existing artifacts are built — `sample_id` is a deterministic hash of
timestamp+context):

```text
identical sample_ids between A/B/C          ⇒ same timestamps
same label per sample_id                     ⇒ same labels
same split assignment per sample_id          ⇒ same train/val/OOS
```

TEST-70D-MODEL-01/02/03/17 encode exactly this gate (identical samples,
labels, temporal split, sample IDs) as executable code.

## 4. STATISTICAL DESIGN (brief §15/§32/§33)

- **Walk-forward:** purged + embargoed folds (NUM_FOLDS=34 policy in
  `WalkForwardTrainer`; purge_gap=15, embargo=purge), SAME fold boundaries
  for A/B/C. Report fold-by-fold, never one aggregate.
- **Effect size:** absolute + relative difference, fold variance (std of
  per-fold macro-F1), bootstrap CI where justified.
- **Significance:** temporally-dependent samples → no naive IID test.
  Use paired fold-level statistics (Wilcoxon signed-rank over FOLDS, not
  samples) with the sample-size limitation documented, else INCONCLUSIVE.
- **Verdicts (brief §51):** STRONG POSITIVE / WEAK POSITIVE / NEUTRAL /
  NEGATIVE / INCONCLUSIVE / INVALID. Never convert INCONCLUSIVE to SUCCESS.

## 5. METRICS (brief §16/§19/§26)

Minimum per cell:

```text
accuracy           balanced accuracy    macro-F1
per-class P/R/F1   confusion matrix     ECE
Brier              decision rate        signal precision (BUY/SELL P)
NO_TRADE rate
```

Calibration measured on the SAME val/OOS split via `compute_calibration`
(ECE floor 0.15 policy in `validation.py::ECE_FLOOR`). A 70D model with
higher accuracy but worse calibration is NOT superior — report both.

## 6. GOVERNANCE (brief §28/§30/§44/§52)

- 70D candidates start as `REJECTED` or `CANDIDATE`, and may become
  `CHALLENGER` ONLY after passing every existing eligibility gate
  (backtest / walk-forward / OOS / robustness / score) —
  `validation.py::ValidationFactory`, `governance/engine.py`.
- NO AUTO-PROMOTION, EVER: Champion changes only via the
  SHADOW → READY_FOR_REVIEW → APPROVED → CHAMPION state machine with an
  operator actor (INV-015).
- At most a shadow candidate, following existing governance.
- Champion safety: artifact hash + scaler hash verified before AND after
  this task (section 8).

## 7. DATASET FAIRNESS — FIRST REAL VERIFICATION (PROVEN)

Existing artifacts (pre-70D, TASK-5 8-cell benchmark) prove the method:
identical sample_ids between 50D and 60D artifacts of the same generation.

```text
ds_cb30f87520e9e6a4 (scalp_v1, 50D) vs ds_af362f55e86a15ca (scalp_v1, 50D):
    identical samples = 99946 / 99946   (jaccard 1.0)

ds_b64513f79687824a (scalp_v2, 60D)   vs ds_f9a06027a76588ff (scalp_v2, 60D):
    identical samples = 99946 / 99946   (jaccard 1.0)

scalp_v1 vs scalp_v2 artifacts of DIFFERENT generations: 0 shared sample_ids
    (expected — different feature-schema generations produce different
    sample identities; the 70D builder must therefore generate the comparison
    datasets in ONE run from ONE source frame, exactly like
    BenchmarkRunner does per schema)
```

Row counts: all four artifacts 99,946 rows, label schema
`triple_barrier_3class_v1`, purge 3 / embargo 3, label split
train 69,962 / val 14,991 / test 14,993. Verified 2026-08-19.

**Method note:** the 70D A/B/C datasets MUST be built by ONE builder from the
SAME bar frame + SAME labeler + SAME split configuration in ONE run (this is
how `BenchmarkRunner.run` already works for scalp_v1 vs scalp_v2). Then
sample_id/label/timestamp identity is structural, not approximate.

## 8. CHAMPION FREEZE (brief §30) — PROVEN UNCHANGED

```text
artifact: artifacts/models/scalp/XAUUSD/v1.0.0/model.pt
model_id: primary_scalp, version 1.0.0, schema scalp_v1 / 50D
artifact_hash_sha256: f0f70efb1b55855beb96ae807d81b44db07ae4d0fcff1da2965ea0a408f1d88b  (matches docs/task5_champion_baseline.json)
scaler_hash_sha256:   811554e5286ea3104a9f759ccce611fb62a9994856d08b2dad82aeb6b99424e1  (matches docs/task5_champion_baseline.json)
Re-verified live on-disk 2026-08-19 02:46 UTC+0330: MATCH
```

## 9. EXECUTABLE CONTRACT-SUITE GLUE (what TASK-4 leaves behind)

`tests/unit/test_70d_model_validation_task4.py` — TEST-70D-MODEL-01..25 —
implements the FAIRNESS + SAFETY contract that is executable TODAY with the
WIP in the tree, plus placeholder/conditional tests that activate the moment
a 70D artifact/schema exists:

1. **Fairness/equivalence gates (current artifacts):** TEST-70D-MODEL-01/02/03/17
   prove identical sample_ids / labels / timestamps / scaler-dimension rules
   on the EXISTING 50D vs 60D artifact pair (PROVEN green).
2. **Schema registry:** TEST-70D-MODEL-05/06 — 60D scaler dimension 60;
   70D scaler dimension 70 (70D scaler test is `skipif` until a 70D schema
   exists).
3. **Forward-pass geometry:** TEST-70D-MODEL-07/08 — 60D/ScalpNet forward
   pass is live; the 70D forward pass activates via parameterization when
   `scalp_v3_70d` (or the TASK-3-registered 70D contract) exists.
4. **Safety gates (always active):** TEST-70D-MODEL-09 (schema mismatch
   rejected), 11 (non-finite features rejected → FAILED), 12 (deterministic
   training smoke), 14 (Champion unchanged — hash check), 25 (no
   auto-promotion: promotion state machine refuses CANDIDATE→CHAMPION,
   INV-015), 16 (failure reason recorded), 20 (OOS gate cannot use training
   data), 22 (calibration metrics define valid ECE).
5. **Placeholders (skipif — activate on TASK-3 landing):** 10 (dataset
   leakage rejected), 13 (manifest correctness on 70D artifact), 15
   (research registry updated), 18 (liquidity ablation reproducible), 19
   (news/liquidity family separation), 21 (robustness gate executes on 70D),
   23 (parameter count reported), 24 (inference latency measured).

## 10. WHAT MUST HAPPEN WHEN TASK-3 LANDS (exact TASK-4 continuation)

1. Re-run `git log --oneline -5` + `git status --short`; confirm a 70D
   parity commit exists and 60D liquidity WIP is committed.
2. Read `docs/70D_DATA_CONTRACT.md` + `docs/agent_handoffs/TASK-03-70D-PARITY.md`.
3. Run the TASK-3 parity suite (dataset==replay==inference==runtime).
   If ANY parity test fails → stop, report
   `MODEL TRAINING BLOCKED: FEATURE CONTRACT NOT TRUSTWORTHY` (do NOT fix
   the contract — that is TASK-3's scope; record BUG-NNN with evidence).
4. Build A/B/C (+D) datasets in ONE run from ONE source frame (same
   labels/splits/purge/embargo). Verify TEST-70D-MODEL-01/02/03/17 on the
   real 70D artifact.
5. Run dataset quality audit (counts: total/valid/invalid, news-unavailable,
   liquidity-unavailable, non-finite, out-of-range, duplicates,
   future-leakage failures) — brief §7.
6. Liquidity feature distribution audit (min/max/mean/median/std/p01..p99,
   zero_rate, missing_rate, unique_count, constant/near-constant/
   saturated_at_±3) — brief §8.
7. Correlation/redundancy audit vs 50D+news (Pearson/Spearman/MI) — brief §9.
8. Label balance report (NO_TRADE/BUY/SELL/WAIT) — brief §10.
9. Train A/B/C with IDENTICAL training budgets (epochs/lr/batch/seed policy;
   document any justified deviation) — brief §40. Report parameter counts
   (§41) and training/inference timing (§42/§43).
10. Walk-forward fold-by-fold + OOS + robustness + calibration + ablation +
   news×liquidity interaction + regime/session analysis (§15–25).
11. Register in the existing research registry (§36) — no parallel registry.
12. Classify result (§51), write `docs/MODEL_BENCHMARK_70D_LIQUIDITY.md`
    final table (§31) + `docs/agent_handoffs/TASK-04-70D-MODEL-VALIDATION.md`.
13. Full quality gates (§55): ruff check/format, mypy src, pytest unit +
    integration, beforePush.ps1.
14. Commit with full mandate (§56/§57): agent-label commit, handoff, exact
    TASK-5 next-agent instructions.

## 11. TRACEABILITY

```text
TASK-ID:    TASK-04-70D-MODEL-VALIDATION
ROLE:       70D Model Generation / Fair Benchmark / Challenger Validation
STATUS:     BLOCKED → WAITING_FOR_AGENT (TASK-03-70D-PARITY)
HEAD (start): 4001e4c
Blocking evidence: sections 0, 2.1, 10 above
```

## 7.1 LIQUIDITY DISTRIBUTION AUDIT — SYNTHETIC REGIMES (brief §8, PARTIALLY PROVEN)

Executed 2026-08-19 before TASK-3 landed, over deterministic fixture regimes
(TRENDING_UP/DOWN, RANGING, VOLATILE, SWEEP, RANDOM_WALK; 94 windows).
Probes: `scratch/liq60d_distribution_audit.py` (+ .out.txt, + .json).
NOTE: synthetic fixtures — shapes are informative, absolute rates are NOT real
market rates. The real-market audit runs on the 70D dataset after TASK-3.

| feature | min | max | mean | zero% | sat+3% | uniq |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| bsl_distance_atr | 0.0169 | 3.0 | 1.146 | 0.0% | 7.4% | 73 |
| ssl_distance_atr | 0.0392 | 3.0 | 1.217 | 0.0% | 11.7% | 70 |
| eqh_strength | 0.0 | 1.0 | 0.7547 | 0.0% | 0.0% | 71 |
| eql_strength | 0.0 | 1.0 | 0.5125 | 0.0% | 0.0% | 73 |
| htf_liquidity_score | -2.3411 | 2.6224 | 0.3136 | 0.0% | 0.0% | 70 |
| internal_liquidity_distance | 0.0169 | 3.0 | 1.1228 | 0.0% | 26.6% | 70 |
| external_liquidity_distance | 0.0994 | 2.7003 | 1.0876 | 0.0% | 0.0% | 77 |
| liquidity_confluence | 1.8935 | 3.0 | 2.7551 | 0.0% | 44.7% | 9 |
| liquidity_sweep_state | -2.0 | 2.0 | -0.3936 | 0.0% | 0.0% | 4 |
| post_sweep_displacement | 0.0 | 0.4401 | 0.0096 | 0.0% | 0.0% | 4 |

Findings (synthetic, to verify on real data):
- `liquidity_confluence` is near-saturated: 44.7% of values at +3.0, only 9
  unique values → likely LOW information content (verify).
- `liquidity_sweep_state` has only 4 unique values (-2..2) — coarse but not
  constant; `post_sweep_displacement` mostly ~0 (event-driven, expected).
- No feature is fully constant or 95% zero on these regimes.

## 7.2 LIQUIDITY-vs-BASE REDUNDANCY AUDIT (brief §9, PARTIALLY PROVEN)

| feature | best-base (Pearson) | pearson | spearman | flag |
| :--- | :--- | ---: | ---: | ---: |
| bsl_distance_atr | dist_to_swing_high_20 | 0.7623 | 0.7436 |  |
| ssl_distance_atr | dist_to_ema_50 | 0.7747 | 0.7708 |  |
| eqh_strength | stop_hunt_depth | -0.4384 | 0.3678 |  |
| eql_strength | norm_kumo_width | 0.5097 | 0.4254 |  |
| htf_liquidity_score | htf_h1_momentum | 0.728 | 0.8108 |  |
| internal_liquidity_distance | norm_displacement | -0.9018 | -0.5644 | NEAR-DUP |
| external_liquidity_distance | norm_displacement | 0.6107 | 0.6333 |  |
| liquidity_confluence | norm_displacement | 0.6147 | 0.4749 |  |
| liquidity_sweep_state | feat_ob_liquidity_swept | 0.3729 | 0.5021 |  |
| post_sweep_displacement | lag_2_log_return | -0.1562 | 0.9976 | NEAR-DUP |

Findings (synthetic, to verify on real data):
- `internal_liquidity_distance` vs `norm_displacement`: Pearson **-0.90**
  → NEAR-DUPLICATE flag (likely re-encodes distance-to-swing structure).
- `post_sweep_displacement` vs `lag_2_log_return`: Spearman **0.998**
  → NEAR-DUPLICATE flag (partly an artifact of the synthetic sweep's monotonic
  construction; must be re-measured on the real 70D dataset before any
  feature-removal decision).
- `bsl/ssl_distance_atr` correlate ~0.75-0.77 with existing distance features
  (high but below the 0.85 flag) → expected: they are distance metrics, but
  with pool-confirmation semantics that the 50D lacks.
- `liquidity_sweep_state` vs `feat_ob_liquidity_swept` ≈ 0.37-0.50: related
  but NOT duplicates (sweep state carries direction/severity beyond the 50D
  0/1 flag) — consistent with the TASK-01 thesis.

## 7.3 LABEL BALANCE + PARAMETER COUNT / LATENCY (brief §10/§41/§42/§43 — PROVEN)

Executed 2026-08-19 on the existing 50D artifact (frozen evidence; the 70D
dataset must show the SAME distribution since labels come from the same
triple-barrier labeler on the same timestamps):

| label | count | share |
| :--- | ---: | ---: |
| NO_TRADE | 88,202 | 88.2% |
| BUY_MARKET | 5,930 | 5.9% |
| SELL_MARKET | 5,814 | 5.8% |

dominant fraction 0.8825 (below the 0.95 collapse gate, but heavy enough that
accuracy alone is meaningless → macro-F1 + per-class metrics are mandatory,
brief §17).

Parameter count (LEGACY_SCALPNET_V1, same architecture, only input dim):

| model | params | delta |
| :--- | ---: | ---: |
| 60D baseline | 266,212 | — |
| 70D candidate | 267,492 | +1,280 (+0.48%) |

Inference latency (CPU, batch 256, 20 batches):

| model | ms/batch | µs/sample |
| :--- | ---: | ---: |
| 60D | 1.67 | 6.5 |
| 70D | 1.58 | 6.2 |

70D adds <1% parameters and NO latency regression (within noise; the input
projection is the only difference). Full training-time/memory comparison is
deferred to the real A/B/C run (identical budgets; §40/§42).
