# AGENT HANDOFF: ML-RISK-001 — Position Replay & Economic Dataset Generation Engine

**Date:** 2026-09-18  
**Author:** AGENT-RISK  
**Task ID:** ML-RISK-001  
**Status:** MERGED & VERIFIED  
**Worktree Branch:** `agent/risk/ML-RISK-001`  
**Primary Deliverables:**
- `src/nexus_scalp/model_generation/position_replay.py`: Comprehensive historical replay runner, position simulator, causal position-state generator, multi-horizon economic labeler, temporal splitter with purge/embargo, dataset manifest attacher, and integrity validator.
- `src/nexus_scalp/model_generation/position_dataset_generator.py`: Delegation wrapper maintaining complete backwards compatibility.
- `src/nexus_scalp/web/model_studio_routes.py`: REST endpoints `POST /api/model-studio/position-dataset/generate` and `POST /api/model-studio/position-dataset/validate`.
- `src/nexus_scalp/cli/model_studio_commands.py`: Typer CLI commands `position-dataset-generate` and `position-dataset-validate`.
- `tests/unit/test_position_replay_pipeline.py`: Comprehensive unit and economic invariant test suite (15 tests, 100% passing).
- `docs/ml-system/tasks/ML-RISK-001.md`: Authoritative specification and master causality contract.

---

## 1. Context & Architecture

The objective of `ML-RISK-001` is building the reproducible, leakage-safe dataset generation pipeline required for training the upcoming Layer-2 Position/Risk Manager (`ML-RISK-002`).

Rather than training or promoting models, this engine executes the real primary `ScalpNet` model across historical market data bars, simulates positions under canonical execution cost geometry (calibrated spread and adverse slippage), records strictly causal position states at every decision point, calculates forward-looking economic outcomes (MFE, MAE, Continuation Value), enforces temporal splitting with purge and embargo windows, guarantees trade group isolation, and outputs an authoritative Parquet dataset accompanied by a cryptographically signed `DatasetManifest`.

### Forensic Audit & Canonical Integration
- **Features:** Causal 50D feature tensor generated via `ScalpFeatureEngine(symbol=...)`.
- **Model Inference:** `ScalpNet` in `torch.inference_mode()`, Softmax probabilities.
- **Costs:** `get_execution_assumptions()` from `src/nexus_scalp/configuration/execution_costs.py`.
- **Manifest:** Standard `DatasetManifest` with `dataset_hash` and `sha256_checksum`.

---

## 2. Invariant & Leakage Guarantees

1. **Causality:** Inputs at bar $T$ use only market information $\le T$. Automated tests prove that mutating future prices $T+1 \dots T+H$ alters economic labels while leaving features at $T$ strictly identical.
2. **Cost Monotonicity:** Net returns and R values are monotonically non-increasing as transaction costs increase.
3. **Trade Group Isolation:** All position-state samples belonging to a single trade reside in exactly one partition. Zero trades cross between Train, Val, or OOS partitions.
4. **Purge & Embargo:** Samples within the forward label horizon $H$ of partition boundaries are purged to prevent label lookahead crossing splits.
5. **No Edge Claim:** Descriptive and validation outputs explicitly state that no claim of trading edge or profitability is made.

---

## 3. Test Coverage & Verification

Executed via critical test suite:
- `tests/unit/test_position_replay_pipeline.py`: 15 passed
- `tests/unit/test_model_studio_pipeline.py`: 14 passed
- `tests/unit/test_model_registry_hot_load.py`: 9 passed
- `tests/unit/test_model_studio.py`: 19 passed
Total: 57 tests passing cleanly.
