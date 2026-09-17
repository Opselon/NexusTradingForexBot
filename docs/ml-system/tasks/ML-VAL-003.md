# ML-VAL-003 — Robustness Stress Testing: Friction Clamp, Slippage & Spread Perturbation

STREAM: STREAM G — VALIDATION/OOS
PRIORITY: P2
STATUS: BLOCKED
DEPENDENCIES: ML-VAL-001
AGENT_ROLE: AGENT-ML-VALIDATION
OWNERSHIP_SCOPE: src/nexus_scalp/research/robustness.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Verify and harden RobustnessGate (GATE8) by subjecting candidate model trading decisions to spread perturbation (+1 to +5 pips) and execution slippage (+1 to +2 ticks), asserting degradation <= 50%.

## WHY_IT_EXISTS
Models optimized for zero-slippage paper backtests often degrade catastrophically under real market execution where broker spreads expand and slippage reduces trade profitability.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/research/robustness.py` (Lines: `45-120`)
  - **Symbol:** `RobustnessGate, evaluate_friction_robustness`
  - **Behavior:** Perturbs friction costs; evaluates economic degradation; enforces max_degradation <= 0.50
  - **Classification:** `PRODUCTION GATING`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/model_lifecycle/gates.py` (Lines: `258-280`)
  - **Symbol:** `gate_robustness`
  - **Behavior:** GATE8_ROBUSTNESS: checks degradation <= 0.50 under perturbed friction
  - **Classification:** `CANDIDATE`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- GATE8 enforces degradation <= 50% under friction stress.
- BUG-299 friction clamp ensures spread + slip + 4.

## UNKNOWNs
- Degradation curve of candidate models under extreme market open spread spikes.

## SCOPE
Write tests/unit/test_robustness_stress_scenarios.py; simulate spread spikes (+10, +20, +50 USD) and slippage (+1, +2 ticks); assert candidate rejection when degradation > 50%.

## NON_GOALS
Do not relax the 50% degradation ceiling.

## SOURCE_AREAS
- `src/nexus_scalp/research/robustness.py`
- `src/nexus_scalp/model_lifecycle/gates.py:258-280`

## FILES_LIKELY_TO_CHANGE
- `tests/unit/test_robustness_stress_scenarios.py`

## INVESTIGATION_PLAN
Verify how RobustnessGate handles trades where spread exceeds take-profit distance (should fail closed).

## IMPLEMENTATION_PLAN
1. Create tests/unit/test_robustness_stress_scenarios.py.
2. Generate mock trade series with known R-distribution.
3. Run evaluate_friction_robustness under baseline friction.
4. Run under severe friction: spread=2.0 USD, slip=2 ticks.
5. Assert gate_robustness rejects trades with degradation > 0.50.
6. Assert gate_robustness passes robust trades.

## TEST_PLAN
- `pytest tests/unit/test_robustness_stress_scenarios.py -v`

## BENCHMARK_PLAN
Execute robustness evaluation on 1,000 trade records across 4 friction stress levels.

## EVIDENCE_REQUIRED
- Test file: tests/unit/test_robustness_stress_scenarios.py
- Passing pytest execution output

## ACCEPTANCE_CRITERIA
1. tests/unit/test_robustness_stress_scenarios.py passes with 0 failures.
2. Candidates with degradation > 50% are fail-closed rejected by GATE8.

## ABORT_CONDITIONS
If friction perturbation allows negative expectancy models to pass gate, STOP and report safety defect.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `tests/unit/test_robustness_stress_scenarios.py`

## SHARED_FILE_RISK
Low. AGENT-ML-VALIDATION owns robustness tests.
