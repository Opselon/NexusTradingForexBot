# ML-ARCH-002 — Causal TCN Dilation & Receptive Field Optimization

STREAM: STREAM D — MODEL ARCHITECTURE
PRIORITY: P2
STATUS: BLOCKED
DEPENDENCIES: ML-ARCH-001, ML-DATA-001
AGENT_ROLE: AGENT-ML-ARCH
OWNERSHIP_SCOPE: src/nexus_scalp/model_generation/architectures.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Optimize CausalConv1dBlock dilation schedules, kernel sizes, and receptive field depth in TCN_ATTENTION_V1, ensuring receptive field matches M1 market swing duration without lookahead leakage.

## WHY_IT_EXISTS
TCN blocks currently use a fixed dilation rate (2**b with kernel_size=3). The relationship between dilation depth, effective receptive field, and financial market memory on Gold M1 has not been formally evaluated.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/model_generation/architectures.py` (Lines: `35-85`)
  - **Symbol:** `CausalConv1dBlock`
  - **Behavior:** Implements left-padded causal conv1d + GELU + residual + LayerNorm with dilation=2**b
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/model_lab/architectures.py` (Lines: `45-90`)
  - **Symbol:** `TeacherTCNAttention`
  - **Behavior:** Stacks 4 CausalConv1d blocks; receptive field = 1 + sum((kernel_size - 1) * 2**b)
  - **Classification:** `RESEARCH`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- Left causal padding guarantees zero lookahead.
- Receptive field formula: $RF = 1 + \sum_{b=0}^{B-1} (k-1) \times d_b$.

## UNKNOWNs
- Optimal receptive field for M1 gold scalping: 16 bars (microstructure), 32 bars (session swing), or 64 bars (multi-hour structure).

## SCOPE
Analyze and parameterize receptive field in CausalConv1dBlock; verify left-padding causal invariant under test; benchmark receptive field depths [16, 32, 64 bars].

## NON_GOALS
Do not implement non-causal centered convolutions.

## SOURCE_AREAS
- `src/nexus_scalp/model_generation/architectures.py`
- `src/nexus_scalp/model_lab/architectures.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/model_generation/architectures.py`
- `tests/unit/test_causal_conv_invariants.py`

## INVESTIGATION_PLAN
Mathematically verify that output at index t depends ONLY on inputs at indices <= t by computing Jacobian matrix $\frac{\partial y_t}{\partial x_{t+k}}$ (must be exactly zero for all $k > 0$).

## IMPLEMENTATION_PLAN
1. Write tests/unit/test_causal_conv_invariants.py asserting $\frac{\partial y_t}{\partial x_{t+k}} == 0$ for all $k > 0$.
2. Parameterize dilation factors in CausalConv1dBlock factory.
3. Calculate exact receptive field for configurations B=3, 4, 5 with k=3.
4. Measure forward pass latency across receptive fields.
5. Publish architectural recommendation in docs/research/TCN_RECEPTIVE_FIELD.md.

## TEST_PLAN
- `pytest tests/unit/test_causal_conv_invariants.py -v`

## BENCHMARK_PLAN
Measure gradient Jacobian causality test on 1,000 random inputs; must have zero non-causal entries.

## EVIDENCE_REQUIRED
- Mathematical causality proof test output
- Report: docs/research/TCN_RECEPTIVE_FIELD.md

## ACCEPTANCE_CRITERIA
1. Causal invariance mathematically proven via Jacobian test (zero future gradient leakage).
2. Receptive field formula validated across all block configurations.

## ABORT_CONDITIONS
If any future gradient is non-zero, STOP immediately and report causal padding defect.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `tests/unit/test_causal_conv_invariants.py`
- `docs/research/TCN_RECEPTIVE_FIELD.md`

## SHARED_FILE_RISK
Low. AGENT-ML-ARCH owns architectures.py.
