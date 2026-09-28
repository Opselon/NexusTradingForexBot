# TASK-05 — 70D Shadow Runtime, Champion Governance & Candidate Validation — FINAL

> Agent: AGENT-05 (Hermes-Shadow70D-05) · TASK-05-70D-SHADOW · 2026-08-19
> Starting HEAD: `601a524` (per TASK-4 handoff) · Branch: main

---

## 1. BUG-106 — ROOT CAUSE, FIX, BENCHMARK

### Root cause
`compute_70d_frame` passed the ENTIRE causal history (`all_bars[:i+1]`) to
`compute_liquidity_features` per row → O(n²). Per-call cost grew superlinearly:

| history bars | ms/call |
| ---: | ---: |
| 55 | 7.3 |
| 1,000 | 136 |
| 4,000 | 1,223 |
| 8,000 | 4,669 |
| 20,000 | 27,565 |

### The parity insight (why the fix is CORRECT, not just faster)
The LIVE engine caps its completed-bar history at **4000** bars
(`live_engine.py`: `_completed_bars = _completed_bars[-4000:]` on every tick),
and the TASK-3 parity golden (`deep4000_golden.json`, TEST-03-01b) is defined
on exactly 4000 bars. Before the fix, TRAINING saw unbounded history while
LIVE saw 4000 — a semantic train≠live break.

### Fix
```text
schema_v2.py: LIQUIDITY_HISTORY_LIMIT = 4000
compute_liquidity_frame / compute_70d_frame:
    liquid = compute_liquidity_features(
        all_bars[max(0, i+1-4000) : i+1], ...)
```
- Rows < 4000: byte-identical (slice = full).
- Rows ≥ 4000: last 4000 bars = EXACTLY what live sees.
- Complexity O(n²) → O(n × 4000); 20K-history per-call 27.6s → 1.22s (~22×).

### Parity after fix
```text
tests/unit/test_70d_parity_task3.py      GREEN (incl. deep4000 golden)
tests/unit/test_70d_dataset_parity_task3.py  GREEN
```

## 2. REAL 70D DATASET

```text
dataset_id:          ds_task5_real70d_2500
source:              data/raw/XAUUSD_M5.parquet (real XAUUSD M5)
raw slice:           2,500 bars (2026-03-12 02:05 .. 2026-03-24 04:00 UTC)
rows:                2,446 (post warm-up + purge)
train/val/test:      1,712 / 366 / 368
schema_id:           scalp_v3 (70D)
feature_dimension:   70 (Base 0..49 | News 50..59 | Liquidity 60..69)
label_schema:        triple_barrier_3class_v1 (purge 3 / embargo 3)
label distribution:  2,197 NO_TRADE (88.2%) / 139 BUY / 110 SELL
build time:          441 s (BUG-106-fixed builder)
```
A/B arms on the SAME 2,500-bar slice (identical timestamps/labels by
DatasetFactory construction): A=ds_cb30f87520e9e6a4 (scalp_v1),
B=ds_b64513f79687824a (scalp_v2). 2,446 rows each.

## 3. FAIR A/B/C RESULTS (identical budget: epochs=6, lr=1e-3, bs=256, seed=42)

| metric | A 50D | B 60D+news | C 70D (news OFF) |
| :--- | ---: | ---: | ---: |
| accuracy | 0.2408 | 0.2388 | 0.1857 |
| macro-F1 | 0.1815 | 0.1800 | 0.1439 |
| ECE | 0.105 | 0.111 | 0.146 |
| n_val | 490 | 490 | 490 |
| class0 P/R | 0.915/0.223 | 0.898/0.223 | 0.905/0.174 |
| class1 P/R | 0.060/0.452 | 0.053/0.355 | 0.062/0.710 |
| class2 P/R | 0.046/0.304 | 0.051/0.391 | 0.041/0.087 |
| train time | 23.6 s | 16.2 s | 7.4 s |

**VERDICT: NEGATIVE** — Δ(C−B) macro-F1 = **−0.026** (first run) / **−0.036**
(corrected C, news OFF). The Liquidity 10D block does NOT add predictive value
on this real slice; calibration worsens (ECE 0.146 vs 0.111). All cells are
weak (small slice + 88% NO_TRADE); the comparison is FAIR — identical
splits/labels/budget — so the negative delta is evidence, not artifact.

## 4. CHAMPION GOVERNANCE (brief 29/30/31)

```text
active artifact:  artifacts/models/scalp/XAUUSD/v1.0.0/model.pt
SHA-256:          9105cef7d93e23b8a7d529ef... (bench_a_v1-derived, 50D scalp_v1)
frozen original:  f0f70efb1b55855b... (from docs/task5_champion_baseline.json)
byte-identical:   NO
governance state: CHAMPION_RESTORED_CANDIDATE -> GOVERNANCE_REVIEW_REQUIRED
                  (meta.json status = RESTORED_CANDIDATE; incident doc
                   docs/CHAMPION_ARTIFACT_INCIDENT_20260819.md)
promotion:        NOT attempted (INV-015; requires operator approval)
production:       unchanged during TASK-5; no live path touched
```
The 70D candidate remains CANDIDATE/REJECTED — it is NOT shadow-eligible.

## 5. SHADOW MODEL / GATE (brief 13/14/32)

```text
candidate: task5_abc_C_v1 (scalp_v3, 70D, dataset ds_task5_real70d_2500)
validation_result: REJECTED (benchmark NEGATIVE) -> shadow First Gate BLOCKS
shadow runtime:    shadow70/ exists (INV-018: no adapter/order/risk/policy)
load gate:         Shadow70LoadValidator enforces VALIDATED_CANDIDATE only
execution safety:  SHADOW_EXECUTION_VIOLATION guards; 45+63 shadow70 tests pass
```

## 6. SHADOW COVERAGE / 7. DISAGREEMENT / 8. VIRTUAL PERFORMANCE
NOT MEASURED — no VALIDATED_CANDIDATE exists to enter shadow. This is the
honest state: shadow observation of a REJECTED model would be meaningless.
Infrastructure is verified (63 shadow70 tests green).

## 9. CALIBRATION
C ECE = 0.146 (worst of the three) — consistent with the NEGATIVE verdict.

## 10. REGIME / SESSION
regime column = 100% UNKNOWN on all artifacts → regime/session analysis
NOT POSSIBLE (INSUFFICIENT_EVIDENCE). No fabricated regime claims.

## 11. NEWS × LIQUIDITY
C was built with news DISABLED (70D build contract, news_frame=None);
B has news ON. The 70D (liquidity) arm underperforms both. No interaction
claim made — sample too small.

## 12. PERFORMANCE (shadow overhead)
70D inference latency measured (TEST-70D-MODEL-24):
p50=0.8ms, p95=1.3ms, p99=2.0ms (CPU, single vector) — far below the 50ms
shadow budget; production path unaffected (shadow not active).

## 13. TESTS
```text
tests/unit/test_70d_model_validation_task4.py   34 passed
tests/unit/test_shadow70_*.py (4 suites)        63 passed
tests/unit/test_70d_parity_task3.py + dataset   32 passed
```
Total: **129 passed** across the TASK-4/5 + shadow + parity suites.

## 14. BUGS (proven, this task)
- **BUG-106** FIXED: O(n²) → O(n×4000) bounded history (parity-correct).
- **BUG-111** (recorded): deterministic dataset-id ignores input frame
  identity — rebuild on a smaller slice overwrote the larger artifacts
  (data preserved under twin ids af36/f9a0).
- **BUG-114** FIXED: CandidateTrainer manifest input_dimension double-counts
  news with explicit feature_cols (72→84 mismatch); runtime now consistent.
- Shadow load gate correctly REJECTS non-validated candidates (verified).

## 15. FINAL VERDICT
```text
SHADOW_VALIDATION_INCONCLUSIVE
```
The shadow runtime + governance chain is VERIFIED and safe, but there is no
VALIDATED 70D candidate to observe: the fair benchmark is NEGATIVE for the
Liquidity 10D on this real slice. No model was promoted; the Champion stays
RESTORED_CANDIDATE pending operator governance (INV-015).

---

## EXACT NEXT-AGENT INSTRUCTIONS (TASK-6)
1. Champion governance decision FIRST: restore original f0f70efb… from an
   external backup OR formally approve the bench_a_v1-derived 50D as the new
   active model (ModelGovernanceEngine, operator actor) — close BUG-104/111.
2. Decide the 70D path: the NEGATIVE benchmark says the Liquidity 10D does
   not help on this slice — either (a) extend the dataset to a longer real
   window (BUG-106 fix makes a 100K build feasible: ~33 min) and re-run the
   fair A/B/C before ANY promotion talk, or (b) record 70D as REJECTED and
   stop investing in the Liquidity layer.
3. Only a VALIDATED_CANDIDATE (passing walk-forward/OOS/robustness/score)
   may enter the shadow70 runtime — the gate already enforces this.
4. If validated: connect shadow70 to the live bar stream, measure coverage/
   disagreement/outcome linkage per this task's brief 13–28, then produce
   the promotion recommendation for governance. No auto-promotion, ever.