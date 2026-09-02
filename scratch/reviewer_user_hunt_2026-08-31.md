# Reviewer deep user-side bug hunt — 2026-08-31 (FC directive)

## NEW BUGS (independently proven, outside BUG-162..174)

### BUG-175 candidate (P1, ML/CLI) — `nexus model-validate` never runs the model
- ROOT CAUSE: `src/nexus_scalp/cli/main.py:2806` — `vf.validate(model_id, "cli", frame, None, labels)` hardcodes `probabilities=None`. `validation.py:254` computes OOS accuracy/macro-F1/balanced-acc ONLY when probs are provided. Calibration falls to `NO_PROBABILITIES` note.
- USER IMPACT: every CLI validation prints fabricated `oos_accuracy=0.0` and is REJECTED with misleading evidence. A genuinely good model (real probs: acc=0.558, macroF1=0.4206, balanced=0.7505 — measured via `_predict_probs` on cand_05d5e65879bc5748) is REJECTED from the CLI path. No candidate can ever become CHALLENGER_ELIGIBLE through this command.
- Cross-schema mismatch is INVISIBLE: 50D model + 70D dataset (ds_d3886c503d6c0901) → same silent REJECTED exit 0. Runtime proof: `RuntimeError: mat1 and mat2 shapes cannot be multiplied (2892x60 and 50x128)` (reproduced directly), never surfaced.
- FIX TARGET: call `_predict_probs`-equivalent (LocalModelRuntime + manifest news_enabled/scaler) in model-validate; surface width mismatch as an explicit schema-mismatch error panel (EXIT_USAGE or EXIT_RUNTIME per contract family).
- FAILS-BEFORE EVIDENCE: p4.out/p6.out/p7.out (REJECTED + oos 0.0 on real model+own dataset); p14/p15 (cross-schema also just REJECTED, no error); forward RuntimeError probe.
- REGRESSION TEST TARGET: validate a trained cand on its own dataset → verdict reflects REAL probabilities (or at minimum oos fields != 0.0 with probs present); cross-schema pair → explicit mismatch error.

### BUG-176 candidate (P1, CLI/UX) — `model-dataset-build --schema` ignored + default raw-bars journey crashes
- ROOT CAUSE (ignored flag): `main.py:2584` declares `--schema` but the value is never passed: `DatasetFactory.build()` (dataset_factory.py:104) has NO schema parameter; `SampleFactory()` defaults `feature_schema_id="scalp_v1"` (sample_factory.py:62). `scalp_v9_bogus` is accepted silently → scalp_v1 dataset built (exit 0).
- ROOT CAUSE (crash): SampleFactory/labeler require PRE-COMPUTED `feat_0..49` + `atr`/`atr_m1` columns. Real exchange export `data/raw/XAUUSD_M1.parquet` (raw OHLCV) → raw rich Traceback `ValueError: DataFrame must contain either 'atr_m1' or 'atr' column.` exit 1.
- FALSE-CONFIDENCE TEST NOTE: e2e_43/45/47 pass because `_make_bars_csv` fabricates feat_0..49+atr — the fixture hides that the documented user path (raw bars) is broken. No 50D feature preprocessor exists anywhere callable (only schema_v2.compute_60d_frame for 60D).
- FIX TARGET: (a) validate --schema against FEATURE_SCHEMAS and thread it into SampleFactory; (b) either add a 50D feature precompute path (mirror compute_60d_frame) or fail fast with an actionable panel listing required input columns; (c) README: document input contract.
- FAILS-BEFORE EVIDENCE: p11.out/p12.out (traceback on real parquet), --schema probe (bogus accepted).

### BUG-177 candidate (P2, Observability) — high-entropy redaction corrupts benign dataset-rejection detail
- `zero-substituted outcome (reconstruction_source=NONE)` → `zero-substituted outcome ([REDACTED_SECRET])` in logs (162 lines on 08-31 alone). Guard in logging.py `_scrub` only spares tokens whose value part is UPPER_SNAKE (`BROKER_DEALS` survives; `NONE` is redacted). Distinction NONE vs authoritative is exactly what the log must show; also injects a false "secret" implication. Same over-redaction class as the known audit; new code path (dataset.py:312).
- FIX TARGET: rephrase detail without `key=VALUE` shape (e.g. "reconstruction source: NONE") or add NONE/authoritative source names to the guard.

## Independently verified fixes (batch BUG-170..173, commit 3814a4d + cade81f)
- BUG-170 O_EXCL pidfile claim (main.py:2224) — race closed.
- BUG-171 `_validate_resume_response` 206/Content-Range semantics (updater.py:863+) — Range-ignoring proxy now restarts cleanly; BUG-122 hash seeding preserved on true resume.
- BUG-172 stop: taskkill rc 128 → "already stopped (stale pidfile)" warning, exit OK; pidfile unlinked (main.py:2505-2545). Probe: live stop on empty env honest.
- BUG-173: `_update_exit_code` FAILED_SAFE → EXIT_RUNTIME (main.py:1064); human panel reads `state` (was `status`→None). Live probe: exit=1, "Rollback not performed (state: FAILED_SAFE)" + actionable hint. test_e2e_18/29 REWRITTEN to honest contracts (not weakened); new net test_user_hunt_bug170_171.py (6 tests, real localhost HTTP servers) — 11/11 green with bug169+bug164 files.
- BUG-169 (c4a1eca): trainer rebind to loaded-bundle effective contract (live_engine.py:968+) + duplicate-tick re-surfaces last real proposal (policy.py:1552) — code-reviewed, 3 tests green.

## Remaining risks / coverage gaps
- BUG-164 regression test still does not pin the "Dataset not found" panel text.
- model-replay returns prediction error INSIDE payload with exit 0 (misleading success for a failed prediction) — fold into BUG-175 family or file P2.
- One transient 1/14 red in tests/release/test_release_hardening.py mid-parallel-edit; re-ran 14/14 green after the owning agent's cycle completed (churn, not a defect).
- ledger numbering: BUG-175/176/177 are CANDIDATE ids — re-grep bugs.md tail before registration (parallel agents race ids).

## Escalation
NEXUS_MAIN: assign final ids, route BUG-175/176 (+replay exit contract) to CODER; BUG-177 to Coder small patch.
