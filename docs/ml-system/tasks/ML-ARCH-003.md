# ML-ARCH-003 — Temporal Multihead Attention vs Positional Encoding Ablation

STREAM: STREAM D — MODEL ARCHITECTURE
PRIORITY: P2
STATUS: BLOCKED
DEPENDENCIES: ML-ARCH-002
AGENT_ROLE: AGENT-ML-ARCH
OWNERSHIP_SCOPE: src/nexus_scalp/models/attention.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Ablate and evaluate the empirical contribution of Multihead Attention (heads=2, 4) and Positional Encodings (Sinusoidal vs Rotary vs None) in financial sequence modeling.

## WHY_IT_EXISTS
Standard Transformer attention was invented for natural language. In financial time series, permutation invariance and weak temporal order can cause self-attention to overfit to spurious cross-bar correlations unless constrained by causal masks and appropriate positional encodings.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/model_generation/architectures.py` (Lines: `110-150`)
  - **Symbol:** `TCN_ATTENTION_V1`
  - **Behavior:** Uses nn.MultiheadAttention(embed_dim=128, num_heads=4) after TCN blocks
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** Attention applied without causal attention mask
- **Path:** `src/nexus_scalp/model_lab/architectures.py` (Lines: `65-80`)
  - **Symbol:** `TeacherTCNAttention.attn`
  - **Behavior:** Uses nn.TransformerEncoder with norm_first=True
  - **Classification:** `RESEARCH`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- Multihead attention is used in TCN_ATTENTION_V1.
- Transformer layers add CPU inference latency.

## UNKNOWNs
- Whether self-attention adds measurable predictive alpha compared to pure TCN blocks on M1 bars.

## SCOPE
Create tests/unit/test_attention_ablation.py; compare: 1. Pure TCN (no attention), 2. TCN + Causal Attention, 3. TCN + Rotary Embeddings; measure validation loss and CPU latency.

## NON_GOALS
Do not deploy unverified attention architectures to production live serving.

## SOURCE_AREAS
- `src/nexus_scalp/model_generation/architectures.py`
- `src/nexus_scalp/model_lab/architectures.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/models/attention.py`
- `docs/research/ATTENTION_ABLATION.md`

## INVESTIGATION_PLAN
Check whether causal masking is enforced inside MultiheadAttention forward call (attn_mask parameter).

## IMPLEMENTATION_PLAN
1. Implement CausalSelfAttention layer with triangular causal mask.
2. Implement ablation runner comparing Pure TCN vs TCN + Causal Attention.
3. Train on identical dataset fold.
4. Measure validation loss, F1 score, and per-sample latency.
5. Document findings in docs/research/ATTENTION_ABLATION.md.

## TEST_PLAN
- `pytest tests/unit/test_attention_causality.py -v`

## BENCHMARK_PLAN
Measure CPU inference latency delta: Pure TCN vs TCN+Attention.

## EVIDENCE_REQUIRED
- Report docs/research/ATTENTION_ABLATION.md
- Pytest causality verification output

## ACCEPTANCE_CRITERIA
1. Attention layer strictly enforces causal masking.
2. Comparative benchmark documents exact Sharpe and latency trade-offs.

## ABORT_CONDITIONS
If attention layer exhibits memory leak or non-causal attention weights, abort.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `src/nexus_scalp/models/attention.py`
- `docs/research/ATTENTION_ABLATION.md`

## SHARED_FILE_RISK
Low. AGENT-ML-ARCH owns new attention module.
