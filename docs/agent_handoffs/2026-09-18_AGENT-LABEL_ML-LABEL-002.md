# ML-LABEL-002 Handoff — Sample Uniqueness Weighting & Label Overlap Anti-Leakage

## Task Summary
- **Task ID**: `ML-LABEL-002`
- **Agent**: `AGENT-LABEL`
- **Date**: `2026-09-18`
- **Branch**: `agent/label/ml-label-002`
- **Status**: `DONE`
- **Goal**: Implement Marcos Lopez de Prado's Sample Uniqueness Weighting algorithm (AFML Chapter 4) to down-weight overlapping training labels, eliminate serial correlation bias, and integrate directly with Polars DataFrames and PyTorch training pipelines.

---

## Deliverables
1. **Core Algorithmic Module**: `src/nexus_scalp/labeling/sample_weights.py`
   - `compute_concurrency_events(start_indices, end_indices, total_bars)`: Vectorized difference array + prefix sum algorithm computing concurrent active labels $c_t$ in $O(M + N)$ time and $O(N)$ space.
   - `compute_sample_uniqueness(df, holding_bars, ...)`: Calculates Marcos Lopez de Prado's average uniqueness $u_i = \frac{1}{L_i} \sum_{t \in [s_i, e_i]} c_t^{-1}$. Guarantees $u_i \in (0.0, 1.0]$. Strictly non-overlapping samples yield $u_i = 1.0$; identical concurrent samples yield $u_i = 1/K$; partial overlaps yield mathematically exact fractional uniqueness. Supports Polars DataFrames with `is_eval_sample` masking and raw array intervals.
   - `normalize_sample_weights(weights, target_sum)`: Normalizes active sample weights such that $\sum w_i = N$, preserving relative uniqueness proportions and zero masks for unevaluated samples.
   - `compute_return_attributed_weights(uniqueness, returns)`: Implements return attribution $w_{r,i} = u_i \cdot |r_i|$ (AFML Section 4.5).
   - `apply_time_decay(weights, decay_factor)`: Implements linear/exponential time decay across chronological sample sequence (AFML Section 4.6).
   - `compute_uniqueness_metrics(weights, ...)`: Generates structured `SampleUniquenessReport` with Kish's effective sample size $N_{eff} = \frac{(\sum w_i)^2}{\sum w_i^2}$, redundancy ratio $1 - \frac{N_{eff}}{N}$, and concurrency metrics.
   - `add_sample_weights_to_dataframe(df, ...)` & `export_sample_weights_artifact(...)`: Appends `sample_weight` column to Polars DataFrames and exports Parquet artifacts with companion `.meta.json` SHA-256 manifests.
   - **PyTorch Training Integration**:
     - `WeightedTensorDataset(features, targets, weights)`: PyTorch Dataset yielding `(features, targets, sample_weight)`.
     - `create_weighted_dataloader(..., use_sampler=True/False)`: DataLoader supporting both standard batch yielding and `WeightedRandomSampler` importance sampling.
     - `SampleWeightedCrossEntropyLoss`: Sample-weighted Cross Entropy Loss. Matches `F.cross_entropy` when $w=1.0$.
     - `SampleWeightedFocalLoss`: Sample-weighted multi-class Focal Loss.

2. **Package Export**: `src/nexus_scalp/labeling/__init__.py`
   - Clean re-exports of public labeling science APIs.

3. **Empirical Study & CLI Analysis**: `scripts/analysis/analyze_sample_uniqueness.py`
   - CLI analyzing sample uniqueness and concurrency distributions across Gold M1 candles labeled with Triple Barrier horizons.
   - Generates ASCII distribution histograms and exported JSON/Markdown reports.

4. **Research Artifact**: `docs/research/SAMPLE_UNIQUENESS_EMPIRICAL_STUDY.md`
   - Evaluated 10,000 Gold M1 bars (2,856 evaluated triple-barrier samples).
   - Mean uniqueness $u_i$: `0.8988`.
   - Kish effective sample size $N_{eff}$: `2,765.2`.
   - Sample redundancy ratio: `3.18%`.
   - Max concurrency: `4` concurrent active labels.
   - Execution time: `0.048s` (48ms).

5. **Comprehensive Unit Test Battery**: `tests/unit/test_sample_weights.py`
   - 22 comprehensive automated tests covering concurrency counting, mathematical bounds, non-overlapping exactness, identical overlap symmetry, hand-calculated fractional uniqueness, Polars DataFrame masking, normalization, return attribution, time decay, Kish metrics, Parquet export, PyTorch dataset/dataloader, PyTorch loss functions, and 50,000-row execution benchmark (<2.0s SLA).
   - 22/22 tests passing in 2.15s. Registered in `tests/critical_suite.txt` (208 paths valid).

---

## Verification Evidence
```bash
# 1. Unit Tests
PYTHONPATH=src:. .venv-linux/bin/python -m pytest tests/unit/test_sample_weights.py -v
# Result: 22 passed in 2.15s

# 2. Benchmark SLA Verification
PYTHONPATH=src:. .venv-linux/bin/python -m pytest tests/unit/test_sample_weights.py -k "test_benchmark_50k_rows_sla" -v
# Result: 50,000 rows across 50,000 bars executed in 13.4ms (<< 2.0s SLA limit)

# 3. Critical Suite Manifest Verification
PYTHONPATH=src:. .venv-linux/bin/python scripts/ci/verify_critical_suite_manifest.py
# Result: CRITICAL_SUITE_MANIFEST_OK: 208 paths all exist

# 4. Linters & Type Checking
PYTHONPATH=src:. .venv-linux/bin/python -m ruff check src/nexus_scalp/labeling/ tests/unit/test_sample_weights.py scripts/analysis/analyze_sample_uniqueness.py
PYTHONPATH=src:. .venv-linux/bin/python -m ruff format --check src/nexus_scalp/labeling/ tests/unit/test_sample_weights.py scripts/analysis/analyze_sample_uniqueness.py
PYTHONPATH=src:. .venv-linux/bin/python -m mypy --follow-imports=skip src/nexus_scalp/labeling/sample_weights.py
# Result: All clean, 0 issues

# 5. Empirical Study Execution
PYTHONPATH=src:. .venv-linux/bin/python scripts/analysis/analyze_sample_uniqueness.py --bars 10000 --export-md docs/research/SAMPLE_UNIQUENESS_EMPIRICAL_STUDY.md
# Result: Evaluated 10,000 bars in 0.048s; Report generated at docs/research/SAMPLE_UNIQUENESS_EMPIRICAL_STUDY.md
```

---

## Unblocked Downstream Tasks
- `ML-TRAIN-002`: Loss Function Exploration: Class-Weighted Focal Loss vs Label Smoothing (depends on `ML-TRAIN-001` and `ML-LABEL-002`).
