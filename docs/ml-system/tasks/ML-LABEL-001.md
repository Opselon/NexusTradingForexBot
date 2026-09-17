# ML-LABEL-001 — Friction-Aware Triple Barrier Horizon & ATR Multiplier Calibration

STREAM: STREAM C — LABELING
PRIORITY: P1
STATUS: BLOCKED
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
  - **Behavior:** Hardcoded defaults: tp_mult=1.1, sl_mult=1.0, max_holding=15, friction_usd=0.35, embargo=3
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** Parameters hardcoded without empirical justification
- **Path:** `src/nexus_scalp/labeling/triple_barrier.py` (Lines: `80-160`)
  - **Symbol:** `label_dataframe`
  - **Behavior:** Evaluates forward price path; touches upper barrier -> BUY, lower barrier -> SELL, time exit -> NO_TRADE
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- TripleBarrierLabeler generates 3-class outcome labels.
- Friction deduction ($0.35/oz) is subtracted from price before barrier touch.

## UNKNOWNs
- Class balance distribution across different TP/SL ratio pairs (e.g. 1.5:1.0 vs 1.1:1.0) under high vs low volatility regimes.

## SCOPE
Build scripts/analysis/calibrate_triple_barrier.py; sweep TP multipliers [1.0, 1.2, 1.5, 2.0], SL multipliers [0.8, 1.0, 1.2], and holding bars [10, 15, 30]; report class distribution, barrier touch rates, and economic R-expectancy per parameter set.

## NON_GOALS
Do not break the 3-class label contract (0: NO_TRADE, 1: BUY, 2: SELL).

## SOURCE_AREAS
- `src/nexus_scalp/labeling/triple_barrier.py`
- `scripts/analysis/`

## FILES_LIKELY_TO_CHANGE
- `scripts/analysis/calibrate_triple_barrier.py`
- `docs/research/TRIPLE_BARRIER_CALIBRATION.md`

## INVESTIGATION_PLAN
Inspect what percentage of samples hit the time-expiration barrier vs vertical TP/SL barriers under current 1.1:1.0 defaults.

## IMPLEMENTATION_PLAN
1. Write scripts/analysis/calibrate_triple_barrier.py sweeping parameter grid.
2. Evaluate on 180-day historical gold dataset.
3. Calculate class ratios (NO_TRADE %, BUY %, SELL %) and barrier touch distribution for each configuration.
4. Publish docs/research/TRIPLE_BARRIER_CALIBRATION.md with recommended optimal parameter ranges.
5. Add unit test verifying parameter propagation.

## TEST_PLAN
- `pytest tests/unit/test_triple_barrier_parameters.py -v`

## BENCHMARK_PLAN
Process 100,000 bars across 12 parameter combinations; measure execution time and memory consumption.

## EVIDENCE_REQUIRED
- Calibration report: docs/research/TRIPLE_BARRIER_CALIBRATION.md
- Class balance distribution plots/tables

## ACCEPTANCE_CRITERIA
1. Calibration sweep completed across parameter space.
2. Clear empirical evidence documenting optimal TP/SL/holding configurations for Gold M1.

## ABORT_CONDITIONS
If any configuration produces 0 BUY or 0 SELL labels across the entire dataset, document pathology and discard configuration.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `scripts/analysis/calibrate_triple_barrier.py`
- `docs/research/TRIPLE_BARRIER_CALIBRATION.md`

## SHARED_FILE_RISK
Low. AGENT-LABEL owns labeling directory.
