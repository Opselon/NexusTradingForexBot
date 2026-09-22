# ML-EXP-001 — Immutable Experiment Registry & Artifact Manifest Schema

**Agent:** AGENT-ML-EXP (NSE Autonomous ML Specialist Swarm, cycle 2026-09-21)
**Task:** ML-EXP-001 (Stream F — Experimentation, P1)
**Status:** DONE — PR #335
**Worktree:** `/tmp/wt-ml-exp-ml-exp-001` (branch `agent/ml-exp/ml-exp-001`, off `origin/main` @ `344aa018`)

## Headline correction

State recorded ML-EXP-001 as complete, but its only two required artifacts —
`src/nexus_scalp/model_lab/experiment_registry.py` and
`tests/unit/test_experiment_registry.py` — existed **nowhere**: not in the
shared tree, not in any `/tmp` worktree, not on `origin/main`
(verified by `git ls-tree -r origin/main --name-only` and a filesystem-wide
`find`). The task was also still `BLOCKED` in its own task file and in
`06_TASK_LEDGER.md`. It was implemented for real this cycle. Landing it also
unblocks **ML-EXP-003** (grid search) and **ML-VAL-002** (ECE calibration),
both of which list ML-EXP-001 as a dependency.

## What was delivered

`src/nexus_scalp/model_lab/experiment_registry.py` — one schema, two
write-once stores:

- `ExperimentRecord` (frozen pydantic, `extra="forbid"`) carrying the seven
  mandatory reproduction fields: `experiment_id`, `git_sha`,
  `dataset_hash`, `model_config`, `seed`, `metrics`, `artifact_hash`, plus
  `params`, `dataset_id`, dirty-tree provenance and timestamps.
- `ExperimentRegistry`: SQLite store under `artifacts/experiments/` with
  `journal_mode=WAL` + `busy_timeout=30000` (the task's stated UNKNOWN —
  concurrent reader/writer agents), write-per-call connections guarded by a
  process-level `RLock`.
- Immutability as a contract: `register()` is idempotent on an identical
  record and refuses redefinition of identity; `record_result()` /
  `record_failure()` are write-once; `write_manifest()` never rewrites an
  existing manifest. Tamper detection via `verify_manifest()`, which
  recomputes a manifest hash that **excludes itself** (a self-referential
  hash cannot be computed) and reports `MANIFEST_TAMPERED`.
- `capture_git_revision()` never raises: a dirty tree is recorded as
  `git_dirty=True` + a working-tree diff hash (the task's ABORT_CONDITIONS),
  and outside a repo it returns `("UNRESOLVED", True, "UNRESOLVED")` instead
  of crashing.
- Queries: `get_best_experiment(metric)`, `top_n(metric, n)`,
  `list_experiments(status/dataset_hash/limit)`, `reproduction_bundle(id)`.
  **Metric direction is required** — sorting a loss descending would
  silently surface the worst run — so it is inferred from the metric name
  (`val_loss`/`brier`/`ece` descend, `f1`/`sharpe`/`accuracy` ascend) and
  raises `ValueError` for an unrecognised name unless `lower_is_better` is
  passed explicitly.
- `validate_experiment_id()` (regex + `..` guard) because the registry
  writes files under `<root>/<experiment_id>/`.

## Two real bugs found by the tests

1. **SQLite `json_extract` path semantics.** The first implementation built
   `"$['val_loss']"`. On SQLite 3.53 the bracket form resolves **array
   indices**, not object keys — it looks up a key literally named
   `[val_loss]` and returns NULL, silently emptying every `get_best_*` /
   `top_n` query. Diagnosed with a probe against the real engine; fixed as
   `json_path()` (dot form `$.val_loss`; double-quoted object-key form for
   names containing `.`). Pinned by two tests, one of which resolves the
   built path through `json_extract` on the real SQLite rather than only
   asserting the string.
2. **Transaction rollback double-fire.** The write paths used
   `BEGIN IMMEDIATE` ... `try / except: ROLLBACK; raise`. A validation error
   raised *after* a successful `ROLLBACK` (e.g. the write-once guard)
   propagated out through `__exit__` of `_connect`, where the stale
   rollback fired again: `sqlite3.OperationalError: cannot rollback - no
   transaction is active` — masking the real immutability error. Replaced
   with a `_transaction()` context manager that commits on clean exit only
   and rolls back (errors suppressed) on exception propagation.

A third potential defect was avoided by the import probe: the test's
`from nexus_scalp.model_generation.artifact_store import ...` pulled in
`model_generation/__init__.py` → `architectures.py` → `torch`, which is
absent from the slim Linux verification venv (CPU-only host). The id guard
is therefore local to the registry module (documented as mirroring
`ArtifactStore.validate_artifact_id`), keeping the registry importable
without torch — it is experimentation infrastructure, not a model module.

## Verification evidence

| Gate | Result |
| --- | --- |
| New tests | **39/39 pass** (`tests/unit/test_experiment_registry.py`) |
| ruff check | clean (both files) |
| ruff format --check | clean (both files) |
| mypy | clean (both files) |
| critical-suite manifest | 215 → 216 paths, `CRITICAL_SUITE_MANIFEST_OK` |
| BENCHMARK_PLAN | 1000 registers + 333 finalizes; top-10 query **0.28 ms** (budget 50 ms) |
| Concurrency | 4 workers × 40 experiments = 160 rows, zero lost/clobbered ids |
| Import purity | registry imports without torch/polars (probed) |

Test coverage map: dual write (SQLite row + manifest), write-once
immutability on all three write paths, manifest tamper detection,
reproduction bundles (incl. dirty-tree non-reproducibility flag), required
metric-direction inference, 1k-record benchmark, the DIRTY abort condition,
concurrent workers, and experiment-id path-safety.

Baseline-compared `tests/unit/test_agent3_champion_registry_sync.py`
(3 failures) against an untouched `origin/main` worktree — **identical
pre-existing failures**, unrelated to this change. All other model_lab /
training suites require torch, which is intentionally absent from the slim
venv; CI on the windows/macos matrix exercises them.

## Non-goals honored

- No external SaaS experiment tracking (SQLite + JSON only).
- No change to `model_lab/registry.py` (the existing lab registry),
  `model_generation/artifact_store.py`, or any production path.
- `write_manifest` is a caller-driven step, not an implicit side effect of
  registration, so a producer that only wants the DB row is not forced to
  write files.

## Next

ML-EXP-001 was the gate for **ML-EXP-003** (Bounded Hyperparameter Grid
Search Runner, P3, AGENT-ML-EXP — deps ML-EXP-001 + ML-TRAIN-003, both now
clear) and **ML-VAL-002** (Probability Calibration & ECE, P2 — dep
ML-EXP-001 in the ledger, though its task file also names ML-EXP-002 which
is HUMAN-DECISION-REQUIRED). ML-EXP-003's `ExperimentRegistry` consumer is
the natural next step: `GridSearchSpec` → trials registered as
`ExperimentRecord`s → `record_result` per trial → leaderboard from
`top_n(metric)`.
