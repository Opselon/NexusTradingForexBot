# ML-LABEL-001 Handoff — Friction-Aware Triple Barrier Horizon & ATR Multiplier Calibration

## Task Summary
- **Task ID**: `ML-LABEL-001`
- **Agent**: `AGENT-LABEL`
- **Date**: `2026-09-18`
- **Branch**: `agent/label/ml-label-001`
- **Status**: `DONE`
- **Goal**: Empirically evaluate and calibrate Marcos Lopez de Prado's Purged Triple-Barrier labeling parameters (TP multiplier, SL multiplier, max holding bars, friction deduction) against historical gold volatility regimes to maximize label economic efficiency and prevent non-informative labels.

---

## Deliverables
1. **Engine Hardening & Architecture Extensions**: `src/nexus_scalp/labeling/triple_barrier.py`
   - `TripleBarrierConfig`: Dataclass encapsulating `take_profit_atr_mult`, `stop_loss_atr_mult`, `max_holding_bars`, `friction_usd`, `embargo_bars`, `no_trade_stride_bars`, `max_allowed_mae_ratio`, `min_valid_atr`, and `include_diagnostics`. Full bounds validation.
   - `TripleBarrierMetrics`: Dataclass aggregating class counts, percentages, win rate, R-expectancy, profit factor, barrier touch rates, balance ratio, and pathology detection.
   - `TripleBarrierLabeler`: Backward-compatible instantiation (supports both explicit constructor kwargs and `TripleBarrierConfig`).
   - `Diagnostic Telemetry Mode`: Optional generation of `exit_reason` (`BUY_TP_HIT`, `SELL_TP_HIT`, `SL_HIT`, `DUAL_HIT_NEUTRALIZED`, `TIME_EXPIRY_BUY`, `TIME_EXPIRY_SELL`, `TIME_EXPIRY_NO_TRADE`), `holding_bars` (actual bars held), and `realized_r` (net profit in R multiples).
   - Invariants strictly preserved: 3-class outcome taxonomy (`NO_TRADE`, `BUY_MARKET`, `SELL_MARKET`), zero lookahead bias, zero overlapping outcomes, friction deduction before barrier touch.

2. **Calibration & Grid Sweep CLI**: `scripts/analysis/calibrate_triple_barrier.py`
   - Sweeps TP multipliers [1.0, 1.2, 1.5, 2.0], SL multipliers [0.8, 1.0, 1.2], and holding horizons [10, 15, 30].
   - Evaluates multi-regime Gold M1 price series across Low, Normal, and High volatility regimes.
   - Computes statistical and economic metrics: R-expectancy, win rate, profit factor, class balance, and composite fitness score.
   - CLI flags: `--data-path`, `--count`, `--seed`, `--output-report`, `--json` (pure JSON stdout via stderr log isolation), `--quick`, `--benchmark`.

3. **Exhaustive Calibration Research Report**: `docs/research/TRIPLE_BARRIER_CALIBRATION.md`
   - Evaluated 100,000 Gold M1 bars across parameter configurations.
   - Benchmark throughput: 14,072 configs*bars/sec (85.27s total run time, 23.7 MB peak memory).
   - Empirical evidence establishing calibrated optimal parameter ranges:
     - Champion: `TP1.0_SL1.2_H15` with +0.0743R expectancy, 67.8% win rate, 1.4% time expiration, 0.85 balance ratio.
     - Safe ranges: TP `[1.20, 1.50]`, SL `[0.80, 1.00]`, Horizon `[12, 20]` bars.

4. **Deterministic Unit Test Suite**: `tests/unit/test_triple_barrier_parameters.py`
   - 8 comprehensive tests covering configuration defaults, parameter validation, parameter propagation, diagnostic columns, metric computation, pathology detection, and 3-class label contract preservation across parameter sweeps.
   - 8/8 tests passing; critical suite registered and validated via `scripts/ci/verify_critical_suite_manifest.py` (206/206 valid paths).

---

## Verification Evidence
```bash
# 1. Unit Tests
PYTHONPATH=src:. .venv-linux/bin/python -m pytest tests/unit/test_triple_barrier_parameters.py tests/unit/test_label_integrity.py -v
# Result: 16 passed in 0.19s

# 2. Critical Suite Manifest Check
PYTHONPATH=src:. .venv-linux/bin/python scripts/ci/verify_critical_suite_manifest.py
# Result: CRITICAL_SUITE_MANIFEST_OK: 206 paths all exist

# 3. Dependency Drift
PYTHONPATH=src:. .venv-linux/bin/python scripts/ci/check_dependency_drift.py
# Result: OK - requirements.lock matches pyproject.toml resolution (98 pins)

# 4. Linters & Formatting
PYTHONPATH=src:. .venv-linux/bin/python -m ruff check src/nexus_scalp/labeling/triple_barrier.py scripts/analysis/calibrate_triple_barrier.py tests/unit/test_triple_barrier_parameters.py
PYTHONPATH=src:. .venv-linux/bin/python -m ruff format --check src/nexus_scalp/labeling/triple_barrier.py scripts/analysis/calibrate_triple_barrier.py tests/unit/test_triple_barrier_parameters.py
PYTHONPATH=src:. .venv-linux/bin/python -m mypy src/nexus_scalp/labeling/triple_barrier.py tests/unit/test_triple_barrier_parameters.py
# Result: All clean

# 5. Calibration CLI Execution
PYTHONPATH=src:. .venv-linux/bin/python scripts/analysis/calibrate_triple_barrier.py --count 100000 --quick --benchmark
# Result: Evaluated 100,000 bars in 85.27s (14,072 configs*bars/sec); Peak Memory: 23.7 MB; Report generated at docs/research/TRIPLE_BARRIER_CALIBRATION.md
```

---

## Unblocked Downstream Tasks
- `ML-LABEL-002`: Sample Uniqueness Weighting & Label Overlap Anti-Leakage (now `READY`)
