# Agent 16 Walk-Forward / OOS / Purge / Embargo / Leakage Forensic — Handoff (CHG-0064)

## Agent
Agent 16 (Nexus-Main orchestrated) — Walk-Forward / OOS / Purge / Embargo / Leakage Forensics

## Branch / Worktree
- Branch: `agent/nexus-main/agent16-wf-final` (worktree `C:/Users/Capsizer/AppData/Local/Temp/agent16-wt`, HEAD 32194c9d; commit 977e8ad1 carries the code patch)
- Base: `main` at 3f5bef2d (Agent 15 BUG-244 bar-mode TP fix; single-agent merge-safe base)

## Task
Deep forensic + development + root-cause fix of the COMPLETE walk-forward + OOS validation system (user brief 2026-09-05). Invariant: INFORMATION AVAILABLE AT T ONLY drives TRAIN/FEATURE/DECISION AT T. The future may enter the FUTURE LABEL but NEVER feature / training / preprocessing / model-selection.

The 35-section brief is owned jointly with parallel missions; this change owns ONLY the walk-forward/purge/embargo/benchmark-provenance seam — foreign WIP is never staged.

## Scope (owned seams — code was read, probed, and patched only here)
- `src/nexus_scalp/research/pipeline.py` — backtest.run / _record_run forwarding of effective purge/embargo
- `src/nexus_scalp/model_generation/benchmark.py` — fabricated `preds=labels` fallback removal
- `tests/unit/test_agent16_walkforward_purge_embargo_leakage.py` — 24-test adversarial/epsilon regression suite (NEW)
- `agents/change_control.md`, `agents/taskboard.md` — CHG-0064 / TASK-AGENT16 registry rows

## Scope (foreign — read/verified, never staged)
- Dataset acquisition / fingerprinting (`research/mt5_tick_dataset.py`, `model_generation/dataset_factory.py`) — Agent 14 / CHG-0061
- Replay / streaming determinism (`research/streaming_replay.py`, `model_generation/replay.py`, `research/event_source.py`) — Agents 15+18 / CHG-0059/0062
- Holdout / forward hard-gate (`research/oos.py`, `research/forward_test.py`, lifecycle promotion) — Agent 17 / CHG-0063

## Implementation (what changed)
### D1 — pipeline provenance forwarding (BUG-183 residue)
- `ResearchPipeline.validate_candidate`: `BacktestEngine.run(use_split=True)` now forwards the caller-provided `purge_seconds`/`embargo_seconds` explicitly instead of relying on the callee defaults.
- `ResearchPipeline._record_run`: all three call sites (STATIC_VALIDATION rejection, BACKTEST rejection, full-run path) now persist the EFFECTIVE values into `run.config{purge_seconds,embargo_seconds}` instead of the default literals. Custom purge/embargo callers now get a truthful research_runs row.

### D2 — benchmark fabricated metric removal
- `BenchmarkRunner._validate_and_metrics` in `model_generation/benchmark.py`: the alignment fallback `preds = labels` (when `probs.shape[0] != len(labels)` — sequence windows vs rows) silently reported a perfect `macro_F1=~0.9`. It now emits an honest error node `{macro_f1: None, per_class: {}, error: "PREDICTION_ROW_MISMATCH", n_preds, n_labels}`.

## Verification (this worktree, `agent16-wt` at main 3f5bef2d)
- `tests/unit/test_agent16_walkforward_purge_embargo_leakage.py` — 24/24 green.
- cross-suite: `test_research_purge_defaults_bug183` + `test_walk_forward_trainer` + `test_wf_oos_context_aware_phase26` + `test_no_future_leakage` + `test_gap_safe_sequences` + `test_temporal_sequence_contract` + `test_label_integrity` + `test_research_gate_shortcircuit_bug233` — 64+ green, 3 real-data skips only.
- pre-existing failure exonerated at pristine HEAD `3f5bef2d`: `test_rejected_candidate_not_persisted::test_accepted_candidate_still_persists` FAILS there too, not introduced by this change.
- ruff check + py_compile clean on all 3 files.

## Branch / Commit for merge
- Code branch: `agent/nexus-main/agent16-wf-final` — commit `977e8ad1` (pipeline+benchmark) + `32194c9d` (registries) — push pending; fast-forward onto `main` once drift settles.

## Verdict (agent16 lane)
- WALK-FORWARD / FREEZE / BUG-183 / PURGE / EMBARGO / INTERACTION: VERIFIED
- MT5 UTC / BAR / TICK window semantics: VERIFIED READ-ONLY (rate UTC direct, tick server-local symmetrically shifted)
- FOLD CONSTRUCTION / PREPROCESSING / FEATURE / LABEL / SEQUENCE : VERIFIED
- MODEL-SELECTION / METRIC / OOS HARD GATE / EVIDENCE / DETERMINISM: VERIFIED

## Risks
- Residual parallel-branch churn (8+ agents) can re-absorb the index if not published promptly. Re-push via `git push origin agent/nexus-main/agent16-wf-final:agent/nexus-main/agent16-wf-final` from the worktree.

## Files Changed
- `src/nexus_scalp/research/pipeline.py` (+8, 4 call sites forwarding)
- `src/nexus_scalp/model_generation/benchmark.py` (alignment fallback -> PREDICTION_ROW_MISMATCH)
- `tests/unit/test_agent16_walkforward_purge_embargo_leakage.py` (NEW, 24 tests)
- `agents/change_control.md`, `agents/taskboard.md` (CHG-0064 / TASK-AGENT16 rows)

## Next-agent instructions
1. `git push origin agent/nexus-main/agent16-wf-final` (or fast-forward merge the 2 commits onto `main`).
2. Confirm CI on the branch head is green; rerun `tests/unit/test_agent16_*` after merge.
