# ML-UI-001 Handoff — Deep Learning & Neural Model Studio (50D / 70D)

## Task Summary
- **Task ID**: `ML-UI-001`
- **Agent**: `AGENT-UI` / `AGENT-FEATURE`
- **Date**: `2026-09-18`
- **Branch**: `agent/feature/ML-UI-STUDIO`
- **Goal**: Implement complete Neural Model Studio & Deep Learning inspection tab across both Legacy UI (`Web/`) and React UI (`frontend/`), CLI commands (`model-quality`, `model-predict`, `model-stress-test`, `model-train-dataset`), 50D/70D model switching, live 70D component assembly (Base 0..49, News 50..59, Liquidity 60..69), layer-by-layer activation norms, feature saliency, uncertainty entropy, numerical validation, OOD scoring, and training dispatch with dataset selection.

---

## 1. Scope of Changes
1. **Backend REST Endpoints (`src/nexus_scalp/web/model_studio_routes.py`)**:
   - `GET /api/model-studio/overview`: Architecture, parameter count, device, weights SHA256, scaler status.
   - `GET /api/model-studio/fetch-70d`: Live 70D component assembly with schema contract verification.
   - `POST /api/model-studio/predict`: 50D/70D forward pass with probabilities, confidence, margin, Shannon entropy, layer norms, and saliency.
   - `GET /api/model-studio/datasets`: Scans filesystem for available parquet/csv training datasets.
   - `POST /api/model-studio/train`: Model training dispatch into engine lifecycle.
   - `GET /api/model-studio/train/progress`: Training progress tracking.
   - `POST /api/model-studio/stress-test`: Adversarial robustness battery.
   - `POST /api/model-studio/benchmark`: 100-pass latency profiling.
2. **Server Mounting (`src/nexus_scalp/web/server.py`)**:
   - Registered `register_model_studio_routes` on the FastAPI application.
3. **CLI Management Surface (`src/nexus_scalp/cli/model_studio_commands.py`)**:
   - `nexus model-quality`
   - `nexus model-predict`
   - `nexus model-stress-test`
   - `nexus model-train-dataset`
4. **CLI Factory (`src/nexus_scalp/cli/app_factory.py`)**:
   - Registered model studio CLI commands on the root Typer application.
5. **Legacy Web UI (`Web/index.html`, `Web/model_studio_ui.js`)**:
   - Added sidebar navigation button: "Neural Studio (50D/70D)".
   - Added `<section id="tab-model-studio">` with full interactive UI, 50D/70D switcher, 70D live fetcher, layer activation inspector, probability bars, entropy meter, saliency drivers, dataset training dispatcher, and stress runner.
6. **React Web UI (`frontend/src/features/model-studio/`)**:
   - Created feature module `index.ts` and `ModelStudioPage.tsx`.
   - Registered in `frontend/src/app/featureRegistry.ts`.
7. **Automated Test Suite (`tests/unit/test_model_studio.py`)**:
   - 19 comprehensive unit and integration tests covering all routes, CLI commands, mathematical invariants, fail-loud boundaries, and web assets.
8. **Critical Suite Manifest (`tests/critical_suite.txt`)**:
   - Added `tests/unit/test_model_studio.py` (verified 200/200 paths exist).

---

## 2. Quality Gate Verification

| Gate | Command | Result |
|:-----|:--------|:-------|
| Gate 1 — Ruff | `ruff check src/nexus_scalp/web/model_studio_routes.py src/nexus_scalp/cli/model_studio_commands.py tests/unit/test_model_studio.py` | `All checks passed!` |
| Gate 2 — Formatting | `ruff format --check src/nexus_scalp/web/model_studio_routes.py src/nexus_scalp/cli/model_studio_commands.py tests/unit/test_model_studio.py` | `All files formatted` |
| Gate 3 — Mypy | `mypy src/nexus_scalp/web/model_studio_routes.py src/nexus_scalp/cli/model_studio_commands.py tests/unit/test_model_studio.py` | `Success: no issues found in 3 source files` |
| Gate 4 — Pytest | `pytest tests/unit/test_model_studio.py -v` | `19 passed in 3.45s` |
| Gate 5 — Manifest | `python scripts/ci/verify_critical_suite_manifest.py` | `CRITICAL_SUITE_MANIFEST_OK: 200 paths all exist` |

---

## 3. Mathematical & Empirical Invariants
- **Probability Sum**: Guaranteed $\sum p_i = 1.0 \pm 10^{-4}$ with non-negative constraints.
- **Shannon Uncertainty**: Calibrated entropy $H = -\sum p_i \log_2(p_i)$ exposed in bits.
- **Top-2 Margin**: $P_{\text{top1}} - P_{\text{top2}}$ exposed for confident signal filtering.
- **Layer Activations**: PyTorch forward hooks capture L2 norm, mean, standard deviation, and sparsity fraction for all linear and norm modules.
- **Feature Saliency**: Analytical backprop gradient $\partial \text{score} / \partial x$ isolates top positive and negative driving features.
- **70D Live Assembly**: Seamlessly merges Base (0..49), News (50..59), and Liquidity (60..69) components under SHA256 schema contract.
