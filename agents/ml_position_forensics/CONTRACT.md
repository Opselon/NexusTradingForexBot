# ML-POSITION-FORENSICS — Implementation Contract

Lane: `agent/ml-position-forensics` (worktree `C:/c/tmp/mlpos-wt`, base `origin/main` @ `bbbec186`).

## Current architecture (discovered, not assumed)

NSE already has the Layer-2 Position Decision Adviser subsystem:
`src/nexus_scalp/position_adviser/` (features/models/service/trainer/integration/paths),
`src/nexus_scalp/model_generation/position_replay.py` (dataset + label generator,
`CAUSALITY_CONTRACT`, purge/embargo split logic, `PositionDatasetValidator`),
`src/nexus_scalp/web/position_adviser_routes.py` (API), frontend
`frontend/src/features/position-adviser/` + legacy `Web/position_adviser_ui.js`.

The ML component ALREADY only advises, and the safety layer already governs: the
single channel to execution is `PositionAdvisory.hold_score_adjustment`, which is
`<= 0` by construction, clamped to `[0, -max_hold_score_penalty]`, applied AFTER the
existing scoring + giveback safety override, and the adviser can never open, size,
modify an order, or lift a score (see `models.py` and `integration.py` invariants).
That is the correct architecture — it is preserved, not replaced.

## Forensic findings (F0..F6) — evidence, all in the worktree

- **F0 LIVE-STATE DRIFT (the only true leak): the adviser never observes live
  state.** In `service.evaluate()` the state is handed a `position_state` dict
  built ONCE at `_evaluate_hold_score` time and then **reused verbatim** for every
  subsequent evaluation while the caller is not re-invoked. Worse: even the single
  caller (`order_manager._evaluate_hold_score`) passes `holding_duration_sec=0.0`
  and `model_probability == model_confidence` (both the entry confidence, both the
  same dict view). So `position_age_bars` is **always 0** live and the two
  confidence features are literally identical — the live feature vector does not
  match the training distribution. The trained model learns `position_age_bars` as
  a real signal (it is a top discriminator in the synthetic trainer test);
  feeding it a constant 0 is a train/serve distribution shift, i.e. the model is
  served a state that never existed at any decision timestamp.
- **F1 STALE/IMMOBILE STATE: no snapshot id, no freshness gate.** The service has
  no concept of a position snapshot version. `_last_eval_at` throttles only by
  wall clock; nothing rejects a prediction built from a position that has since
  been modified/closed. `forget(ticket)` exists in the API contract
  (`service.forget`) but **is never called anywhere in the codebase** — the
  throttle map grows for the process lifetime (unbounded memory growth over long
  runtimes).
- **F2 PURGE IS ASYMMETRIC (train/serve contamination).** `position_replay` purges
  samples whose lookahead crosses the train|val and val|oos boundary, but the
  *tail boundary* (the end of the oos partition) is never embargoed, and the
  generator's `purge` test only asserts `>= 0`. The validator reports
  `causality_violations=0` as a hardcoded constant — the field is decorative.
- **F3 LIVE FEATURE VECTOR IS POSITION-TIME-IGNORANT.** `holding_duration_sec=0.0`
  is passed explicitly; `build_position_state_for_adviser` then derives
  `position_age_bars = max(0, int(holding_duration_sec/60.0)) = 0`. Fix at the
  caller: pass the real duration, and derive the age in *bars* from the live tick
  cadence, not from a seconds/60 conversion.
- **F4 ENTRY MODEL CONFIDENCE IS USED FOR BOTH `model_probability` AND
  `model_confidence`.** In the generator these are the same value too
  (`model_probability=model_prob, model_confidence=model_prob`), so the
  *training* data is consistent — but the *live* path uses the entry confidence
  for both, which is an entry-time value, not a decision-time one. `signal_age`
  is also never written (`entry_signal_age_sec` has no writer in the repo) so the
  feature is structurally `0.0` live.
- **F5 UNBOUNDED PREDICTION HISTORY.** `_ADVISORY_HISTORY` is a bounded ring
  (`_MAX_HISTORY=200`) but `service._last_eval_at` is not; see F1.
- **F6 CLI/DECISION-TRACE GAPS.** No CLI command exposes adviser status, models,
  datasets or per-position evaluation; the API exposes `/status`, `/models`,
  `/advisories` and (new) `/position/:ticket/evaluate` only via HTTP. Decision
  history (§22) is the advisory dict alone — no feature-snapshot id /
  prediction id / policy decision id linkage.

## Scope of work — what this lane changes

Files this lane owns (new or edited):
- `src/nexus_scalp/position_adviser/features.py` — live vector causality helpers.
- `src/nexus_scalp/position_adviser/service.py` — snapshot contract, `forget`,
  drift/staleness rejection, memory bounds.
- `src/nexus_scalp/position_adviser/integration.py` — real holding duration,
  decision-time model state, decision provenance ids.
- `src/nexus_scalp/execution/order_manager.py` — only the `_evaluate_hold_score`
  adviser call site and the ticket teardown site (`forget()` wiring, F1).
- `src/nexus_scalp/model_generation/position_replay.py` — honest purge/embargo
  (F2), real validator checks replacing the hardcoded constant, and a real
  `model_confidence` distinct from probability.
- `src/nexus_scalp/cli/position_adviser_commands.py` (NEW) + registration in
  `cli/app_factory.py` — CLI audit surface.
- `tests/unit/test_ml_position_forensics.py` (NEW) — the forensic test matrix
  for DATA / MODEL / POSITION / RISK / RUNTIME / PARITY as defined in the
  mission §36, scoped to the findings above.

Explicitly NOT in scope (other lanes own): the model registry/champion pipeline
(`model_lifecycle/`, `governance/`), shadow runtime, the 70D feature contract,
`model_studio_routes.py`, `frontend/`. The adviser already cannot widen SL, move
TP, or increase exposure — no new authority is created anywhere in this lane.

## Success gates (per mission §42)

1. No material look-ahead leakage in Position ML features (F0 fixed: the live
   vector is provably decision-time-only and is re-derived per evaluation).
2. Stale position snapshots are rejected (snapshot id + freshness).
3. Duplicate decisions are controlled (throttle + snapshot id).
4. Long-running inference/training does not leak memory (bounded throttle map,
   `forget()` on close).
5. Replay can reproduce live semantics — the live feature builder and the
   generator write the same schema with the same semantics for the fixed fields.
6. Critical tests pass; `ruff format --check`, `ruff check`, `mypy src` green for
   the touched files; CI green.

## Verification commands (from the worktree)

```bash
PYTHONPATH=src ./.venv/Scripts/python.exe -m pytest tests/unit/test_ml_position_forensics.py \
  tests/unit/test_position_adviser.py tests/unit/test_position_replay_pipeline.py \
  -p no:cacheprovider > pytest_out.txt 2>&1
ruff format --check . && ruff check src/nexus_scalp/position_adviser src/nexus_scalp/cli \
  src/nexus_scalp/model_generation/position_replay.py src/nexus_scalp/execution/order_manager.py
./.venv/Scripts/python.exe -m mypy src/nexus_scalp/position_adviser src/nexus_scalp/cli/position_adviser_commands.py
```

Note the `.venv` belongs to the MAIN checkout — run with `PYTHONPATH=src` from the
worktree so the suite exercises the worktree's tree, not the editable install.
