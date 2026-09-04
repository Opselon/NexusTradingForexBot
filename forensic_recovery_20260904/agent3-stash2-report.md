# Stash@{2} Forensic Deep Audit — 483fc0c3 hold-303-docs-site-three_model

**Stash:** `stash@{2}` `483fc0c386eba6167da96452e3434cd0666ec755` — `On hermes-subagent/subagent-sa-1-6eccae4e: hold-303-docs-site-three_model for main pickup`
**Base:** `16e86d70` `Nexus-Docs: flagship pro site — glass hero + live chart + bento + modes + terminal + pipeline + CmdK …`
**Main at audit:** `a5e2ccc4` (via `694ee2b2` docs/forensics). Do NOT pop/drop stash — read-only.

---

## 1) Tracked diff — `stash@{2}^1..stash@{2} --stat`

```
 docs/project/status.md                          |  4 ++--
 scripts/docs/build_site.py                      | 12 +++++++++++-
 src/nexus_scalp/model_generation/three_model.py |  4 +++-
 3 files changed, 16 insertions(+), 4 deletions(-)
```

Matches expected triple: `status.md` version bump, `build_site.py` docs-enhance wiring, `three_model.py` 70d fast path. No other tracked files.

Index diff (`stash@{2}^1..stash@{2}^2 --stat`) is identical — all three were staged at stash time.

---

## 2) Exact `three_model.py` patch + semantics

### Patch (parent `d4779ccb` -> stash `09ba4df0`)

```diff
@@ -132,7 +132,9 @@ def build_feature_frame(
             "liquidity_status",
         ] + [f"feat_{i}" for i in range(50)]
         return full.select(cols)
-    return compute_70d_frame(bars_frame, min_bars=min_bars, news_frame=news_frame)
+    from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast
+
+    return compute_70d_frame_fast(bars_frame, news_frame=news_frame)
```

Top-level import left untouched in the stash literal:

```python
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame  # line 39 — survives in stash, becomes dead code
```

### Semantics: `compute_70d_frame` -> `compute_70d_frame_fast` for 70d variants

`build_feature_frame` routes `variant == "50d_main"` through the fast path already (selects `feat_0..49` from the full 70d frame), and the else branch — `70d_news` / `70d_liquidity` — was the last slow call site. Stash ports that else branch to `compute_70d_frame_fast`.

**Is fast byte-identical? Yes, per the module contract.** `src/nexus_scalp/model_generation/schema_v2_incremental.py` docstring at parent `16e86d70` (unchanged at HEAD):

> `compute_70d_frame` was O(n^2)-or-worse ... This module provides `compute_70d_frame_fast` — a **semantics-preserving incremental builder that produces BYTE-IDENTICAL feature vectors** to the canonical per-row function while running in ~O(n*window) total ... VERIFIED against the canonical function on real data (see `tests/unit/test_70d_frame_incremental_phase19.py`).

The `6bb76497` commit message records the explicit verification that drove the integration: 200 synthetic bars, max diff `0.0` across `feat_0..feat_4`, col-set equality `80/80`.

Signatures at parent:

```python
# schema_v2.py
def compute_70d_frame(df: pl.DataFrame, *, min_bars: int = 55, spread: float = SPREAD_USD, news_frame: ...)

# schema_v2_incremental.py
def compute_70d_frame_fast(df: pl.DataFrame, *, min_bars: int = 55, spread: float = 0.20, news_frame: ...)

"""Incremental 70D frame builder — byte-identical to compute_70d_frame.
Same contract, same columns, same values. Only the time complexity
changes (O(n*window) instead of O(n^2+))."""
```

**One nuance — dropped `min_bars` forwarding.** Stash calls `compute_70d_frame_fast(bars_frame, news_frame=news_frame)` and discards the `min_bars` kwarg that the slow path forwarded. `build_feature_frame` defaults `min_bars=55` and no caller in `three_model.train_variant` / CLI passes a custom value, so observable behavior is identical at default. Strictly, a future caller that supplied `min_bars != 55` for a 70d variant would be silently clamped to `55` on the stash (and on the integrated `6bb76497`) path. Low-risk given the current call graph, but worth noting as a deliberate contract-narrowing — the integrated commit inherits it verbatim.

**Import hygiene — stash vs integrated.** The stash literal retains the now-dead top-level `from nexus_scalp.model_generation.schema_v2 import compute_70d_frame` (ruff `F401`). The integrated version `6bb76497` removes it; `HEAD` (`01ab9c7c` via `3179df9c`) is therefore **strictly better** than the stash literal. Applying the stash byte-for-byte would dirty `ruff`.

---

## 3) Untracked files — `git ls-tree stash@{2}^3` (third parent)

**60 entries**, not 30. Full enumeration:

```
.hermes/plans/2026-09-04_073000-future-full-70d-retrain.md
_splice.py
scratch/agent14_broken_asset_refs.txt
scratch/ci760/runtime_gate.json
scratch/nexusml_uibacksync_debug_state.json
scratch/nexusml_uibacksync_live_state.json
scratch/ns_antigod_census.json
scratch/ns_antigod_probe_bug220_valid_tally.py
scratch/ns_bug223_bugs_md_entry.md
scratch/ns_perf_tdf_p1_latency.py
scratch/ns_perf_tdf_p2_stalls.py
scratch/ns_perf_tdf_p3_stalls2.py
scratch/ns_perf_tdf_p4_downtime.py
scratch/ns_perf_tdf_p5_restart.py
scratch/ns_perf_tdf_p6_errorstorm.py
scratch/ns_perf_tdf_p7_evaltimeline.py
scratch/ns_pinc_exp2_offlive.py
scratch/ns_pinc_probe_r2.py
scratch/ns_pinc_probe_r2_out.json
scratch/ns_probe_bug209_api_surface.py
scratch/ns_probe_bug209_fixspec_dryrun.py
scratch/ns_probe_bug223_default_audit_seam.py
scratch/ns_probe_tdf_main1.py
scratch/ns_probe_tdf_main2.py
scratch/ns_probe_tdf_main3.py
scratch/ns_probe_tdfq1_1_schema_census.py
scratch/ns_probe_tdfq1_2_decision_funnel.py
scratch/ns_probe_tdfq1_3_candidate_chain_health.py
scratch/ns_probe_tdfq1_4_execution_losses.py
scratch/ns_probe_tdfq1_5_mode_split.py
scratch/ns_qa_tdfq1_funnel_query.py
scratch/ns_qa_tdfq2_final_checks.py
scratch/ns_qa_tdfq2_r1_bursts.py
scratch/ns_qa_tdfq2_r1_census.py
scratch/ns_qa_tdfq2_r1_census2.py
scratch/ns_qa_tdfq2_r1_repro.py
scratch/ns_qa_tdfq2_r2_candles.py
scratch/ns_qa_tdfq2_r2_chop.py
scratch/ns_qa_tdfq2_r2_chop2.py
scratch/ns_qa_tdfq2_r2_continuity.py
scratch/ns_qa_tdfq2_r2_logkinds.py
scratch/ns_qa_tdfq2_r2_logscan.py
scratch/ns_qa_tdfq2_r2_logtimeline.py
scratch/ns_qa_tdfq2_r2_verdict.py
scratch/ns_qa_tdfq2_r3_broker.py
scratch/ns_qa_tdfq2_r3_final.py
scratch/ns_qa_tdfq2_r3_lifecycle.py
scratch/ns_qa_tdfq2_r3_mode.py
scratch/ns_qa_tdfq2_r3_orders.py
scratch/ns_qa_tdfq2_r3_trace.py
scratch/ns_qa_tdfq2_r3_trace2.py
scratch/ns_register_bug217.py
scratch/parse_ci_jobs.py
scratch/parse_ci_runs.py
scratch/rungate/run_gate_container.sh
scratch/runtime_gate_local.json
scratch/t70d_f1_rebuild_out.txt
site/assets/search.js.bak2
site/assets/search.js.orig.bak
site/assets/search.js.orig302.bak
```

Character: entirely **scratch probes** (`ns_*` TDF/Q funnel/broker/order traces), `runtime_gate` snapshots, `.bak` search.js artifacts, a future-plan md, and `_splice.py`. Zero `src/` production code. Sample tops confirm read-only TDF forensics (`TDF-Q2 R2 part8: freeze-window classification verdict`, `TASK-TDF Wave-1 orchestrator probe — READ-ONLY`).

Expectation mismatch note: task brief said "expect 30 scratch probes" — actual count is 60 inclusive of the plan/baks; scratch-namespace alone is ~54 `ns_*` + 3 non-ns `scratch/`. Not material — verdict unchanged.

---

## 4) Parent `16e86d70` vs current main

`16e86d70..HEAD` path (abbreviated):

```
16e86d70 flagship pro site
  -> edd6694a version 9.0.8
  -> 7948b6b4 9000-line flagship
  -> 9bb9f692 status v9.0.8 drift heal (missed bump)
  -> b50f85a6 cinematic JS/CSS v2
  -> 62cfc512/706269f1 BUG-239 release.yml
  -> 6bb76497 forensic: use compute_70d_frame_fast for 70d variants (BUG-106 extension)
  -> 3179df9c Merge forensic/recover-three-model-fast (706269f1 + 6bb76497)
  -> d3c59d46/54227f52/3d8dd752 P3 build_site cleanup + docs-enhance wiring
  -> 4261c3d2 site/_site regenerate
  -> 694ee2b2 docs(forensics) reflect P3
  -> a5e2ccc4 ruff format heal
```

### Is the status bump obsolete?

**Yes — superseded.** Stash bumps `docs/project/status.md` `9.0.6 -> 9.0.8`. HEAD is `9.0.9` (`edd6694a` + `9bb9f692` -> `3d8dd752/4261c3d2` chain). Re-applying the stash hunk would **downgrade** HEAD. No integration needed.

### Is the docs-enhance wiring already integrated or still needed?

**Already integrated — zero lines remain.** Stash adds to `scripts/docs/build_site.py`:

- `shell()` `<link>` for `docs-enhance.css`
- `shell()` `<script>` for `docs-enhance.js`
- `main()` `src_enhance_css/js` + copy loop entries

`git diff stash@{2}..HEAD -- scripts/docs/build_site.py` -> **0 lines**. The identical wiring landed via P3: `54227f52` (wire assets) + `3d8dd752` (FLAG removal + wiring integration) + `4261c3d2` (site regenerate). Byte-identical shell blocks in `stash@{2}:build_site.py` lines 759-810 vs `HEAD`. No further work.

---

## 5) Contract validation

### Temporal (causal convention)

`build_feature_frame` docstring claims "Build the exact causal feature frame". Both `compute_70d_frame` and `compute_70d_frame_fast` are causal — every pool source and state predicate depends only on `bars[:k+1]`; the fast builder's prefix invariant is explicit in `schema_v2_incremental.py` (swings `confirmed_at <= times[k]`, running session/daily max/min, incremental `update_pool_states` with monotone predicates, HTF bucket max/min). The stash port preserves this — no look-ahead introduced. **PASS.**

### 70D

70D contract is `scalp_v3` **candidate-only** (50D `scalp_v1` is `ACTIVE_SCHEMA_ID`; `features/schema_contract.py`, `docs/70D_*`). Three-model variant matrix (`50d_main` / `70d_news` / `70d_liquidity`) lands `artifacts/models/scalp/XAUUSD/<variant>/` with execution behind `BenchmarkRunner` gate (`model_variants.json` + per-variant `*.meta.json`). The fast-path swap touches only the data-plane build — schema ID, artifact layout, and swap gating are unchanged. **PASS — no illicit promotion.**

### BUG-106

`BUG-106` is the `O(n^2) -> O(n*window)` dataset-build perf fix. At parent `16e86d70`, only the `50d_main` branch used the fast incremental builder; the `70d_*` else branch still called the slow `compute_70d_frame` (full-history swing/pool re-scan per row). The stash closes that gap; `6bb76497` message documents the verification (slow-vs-fast `max diff 0.0`). No approximation — byte-identical incremental path. **PASS — completes BUG-106.**

---

## 6) Verdict per file

| File | Verdict | Rationale |
|------|---------|-----------|
| `docs/project/status.md` | **REJECT — obsolete** | `9.0.6->9.0.8` rebase-downgrade vs HEAD `9.0.9`. No semantic change beyond version stamp. |
| `scripts/docs/build_site.py` | **ALREADY INTEGRATED — no-op** | Both `docs-enhance.css/js` wiring hunks present at HEAD; `stash@{2}..HEAD` diff is 0. P3 `54227f52/3d8dd752/4261c3d2` supersede. |
| `src/nexus_scalp/model_generation/three_model.py` | **ALREADY INTEGRATED — superior at HEAD** | Stash logic (70d -> `compute_70d_frame_fast`) was recovered as `6bb76497` -> merged `3179df9c` (`706269f1 + 6bb76497`). HEAD `01ab9c7c` is byte-identical in behavior to stash `09ba4df0` but fixes the stale `F401` import. **Do not re-apply stash bytes literally.** |
| Untracked (`stash@{2}^3` — 60 files) | **REJECT — quarantine** | All `scratch/` probes + `site/assets/*.bak` + `_splice.py` + `.hermes/plans/...` — read-only TDF forensics, CI runtime snapshots, future-plan draft. No `src/` production code. Keep in forensic snapshots; do not merge. |

### Is any piece of this stash still unintegrated?

**No.** Every tracked hunk has a live successor at `HEAD`:

- `status.md` — superseded by the `9.0.9` bump chain.
- `build_site.py` — superseded by P3 docs-enhance integration (`git diff stash..HEAD` 0 lines).
- `three_model.py` — **already integrated via `6bb76497 -> 3179df9c`**; applying the stash literally would regress import hygiene (`F401`) and discard nothing of value. The integrated commit retains the stash's Co-Authored-By provenance (`Co-Authored-By: forensic-recovery-2026-09-04`) and its blame lineage.

Caveat: if `min_bars` parameterization for 70d variants ever matters, both stash and `6bb76497` silently drop it (both use `min_bars` default `55`). If that parameter needs threading, the fix is `compute_70d_frame_fast(bars_frame, min_bars=min_bars, news_frame=news_frame)` — a one-kwarg delta off HEAD, not off stash.

---

## Evidence commands

```bash
git diff stash@{2}^1..stash@{2} --stat
git diff stash@{2}^1..stash@{2} -- src/nexus_scalp/model_generation/three_model.py
git show stash@{2}^1:src/nexus_scalp/model_generation/three_model.py | sed -n '100,160p'
git show 16e86d70:src/nexus_scalp/model_generation/schema_v2_incremental.py | head -80
git ls-tree -r --name-only stash@{2}^3 | wc -l; git ls-tree -r --name-only stash@{2}^3
git show 6bb76497 --stat; git show 3179df9c --stat
git diff stash@{2}..HEAD -- scripts/docs/build_site.py | wc -l  # 0
git show HEAD:src/nexus_scalp/model_generation/three_model.py | md5sum
git show 6bb76497:src/nexus_scalp/model_generation/three_model.py | md5sum  # = HEAD
git show stash@{2}:src/nexus_scalp/model_generation/three_model.py | md5sum  # != HEAD (F401 delta)
```

---

*Agent 3 — stash@{2} (483fc0c3). Stashes preserved, no mutations.*
