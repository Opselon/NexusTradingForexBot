# ML-BT-001 — Trading Quality Metric Suite: Expectancy R, Profit Factor & Slippage Decay Curves

STREAM: STREAM I — BACKTEST/FORWARD TEST
PRIORITY: P1
STATUS: BLOCKED
DEPENDENCIES: ML-DATA-001
AGENT_ROLE: AGENT-BACKTEST
OWNERSHIP_SCOPE: src/nexus_scalp/research/trading_metrics.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Implement an institutional trading quality evaluation suite separating pure classification metrics (F1, Accuracy) from economic trading metrics (Expectancy R, Profit Factor, Max Drawdown, Calmar Ratio, Slippage Decay Curves).

## WHY_IT_EXISTS
A model with 65% classification accuracy can lose money if its losses are larger than its wins, while a model with 40% accuracy can be highly profitable with positive R-expectancy. Relying on classification accuracy alone leads to false model promotion.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/research/oos.py` (Lines: `40-70`)
  - **Symbol:** `MIN_ECONOMIC_OOS_EXPECTANCY_R`
  - **Behavior:** MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02; tracks economic R
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/model_generation/validation.py` (Lines: `80-140`)
  - **Symbol:** `confusion_and_class_metrics`
  - **Behavior:** Calculates accuracy, precision, recall, F1; lacks economic drawdown and profit factor calculation
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** Separation between ML metrics and trading metrics incomplete

## FACTS
- OOSGate requires expectancy >= 0.02R.
- Validation currently emphasizes classification metrics.

## UNKNOWNs
- Correlation between validation fold F1 score and realized economic Profit Factor on Gold.

## SCOPE
Create src/nexus_scalp/research/trading_metrics.py implementing calculate_economic_metrics(trades); compute Expectancy R, Profit Factor, Max Drawdown, Win Rate, Turnover, and Slippage Decay curve; integrate into ModelBenchmark report.

## NON_GOALS
Do not replace classification metrics; provide trading metrics alongside them.

## SOURCE_AREAS
- `src/nexus_scalp/research/oos.py`
- `src/nexus_scalp/model_generation/validation.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/research/trading_metrics.py`
- `tests/unit/test_trading_metrics.py`

## INVESTIGATION_PLAN
Ensure trade R-multiples account for actual entry/exit spread and broker commission costs.

## IMPLEMENTATION_PLAN
1. Implement calculate_economic_metrics() in trading_metrics.py.
2. Implement compute_slippage_decay() evaluating expectancy decay across slippage [0, 1, 2, 3, 5 ticks].
3. Write tests/unit/test_trading_metrics.py with known trade outcomes.
4. Verify Profit Factor, Max Drawdown, and Expectancy R formulas against canonical definitions.
5. Integrate into ValidationFactory report output.

## TEST_PLAN
- `pytest tests/unit/test_trading_metrics.py -v`

## BENCHMARK_PLAN
Evaluate 10,000 simulated trade executions; verify metric computation completes in < 50ms.

## EVIDENCE_REQUIRED
- Code file: src/nexus_scalp/research/trading_metrics.py
- Pytest output
- Sample economic metrics JSON report

## ACCEPTANCE_CRITERIA
1. calculate_economic_metrics() passes all mathematical unit tests.
2. Slippage decay curve correctly computes expectancy degradation across tick friction.

## ABORT_CONDITIONS
If trade list is empty or contains non-finite values, raise ValueError.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `src/nexus_scalp/research/trading_metrics.py`
- `tests/unit/test_trading_metrics.py`

## SHARED_FILE_RISK
Low. AGENT-BACKTEST owns trading metrics module.
