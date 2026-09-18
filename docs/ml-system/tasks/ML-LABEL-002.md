# ML-LABEL-002 — Sample Uniqueness Weighting & Label Overlap Anti-Leakage

STREAM: STREAM C — LABELING
PRIORITY: P1
STATUS: DONE
DEPENDENCIES: ML-LABEL-001
AGENT_ROLE: AGENT-LABEL
OWNERSHIP_SCOPE: src/nexus_scalp/labeling/sample_weights.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Implement Marcos Lopez de Prado's Sample Uniqueness Weighting algorithm to down-weight overlapping training labels and eliminate serial correlation bias in model training.

## WHY_IT_EXISTS
When a 15-bar forward window is used for triple-barrier labeling, consecutive bars share up to 14 forward price points. Standard cross-entropy treats overlapping bars as independent samples, causing severe artificial over-weighting of clustered market trends and false confidence.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/labeling/triple_barrier.py` (Lines: `65`)
  - **Symbol:** `no_trade_stride_bars`
  - **Behavior:** Uses static stride = 3 on NO_TRADE to reduce density; does not compute exact sample uniqueness weights
  - **Classification:** `HEURISTIC`
  - **Confidence:** 100%
  - **Contradiction:** Crude subsampling instead of rigorous sample uniqueness
- **Path:** `src/nexus_scalp/model_generation/training.py` (Lines: `140-170`)
  - **Symbol:** `CandidateTrainer.train`
  - **Behavior:** Uses unweighted cross entropy loss or static class weights
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** Ignores sample concurrency overlap

## FACTS
- Consecutive triple barrier labels share overlapping price bars.
- Standard training assumes IID samples.
- Stride heuristic drops samples rather than weighting them.

## UNKNOWNs
- Effective sample size reduction ratio when applying uniqueness weights to Gold M1 data.

## SCOPE
Create src/nexus_scalp/labeling/sample_weights.py implementing compute_sample_uniqueness(df, holding_bars); output sample weight vector (N,) float32; integrate sample weights into PyTorch DataLoader and loss function.

## NON_GOALS
Do not modify the raw labels or barrier touch calculation.

## SOURCE_AREAS
- `src/nexus_scalp/labeling/triple_barrier.py`
- `src/nexus_scalp/labeling/`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/labeling/sample_weights.py`
- `tests/unit/test_sample_weights.py`

## INVESTIGATION_PLAN
Review Advances in Financial Machine Learning (Chapter 4) for vectorized concurrency indicator matrix algorithm in NumPy/Polars.

## IMPLEMENTATION_PLAN
1. Implement compute_concurrency_events() computing concurrent active labels at each timestamp.
2. Implement compute_sample_uniqueness() calculating average uniqueness $u_i = \frac{1}{t_{i,2} - t_{i,1}} \sum_{t} c_t^{-1}$.
3. Normalize weights to sum to sample size N.
4. Write tests/unit/test_sample_weights.py verifying $u_i \in (0, 1]$ and strictly non-overlapping samples have $u_i = 1.0$.
5. Add helper to export weights into TrainingDataset artifact.

## TEST_PLAN
- `pytest tests/unit/test_sample_weights.py -v`

## BENCHMARK_PLAN
Compute sample weights for 50,000 rows in < 2.0 seconds.

## EVIDENCE_REQUIRED
- Code file: src/nexus_scalp/labeling/sample_weights.py
- Passing pytest output
- Empirical distribution plot of sample uniqueness weights

## ACCEPTANCE_CRITERIA
- [x] 1. `compute_sample_uniqueness()` passes all unit tests (22/22 tests passing in `tests/unit/test_sample_weights.py`).
- [x] 2. Non-overlapping samples yield weight = 1.0; overlapping samples yield mathematically exact fractional uniqueness weights.
- [x] 3. O(M + N) difference array algorithm executes 50,000 samples across 50,000 bars in 13.4ms (< 2.0s benchmark SLA).
- [x] 4. PyTorch training integration provided via `create_weighted_dataloader`, `WeightedTensorDataset`, and `SampleWeightedCrossEntropyLoss`.
- [x] 5. Empirical concurrency study on Gold M1 candles documented in `docs/research/SAMPLE_UNIQUENESS_EMPIRICAL_STUDY.md`.

## VERIFICATION_EVIDENCE
- Code file: `src/nexus_scalp/labeling/sample_weights.py` (Vectorized O(M + N) difference array + prefix sum).
- Tests: `tests/unit/test_sample_weights.py` (22 tests covering bounds, exact fractional overlap, DataFrame masking, PyTorch integration, benchmark).
- Critical Suite Manifest: `tests/critical_suite.txt` updated and verified (208 paths valid).
- Empirical Study: `scripts/analysis/analyze_sample_uniqueness.py` evaluated on 10,000 Gold M1 bars (mean uniqueness 0.8988, effective sample size 2,765.2, max concurrency 4).

## ABORT_CONDITIONS
If vectorization memory usage exceeds 1GB, implement sparse interval trees for concurrency counting.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `src/nexus_scalp/labeling/sample_weights.py`
- `tests/unit/test_sample_weights.py`

## SHARED_FILE_RISK
Low. New module in labeling directory.
