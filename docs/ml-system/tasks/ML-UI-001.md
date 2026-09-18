# ML-UI-001 — End-to-End Model UX & CLI Verification: Training, Predict, Quality & Confidence Calibration

STREAM: STREAM L — OBSERVABILITY, UX & INTEGRATION
PRIORITY: P1
STATUS: IMPLEMENTED
DEPENDENCIES: None
BLOCKS: None
AGENT_ROLE: AGENT-UI
OWNERSHIP_SCOPE: tests/integration/test_model_ui_cli_e2e.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE
TYPE: Integration & UX Validation
RISK: Low
ESTIMATED_SCOPE: 1 integration test suite (~250 LOC)

## OBJECTIVE
Create an automated end-to-end integration test suite verifying the complete model lifecycle across both CLI and Web UI interfaces: managed training dispatch, real-time progress polling, single-tick prediction (`/api/debug/model-test`), output quality metrics, and honest confidence score visualization without user deception.

## WHY_IT_EXISTS
Operators interact with models through the CLI (`nse model-train-local`, `nse model-setup`) and Web Dashboard (`Web/first_setup.html`, `Web/app.js`). If UI progress polling freezes, if `/api/debug/model-test` returns unnormalized probabilities, or if the UI displays high confidence when the model output is actually ambiguous or uncalibrated, operator trust is destroyed. We need rigorous end-to-end verification that the model's training status, predictions, and confidence levels are honestly and accurately reported.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/web/debug_research_routes.py` (Lines: `172-285`)
  - **Symbol:** `@app.post("/api/debug/model-test")`
  - **Behavior:** Accepts feature vector payload; executes forward pass through PyTorch model; returns logits, probabilities, action, confidence, and inference latency
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/cli/provision_commands.py` (Lines: `115-180`)
  - **Symbol:** `@app.command("model-train-local"), @app.command("train-once")`
  - **Behavior:** Dispatches isolated local candidate training subprocess with stage callbacks and progress rendering
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/web/provisioning_routes.py` (Lines: `45-120`)
  - **Symbol:** `POST /api/provisioning/train/start, GET /api/provisioning/train/progress`
  - **Behavior:** Dispatches background worker and exposes non-blocking training progress (loss, epoch, stage, eta_seconds)
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `Web/app.js` (Lines: `287-330`)
  - **Symbol:** `Model Output (latest inference), snap.ai_confidence`
  - **Behavior:** Renders `Decision` (BUY/SELL/NO_TRADE) and `Confidence` percentage; shows explicit halt/unavailable banner when inference fails
  - **Classification:** `PRODUCTION UI`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/signals/policy.py` (Lines: `54-75`)
  - **Symbol:** `SignalPolicy.__init__`
  - **Behavior:** Enforces `confidence_threshold` (default 0.35 gate) below which directional signals are suppressed to NO_TRADE
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- The web server exposes `/api/debug/model-test` for interactive prediction diagnostics.
- Local training can be initiated via CLI (`model-train-local`) and Web API (`/api/provisioning/train/start`).
- The frontend UI displays AI confidence and suppresses low-confidence proposals via `SignalPolicy`.
- PR #247 established honest progress reporting (real loss, epoch, ETA).

## UNKNOWNs
- Behavior of progress polling if training subprocess crashes mid-epoch with SIGKILL.

## PRECONDITIONS
- Starlette / FastAPI test client available.
- Typer `CliRunner` available for testing CLI entrypoints.

## SCOPE
1. Author `tests/integration/test_model_ui_cli_e2e.py` to test:
   - CLI execution of `model-train-env` and help text diagnostics.
   - Web API dispatch of local training (`POST /api/provisioning/train/start`) and query of progress (`GET /api/provisioning/train/progress`).
   - Web API prediction test (`POST /api/debug/model-test`) with synthetic 50D feature vector.
   - Verification that probabilities sum to 1.0 $\pm 10^{-5}$ and confidence matches the max directional class probability.
   - Verification that predictions below `confidence_threshold` (0.35) are honestly flagged as `NO_TRADE` or low-confidence.
   - Verification that when model inference fails, the API responds with fail-safe error payloads rather than fabricated predictions.

## NON_GOALS
- Do not redesign frontend CSS/HTML layouts.
- Do not execute a multi-hour real training run during automated unit/integration test runs.

## SOURCE_AREAS
- `src/nexus_scalp/web/debug_research_routes.py`
- `src/nexus_scalp/web/provisioning_routes.py`
- `src/nexus_scalp/cli/provision_commands.py`
- `src/nexus_scalp/signals/policy.py`
- `Web/app.js`

## FILES_LIKELY_TO_CHANGE
- `tests/integration/test_model_ui_cli_e2e.py`

## INVESTIGATION_PLAN
1. Inspect the payload contract of `POST /api/debug/model-test` in `debug_research_routes.py`.
2. Inspect the mock response structure of `GET /api/provisioning/train/progress`.
3. Check how `SignalPolicy` treats confidence below the 0.35 threshold.

## IMPLEMENTATION_PLAN
1. Create `tests/integration/test_model_ui_cli_e2e.py`.
2. Implement `test_cli_model_commands_help_and_env()`:
   - Use `typer.testing.CliRunner` on `src.nexus_scalp.cli.main.app`.
   - Invoke `model-train-env` and `model-setup --help`; assert exit code 0 and non-empty output.
3. Implement `test_web_api_model_debug_prediction()`:
   - Use `fastapi.testclient.TestClient` on `create_app()`.
   - Send `POST /api/debug/model-test` with a synthetic 50D vector.
   - Assert response contains `status == "ok"` (or valid mock response), `probabilities`, and `confidence`.
   - Verify probabilities are non-negative and sum to 1.0.
4. Implement `test_honest_confidence_and_threshold_gating()`:
   - Test that an ambiguous prediction (probabilities ~ [0.34, 0.33, 0.33]) is rejected by `SignalPolicy` (does not emit BUY or SELL).
   - Test that an authoritative prediction (BUY prob >= 0.70) cleanly passes the confidence gate.
5. Implement `test_provisioning_train_progress_lifecycle()`:
   - Test start and poll cycle for `/api/provisioning/train/progress`.
   - Assert schema includes `status`, `stage`, `loss`, `epoch`, `eta_seconds`.

## TEST_PLAN
- `pytest tests/integration/test_model_ui_cli_e2e.py -v`

## BENCHMARK_PLAN
- `/api/debug/model-test` latency must remain under 50ms for a single 50D inference vector in test environment.

## EVIDENCE_REQUIRED
- `tests/integration/test_model_ui_cli_e2e.py`
- Pytest test execution output demonstrating clean green runs across CLI and Web routes.

## ACCEPTANCE_CRITERIA
1. `tests/integration/test_model_ui_cli_e2e.py` passes 100% cleanly without warnings or errors.
2. Model prediction debug route returns mathematically valid probability distributions ($P \ge 0, \sum P = 1$).
3. Low-confidence outputs (< 0.35) are proven to be suppressed from trade proposal generation.
4. CLI commands exit cleanly with code 0.

## ABORT_CONDITIONS
- If the debug route emits fabricated confidence scores or ignores the underlying model logits, STOP and report critical integrity defect.

## HUMAN_STOP_CONDITIONS
- None. This is an integration test and quality verification task.

## EXPECTED_ARTIFACTS
- `docs/ml-system/tasks/ML-UI-001.md`
- `tests/integration/test_model_ui_cli_e2e.py`

## SHARED_FILE_RISK
- Low. Self-contained integration test file in `tests/integration/`.

## COMMIT_BRANCH_EXPECTATIONS
- Branch: `agent/ui/ML-UI-001`
- Commit message: `AGENT-UI: add end-to-end model UI and CLI integration test suite (ML-UI-001)`

## DEFINITION_OF_DONE
- Automated tests verify CLI and Web UI endpoints for model training progress, prediction inspection, and honest confidence gating.
