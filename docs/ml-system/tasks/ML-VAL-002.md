# ML-VAL-002 — Probability Calibration & Expected Calibration Error (ECE) Evaluation

STREAM: STREAM G — VALIDATION/OOS
PRIORITY: P2
STATUS: BLOCKED
DEPENDENCIES: ML-EXP-001
AGENT_ROLE: AGENT-ML-VALIDATION
OWNERSHIP_SCOPE: src/nexus_scalp/research/calibration.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Measure the empirical calibration of ScalpNet softmax probabilities using Expected Calibration Error (ECE), reliability diagrams, and Brier score, implementing Temperature Scaling to correct probability miscalibration.

## WHY_IT_EXISTS
Neural network softmax outputs are notoriously overconfident. A model predicting 0.90 confidence may only be correct 60% of the time. In trading, policy gates rely on confidence thresholds (e.g. 0.60 for high-conviction); uncalibrated probabilities cause misaligned trade sizing and false triggers.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/application/live/inference.py` (Lines: `170-185`)
  - **Symbol:** `InferenceService.infer_probabilities`
  - **Behavior:** Applies raw softmax to logits; returns [P_NO_TRADE, P_BUY, P_SELL]
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** Assumes raw softmax outputs are calibrated probabilities
- **Path:** `src/nexus_scalp/signals/policy.py` (Lines: `550-600`)
  - **Symbol:** `SignalPolicy.evaluate_probabilities`
  - **Behavior:** Thresholds confidence >= 0.35 or >= 0.60 to trigger orders
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** Relies on uncalibrated confidence

## FACTS
- Raw softmax output is passed to SignalPolicy.
- Policy applies hard confidence thresholds.
- No probability calibration is performed.

## UNKNOWNs
- Magnitude of calibration error across different market volatility regimes.

## SCOPE
Create src/nexus_scalp/research/calibration.py; implement compute_ece(probs, labels, bins=10); implement TemperatureScaler(nn.Module); measure ECE before and after temperature scaling on OOS validation data.

## NON_GOALS
Do not alter the raw model forward pass weights.

## SOURCE_AREAS
- `src/nexus_scalp/application/live/inference.py`
- `src/nexus_scalp/research/`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/research/calibration.py`
- `tests/unit/test_calibration.py`
- `docs/research/CALIBRATION_AUDIT.md`

## INVESTIGATION_PLAN
Review Guo et al. (2017) 'On Calibration of Modern Neural Networks' for temperature scaling optimization via NLL loss.

## IMPLEMENTATION_PLAN
1. Implement compute_ece() and reliability_diagram() in calibration.py.
2. Implement TemperatureScaler learning scalar T > 0 on validation fold.
3. Evaluate uncalibrated ScalpNet outputs: measure ECE and Brier score.
4. Fit TemperatureScaler and evaluate calibrated ECE.
5. Publish report in docs/research/CALIBRATION_AUDIT.md.

## TEST_PLAN
- `pytest tests/unit/test_calibration.py -v`

## BENCHMARK_PLAN
Measure ECE reduction from raw softmax to temperature-scaled probabilities on 20,000 OOS samples.

## EVIDENCE_REQUIRED
- Code file: src/nexus_scalp/research/calibration.py
- Report docs/research/CALIBRATION_AUDIT.md
- Pytest output

## ACCEPTANCE_CRITERIA
1. compute_ece() and TemperatureScaler pass all unit tests.
2. Temperature scaling successfully reduces ECE on OOS validation set.

## ABORT_CONDITIONS
If optimal temperature T converges to 0 or infinity, check for exploding validation logits.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `src/nexus_scalp/research/calibration.py`
- `docs/research/CALIBRATION_AUDIT.md`

## SHARED_FILE_RISK
Low. AGENT-ML-VALIDATION owns calibration module.
