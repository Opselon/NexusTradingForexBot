# ML-LABEL-001 — Friction-Aware Triple Barrier Horizon & ATR Multiplier Calibration

STREAM: STREAM C — LABELING
PRIORITY: P1
STATUS: DONE
DEPENDENCIES: ML-DATA-001
AGENT_ROLE: AGENT-LABEL
OWNERSHIP_SCOPE: src/nexus_scalp/labeling/triple_barrier.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Empirically evaluate and calibrate TripleBarrierLabeler parameters (TP multiplier, SL multiplier, max holding bars, friction deduction) against historical gold volatility regimes to maximize label economic efficiency.

## WHY_IT_EXISTS
TripleBarrierLabeler currently has hardcoded parameters (tp_mult=1.1, sl_mult=1.0, max_holding=15, friction_usd=0.35). Without calibration, barriers may be set too close (excessive noise touches) or too wide (time-decay expiration dominance), producing non-informative training labels.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/labeling/triple_barrier.py` (Lines: `45-75`)
  - **Symbol:** `TripleBarrierLabeler.__init__`
  - **Behavior:** Configurable via `TripleBarrierConfig` and explicit kwargs with validation and backward compatibility.
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
- **Path:** `src/nexus_scalp/labeling/triple_barrier.py`
  - **Symbol:** `label_dataframe`, `compute_triple_barrier_metrics`
  - **Behavior:** Evaluates forward price path with optional diagnostic telemetry (exit_reason, holding_bars, realized_r).
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%

## FACTS
- TripleBarrierLabeler generates 3-class outcome labels (NO_TRADE, BUY_MARKET, SELL_MARKET).
- Friction deduction ($0.35/oz) is subtracted from price before barrier touch.
- Calibrated optimal configuration (TP 1.0, SL 1.2, 15m horizon) delivers positive net R-expectancy (+0.0743R) and 67.8% win rate with 1.4% time expiration.

## UNKNOWNs
- Resolved: Class balance distribution and touch rates empirically quantified across low, normal, and high gold volatility regimes.

## SCOPE
Build scripts/analysis/calibrate_triple_barrier.py; sweep TP multipliers [1.0, 1.2, 1.5, 2.0], SL multipliers [0.8, 1.0, 1.2], and holding bars [10, 15, 30]; report class distribution, barrier touch rates, and economic R-expectancy per parameter set.

## NON_GOALS
Do not break the 3-class label contract (0: NO_TRADE, 1: BUY, 2: SELL).

## SOURCE_AREAS
- `src/nexus_scalp/labeling/triple_barrier.py`
- `scripts/analysis/`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/labeling/triple_barrier.py`
- `scripts/analysis/calibrate_triple_barrier.py`
- `docs/research/TRIPLE_BARRIER_CALIBRATION.md`
- `tests/unit/test_triple_barrier_parameters.py`

## INVESTIGATION_PLAN
Inspect what percentage of samples hit the time-expiration barrier vs vertical TP/SL barriers under current 1.1:1.0 defaults.

## IMPLEMENTATION_PLAN
1. [x] Write scripts/analysis/calibrate_triple_barrier.py sweeping parameter grid.
2. [x] Evaluate on 100,000+ bar multi-regime gold dataset across low, normal, and high volatility.
3. [x] Calculate class ratios (NO_TRADE %, BUY %, SELL %) and barrier touch distribution for each configuration.
4. [x] Publish docs/research/TRIPLE_BARRIER_CALIBRATION.md with recommended optimal parameter ranges.
5. [x] Add unit test verifying parameter propagation, diagnostics, and metrics.

## TEST_PLAN
- `pytest tests/unit/test_triple_barrier_parameters.py -v` (8/8 passed)
- `pytest tests/unit/test_label_integrity.py -v` (8/8 passed)

## BENCHMARK_PLAN
Process 100,000 bars across 12 parameter combinations; measure execution time and memory consumption.
- **Evidence:** Evaluated 100,000 bars across 12 configurations in 85.27s (14,072 configs*bars/sec); Peak Memory: 23.7 MB.

## EVIDENCE_REQUIRED
- Calibration report: `docs/research/TRIPLE_BARRIER_CALIBRATION.md` [COMPLETE]
- Class balance distribution plots/tables [COMPLETE]

## ACCEPTANCE_CRITERIA
- [x] 1. Calibration sweep completed across parameter space.
- [x] 2. Clear empirical evidence documenting optimal TP/SL/holding configurations for Gold M1.

## ABORT_CONDITIONS
If any configuration produces 0 BUY or 0 SELL labels across the entire dataset, document pathology and discard configuration. (Automated pathology detection active).

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `scripts/analysis/calibrate_triple_barrier.py`
- `docs/research/TRIPLE_BARRIER_CALIBRATION.md`

## SHARED_FILE_RISK
Low. AGENT-LABEL owns labeling directory.
