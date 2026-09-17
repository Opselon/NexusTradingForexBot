# ML-TRAIN-002 — Loss Function Exploration: Class-Weighted Focal Loss vs Label Smoothing

STREAM: STREAM E — TRAINING
PRIORITY: P2
STATUS: BLOCKED
DEPENDENCIES: ML-TRAIN-001, ML-LABEL-001
AGENT_ROLE: AGENT-ML-TRAIN
OWNERSHIP_SCOPE: src/nexus_scalp/training/losses.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Implement and benchmark specialized financial loss functions (Focal Loss, Class-Balanced Loss, Label Smoothing Cross-Entropy) to counter extreme NO_TRADE class imbalance and reduce probability overconfidence.

## WHY_IT_EXISTS
Financial datasets have 70-85% NO_TRADE samples. Standard CrossEntropyLoss with inverse weights either causes the network to predict NO_TRADE blindly (high accuracy, zero alpha) or over-predicts noisy trades. Hardcoded cross-entropy limits predictive calibration.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/model_generation/training.py` (Lines: `155`)
  - **Symbol:** `CandidateTrainer.loss_fn`
  - **Behavior:** nn.CrossEntropyLoss(weight=class_weights)
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** No modern focal loss or label smoothing capability
- **Path:** `src/nexus_scalp/training/walk_forward_trainer.py` (Lines: `1100`)
  - **Symbol:** `fine_tune_online.loss`
  - **Behavior:** nn.CrossEntropyLoss()
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- CrossEntropyLoss is hardcoded across all trainers.
- Class imbalance is currently handled only via static weights or oversampling.

## UNKNOWNs
- Optimal focusing parameter gamma (gamma=1.5 vs 2.0) for Gold M1 trade classification.

## SCOPE
Implement FocalLoss(gamma, alpha) and LabelSmoothingCrossEntropy(smoothing) in losses.py; benchmark on identical fold; evaluate balanced accuracy, minority F1, and Brier score.

## NON_GOALS
Do not hardcode a new loss function without empirical benchmark evidence.

## SOURCE_AREAS
- `src/nexus_scalp/model_generation/training.py`
- `src/nexus_scalp/training/`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/training/losses.py`
- `tests/unit/test_loss_functions.py`
- `docs/research/LOSS_FUNCTION_BENCHMARK.md`

## INVESTIGATION_PLAN
Inspect whether Focal Loss reduces gradients of easily-classified NO_TRADE examples, allowing minority BUY/SELL signals to update weights effectively.

## IMPLEMENTATION_PLAN
1. Implement FocalLoss(nn.Module) supporting multiclass with gamma and class weights.
2. Implement LabelSmoothingCrossEntropy(nn.Module).
3. Write tests/unit/test_loss_functions.py testing loss computation, gradient backprop, and reduction modes.
4. Execute comparative benchmark across 5 folds.
5. Document metrics in docs/research/LOSS_FUNCTION_BENCHMARK.md.

## TEST_PLAN
- `pytest tests/unit/test_loss_functions.py -v`

## BENCHMARK_PLAN
Measure minority class recall and Brier score across CrossEntropy vs Focal Loss vs Label Smoothing.

## EVIDENCE_REQUIRED
- Code file: src/nexus_scalp/training/losses.py
- Benchmark report docs/research/LOSS_FUNCTION_BENCHMARK.md
- Pytest output

## ACCEPTANCE_CRITERIA
1. FocalLoss and LabelSmoothing pass all mathematical gradient checks.
2. Benchmark report provides verifiable metric comparison across all 3 loss functions.

## ABORT_CONDITIONS
If Focal Loss produces NaN gradients with extreme logits, add numerical epsilon clamping.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `src/nexus_scalp/training/losses.py`
- `docs/research/LOSS_FUNCTION_BENCHMARK.md`

## SHARED_FILE_RISK
Low. AGENT-ML-TRAIN owns losses.py.
