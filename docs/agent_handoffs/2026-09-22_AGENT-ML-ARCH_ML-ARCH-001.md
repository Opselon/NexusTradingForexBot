# 2026-09-22 — AGENT-ML-ARCH — ML-ARCH-001 (evidence run)

**Task:** ML-ARCH-001 — ScalpNet Dual-Path Tensor Contract & 3-Class Head Sunset
**Role:** AGENT-ML-ARCH
**Status:** EVIDENCE-COMPLETE — PR opened. **Not DONE.** The task is
`HUMAN_DECISION_REQUIRED: YES`; this cycle delivered the audit and the decision
vehicle the operator needs to unblock it, and deliberately did not execute a sunset.

---

## 1. What this cycle did

ML-ARCH-001 is the P0 gate on three downstream tasks (ML-ARCH-002, ML-ARCH-003,
ML-EXP-002). Its INVESTIGATION_PLAN demanded an artifact audit to answer one
UNKNOWN:

> Whether any production client still depends on 4-wide checkpoint loading.

That audit was never done — the task sat at READY with the UNKNOWN open. This
cycle **answered it with a reproducible probe instead of prose**, and built the
decision record its AC-1 requires.

### Deliverables

| Artifact | Purpose |
|---|---|
| `scripts/audit/audit_legacy4_surface.py` | Torch-free static audit probe. Classifies every `num_classes` literal by subsystem, counts the Option-A blast radius, and checks for committed 4-wide artifacts via `git ls-tree` (untracked locals never inflate the surface). |
| `tests/unit/test_audit_legacy4_surface.py` | 12 tests: probe-correctness on synthetic trees + the measured HEAD surface pinned. Critical-suite registered. |
| `agents/decisions/DEC-0010-scalpnet-3class-head-sunset.md` | The decision vehicle for AC-1: Options A/B/C, measured blast radius, and a recommendation. |

No production code was changed. `src/` is untouched.

---

## 2. The answer, and the evidence

**No in-repo dependency on 4-wide checkpoint loading exists.**

Probe output, measured at `eb73440a`:

```
src/ num_classes literals ......... 11
src/ legacy-4 literals ............ 2
    SMOKE                1
    WEB                  1
    src/nexus_scalp/smoke/runner.py:731  in _chain()  [SMOKE]
    src/nexus_scalp/web/model_governance_routes.py:515  in attach_shadow70()  [WEB]
tests/ legacy-4 occurrences ....... 66 across 29 files
committed .pt artifacts ........... 0
--------------------------------------------------------------------
VERDICT: LEGACY-4-DEAD-WEIGHT
```

Corroborated by hand:

- `git ls-tree -r HEAD --name-only` → **0 `.pt` paths**. No checkpoint is
  committed to git, so clean-checkout training is impossible by design — no
  production client can depend on a 4-wide bundle held in this repo.
- `find artifacts -name '*.pt'` → 0; `~/.nexusscalpengine/models/` is empty.
- The serving head resolver (`live_engine.py:2448-2465`) returns the
  meta-declared head **only** when it is `3` or `4`, else falls back to
  `TRAINED_CLASS_COUNT` (3). With a 3-declaring meta — the only kind that can be
  served today — `mask_wait_logit` is a **no-op** (`model_class_contract.py:114`).
  The per-tick mask is dead work for every servable bundle.

### Why the recommendation is Option B

DEC-0010 recommends **strict sunset + legacy adapter**, not removal:

1. Zero 4-wide artifacts exist, so removing the 4th logit buys **no real safety**.
2. Option A **destroys proven negative tests**: 66 `num_classes=4` occurrences
   across 29 test files, most deliberate probes (e.g.
   `test_bug243_bundle_class_head_mint.py`,
   `test_promotion_rejects_degenerate_model.py`) that *prove* gates reject
   incoherent bundles. Deleting the constructor deletes that coverage.
3. The official-model publication path is not yet live; retaining legacy-4 read
   compatibility keeps a future operator-supplied 4-wide bundle loadable instead
   of hard-failing at a client site.
4. The only production-path saving from Option A is one no-op `mask_wait_logit`
   per tick — negligible inside the ~60ms forward it sits in.

---

## 3. Verification (real, not claimed)

| Gate | Result |
|---|---|
| New tests | **12/12 pass** (`pytest tests/unit/test_audit_legacy4_surface.py`, Python 3.11.16, 1.16s) |
| `ruff check` | All checks passed (both files) |
| `ruff format --check` | 2 files already formatted |
| Critical-suite manifest | `CRITICAL_SUITE_MANIFEST_OK: 222 paths` after registering the new test |
| Probe on committed tree | rc=0, verdict `LEGACY-4-DEAD-WEIGHT` |

### One bug in my own probe, caught by running it

The first run crashed with `IndexError`: I indexed `lines[m.start()]` using a
**character offset** into the whole file text as if it were a **line index**.
Fixed by computing the line number first, then indexing. The probe now handles
comment-only lines correctly (verified by `test_probe_skips_comment_only_lines`,
which fails against the old logic).

### Environment honesty

`.venv-linux` on this host is an **empty stub** (no `pytest`, no `torch`, empty
`site-packages`) — the documented slim venv is not present. I installed pytest
and the small pure deps (structlog/pydantic/PyYAML/numpy) into an isolated
`/tmp/pylibs` and ran with `PYTHONPATH=src:.:/tmp/pylibs`. The probe and its
tests are **deliberately torch-free**, so they run in the static CI lane and on
a slim venv without pulling the torch/polars chain — the same design decision as
ML-CI-002's drift gate. The 12 tests pass under this arrangement.

The pre-existing red flagged last cycle (`tests/unit/test_check_local_gate.py`)
was re-checked: **it is not in `tests/critical_suite.txt`** (grep rc=1), so it
does not gate pushes. It remains out of scope.

---

## 4. Board / ledger corruption repaired

While reading the task board I found and fixed real corruption — not cosmetic:

- **`docs/ml-system/06_TASK_LEDGER.md`** carried a stale duplicate STREAM E/F
  block: 6 extra rows interleaved *inside* the master table, each contradicting
  the task file it points at (ML-TRAIN-001/002/003 and ML-EXP-001 each appeared
  twice with opposite statuses). Removed the duplicates; statuses now match the
  task files.
- **`TASK_BOARD.md`** duplicated all three STREAM E rows with contradictory
  statuses. Removed the stale block.
- **ML-BT-001** was marked BLOCKED on the board and ledger but its task file
  says DONE and `51a3dc86 feat(backtest): trading quality metric suite ...`
  (PR #316) shipped `src/nexus_scalp/research/trading_metrics.py`. Corrected.
- **ML-CI-002** was still BLOCKED in both docs despite PR #346. Corrected.

A swarm that trusts a corrupted board would re-do DONE work or skip unblocked
tasks — this is the exact "metadata-conflicted" failure mode the contract warns
about, left uncorrected in the backlog itself.

---

## 5. Swarm state after this cycle

26/30 tasks DONE or evidence-complete. The gate is unchanged: the 5 remaining
human-decision tasks (ML-ARCH-001, ML-GOV-002, ML-EXP-002, ML-FEAT-003,
ML-OBS-002) need operator sign-off; ML-CI-001's scope is a workflow file the
swarm cannot touch.

**But the P0 blocker is now cheaper to clear.** ML-ARCH-001 no longer needs an
investigation before the operator can decide — the evidence is in DEC-0010 and
the probe is reproducible. A one-word answer (A, B, or C) unblocks ML-ARCH-002,
ML-ARCH-003 and ML-EXP-002.

### Recommended next cycle

If the operator picks **Option B**: implement the DEC-0010 §3.2 surface —
`DeprecationWarning` on `num_classes=4` construction in `scalp_net.py` and on the
`allow_legacy_4=True` load path in `integrity.py`, with tests asserting the
warning fires and masking still neutralizes WAIT. No serving-path change.

If no answer: the next-highest-value autonomous work is **ML-CI-001's executable
subset** — its OWNERSHIP_SCOPE is the forbidden workflow file, but its
`tests/integration/test_walk_forward_pipeline_real.py` deliverable (5 folds,
3 epochs, <180s) is swarm-writable and is the thing that actually closes the
CI-vs-real-training gap.

---

## 6. References

- Probe: `scripts/audit/audit_legacy4_surface.py`
- Tests: `tests/unit/test_audit_legacy4_surface.py`
- Decision: `agents/decisions/DEC-0010-scalpnet-3class-head-sunset.md`
- Contract: `src/nexus_scalp/model_lifecycle/model_class_contract.py:50-172`
- Serving head resolution: `src/nexus_scalp/application/live_engine.py:2448-2465`
- Tick-path mask: `src/nexus_scalp/application/live/inference.py:306-315`
