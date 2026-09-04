# MAIN INTEGRATION REPORT — 2026-09-04T15:50+03:30

> **Operator:** Nexus Main (recovery architect)
> **HEAD at report:** `979624fcaaebe9a8653df811091f6aa4045bc27a` (`origin/main` in sync)
> **Policy:** No stash pop/drop, no reset/clean, no retrain kill, no model promotion

---

## 1. What Was Integrated Into `main` (this recovery window)

| Batch | Commits | Files | Risk | Provenance |
|---|---|---|---|---|
| **A — Low** | `b015db93` `ui(NSE): make Admin Dashboard responsive on phones/tablets/monitors/ultrawide (CSS only)` | `Web/index.html` +61 (hamburger `nse-sidebar` + overlay) + `Web/responsive.css` +238 (new, CSS-only media queries) + `src/nexus_scalp/web/server.py` +4 (`/responsive.css` route) | **LOW** — CSS-only, media-query driven, JS only toggles `classList open` + `aria-expanded`, no execution/logic touch | Pushed via fleet docs lane; disjoint from all 6 stashes (`site/_site` vs `Web/*`) — verified zero path overlap |
| **A — Low** | `979624fc` `docs(forensics): 6 independent stash audits (one agent per stash)` | `forensic_recovery_20260904/*` (patch exports + per-stash parents) + `STASH_INTEGRATION_MATRIX.md` / `FORENSIC_RECOVERY_REPORT.md` scaffolding | **LOW** — docs-only | Forensic worktree triage (read-only) |

**Previously integrated (ancestor, not this window — kept for completeness):**

| Commit | What | Batch |
|---|---|---|
| `a5e2ccc4` | `ruff format heal on scripts/docs/build_site.py` | A |
| `694ee2b2` + `4261c3d2` + `3d8dd752` | `forensic(P3): wire docs-enhance assets in build_site.py shell + copy` + regen `site/_site` | A |
| `6bb76497` → merge `3179df9c` | `forensic: use compute_70d_frame_fast for 70d variants in three_model (BUG-106 extension)` — 70D now `O(n·window)` byte-identical to slow | **B — Contract-preserving** (perf, no contract change) — `F401` repaired, `ruff` clean, `test_three_model 5/5` + `test_bug106 2/2` + 200-bar `maxdiff 0.0` |
| `706269f1` | `fix release.yml CRLF corruption` | A (CI gate) |
| `62cfc512` (tag `v9.0.9`) | `fix(release): BUG-239 payload lacks build-info.json` | A |

**Nothing from Batch B/C (model swap, lifecycle, risk paths) was merged this window** — P0 blocks batch B/C per §14.

---

## 2. Stash Accounting — 0 INTEGRATE This Window

Per `STASH_INTEGRATION_MATRIX.md` (168 lines, isolated worktree `subagent-sa-3-fa4df021@9c6a2370`):

| Stash | Verdict | Why not integrated |
|---|---|---|
| `stash@{0}` `b92cbd90` hold-304 | **OBSOLETE (EMPTY)** | 0 B / 0 lines — WIP==base (`15969c2b`) |
| `stash@{1}` `07b2ef5d` hold-303 (959d7d90) | **OBSOLETE** | 1.4 MB `site/_site/**` generated churn — superseded by `4261c3d2` regen; would reintroduce FLAGs |
| `stash@{2}` `483fc0c3` hold-303 (16e86d70) | **CONFLICTING** | `docs-enhance` wiring DUPLICATE (HEAD already 6 refs) + status 9.0.8 stale vs HEAD 9.0.9 + `three_model.py` dead `from schema_v2 import compute_70d_frame` (F401) — useful `compute_70d_frame_fast` already on main via `6bb76497` |
| `stash@{3}` `e90013d5` hold-302 | **DUPLICATE** | Exact P3 payload already on main as `3d8dd752+4261c3d2` |
| `stash@{4}` `7ad11d9f` tmp-site | **DUPLICATE** | byte-identical to stash 3 minus status |
| `stash@{5}` `d3cab5bc` css-js-old | **OBSOLETE** | `site/assets/search.js+styles.css` 33 KB — superseded by `3501-line` HEAD flagship |

**0 overlaps** with `Web/responsive.css` / `server.py` — `a5e2ccc4..b015db93` file set is `Web/*` only.

**Preservation:** All 6 stashes intact (`git stash list` unchanged), `/tmp/stash-{0..5}.patch` archived, no `stash drop`.

---

## 3. Out-of-Stash Branches — Accountability

| Branch | HEAD | Decision |
|---|---|---|
| `forensic/p3-build-site-cleanup` | `54227f52` | **INTEGRATED** (merged `3d8dd752`) — kept as ref |
| `forensic/recover-three-model-fast` | `6bb76497` | **INTEGRATED** (merged `3179df9c`) — kept as ref |
| `pinc-stash-rescue@0c90725b` | `0c90725b` | **REJECT** — reverts 4 safety gates (`ScalerBundle.is_ready`, temporal contract, CHECK-MDL-03, ARCH-SEQ-UNIFY) |
| `hermes-subagent/*` (~90) | various stale forks `16e86d70` | **NO-OP** — quiescent docs noise (`1177 ahead` illusion) |
| `nse/checkpoint/mt5-pipeline-stash-20260903` | `a76b0a92` | **REJECT** — foreign-wip bulk deletions |

---

## 4. Acceptance Gates (before declaring integration complete)

### 4.1 Git Integrity — PASS (with notes)

| Check | Result | Evidence |
|---|---|---|
| HEAD vs origin/main | **PASS** | `979624fc` both |
| Unexplained uncommitted changes | **PASS** | staged empty; untracked are this report set (intended) + `STASH_INTEGRATION_MATRIX.new.md` (to be removed) |
| Lost/dropped stash | **PASS** | 6/6 intact — `git stash list` verified pre/post |
| Unresolved conflict markers | **PASS** | `grep -r "<<<<<<"` — 0 |
| Accidental generated files staged | **PASS** | `site/_site` not staged (`.gitignore`) |
| Duplicate implementations | **PASS** | `compute_70d_frame_fast` deduplicated — slow path retained only as validated parity reference |
| Deferred work documented | **PASS** | `RECOVERED_AGENT_WORK_SUMMARY.md` + this report |
| Worktrees | **NOTE** | 3 `scratch/rungate/*` prunable — `git worktree prune` deferred until review |

### 4.2 Build & Test — PASS (docs lane) + P0-blocked model lane

| Check | Result | Evidence |
|---|---|---|
| `ruff check .` | **PASS (sampled)** | `three_model.py` lane: `All checks passed!`; P3 lane: `ruff format --check` clean at `a5e2ccc4` |
| `mypy` | **DEFERRED** | docs-only window — full `mypy` gated on Batch B/C |
| Targeted tests `test_three_model* / test_70d_bug106*` | **PASS** | `5/5` + `2/2` + `py_compile` clean (prior merge `3179df9c`) |
| Critical ML suite | **BLOCKED** | coherent 70D 3-class tensor absent — suite withheld per `MODEL_READINESS_REPORT.md` (not a gate failure — correct to block) |
| Champion overwrite / persistence / security suite | **DEFERRED to Batch C** | blocked by P0 + not touched this window |

### 4.3 Model Integrity — P0 BLOCK (dataset PASS, bundle FAIL)

| Check | Result | Evidence |
|---|---|---|
| One coherent 70D 3-class ScalpNet bundle | **P0 FAIL** | `70d_liquidity/model.pt` `[4,32]` vs meta `[3]` — `artifacts/forensics/model_artifact_forensics_20260904.md` |
| Exact `(B,32,70)` contract | **PASS (code)** | `temporal_contract.FEATURE_DIM=70 / CANONICAL_SEQ_LEN=32` — `CONTRACT_AUDIT_REPORT.md` |
| Correct scaler / lineage / dataset identity | **PARTIAL** | scaler `70` correct; `dataset_id null` — no provenance link; `feature_schema_hash 235b8fccc96b7e0e` matches dataset |
| Correct git provenance | **FAIL** | `model.meta.json git_commit null` |
| Genuine training / behavioral / OOS / calibration | **BLOCKED** | withheld until coherent emission from PID `26528` |
| Offline/live equivalence | **BLOCKED** | contract sound, nothing coherent to replay |

### 4.4 Production Safety — PASS (no live exposure)

| Check | Result |
|---|---|
| Real order submitted | **PASS — 0** |
| Live mode silently changed | **PASS — PAPER default, LIVE behind confirmation — no change** |
| Champion overwritten | **PASS — 0 champion writes this window** |
| Unsafe model swap exercised | **PASS — `model_hot_swap` not called; failures are pre-existing (see security audit)** |
| Rejected candidate traded | **PASS — 0** |
| Degraded data silently traded | **PASS — `live_freshness_gate → BLOCKED/NO_TRADE` verified** |

---

## 5. Deliverables — 8/8

| # | File | Status |
|---|---|---|
| 1 | `FORENSIC_BASELINE.md` | **NEW** — HEAD `979624fc`, 6 stashes, 19 worktrees, PIDs `26528/25032`, artifact hashes |
| 2 | `FORENSIC_RECOVERY_REPORT.md` | **EXISTS** (`706269f1` window) — superseded by `FORENSIC_BASELINE.md` for current HEAD; kept as history |
| 3 | `STASH_INTEGRATION_MATRIX.md` | **NEW** — 168 lines, `subagent-sa-3-fa4df021@9c6a2370` — 0 INTEGRATE / 2 DUPLICATE / 3 OBSOLETE / 1 CONFLICTING |
| 4 | `RECOVERED_AGENT_WORK_SUMMARY.md` | **EXISTS** — R-1 integrated (`three_model` fast), D/F/X classified |
| 5 | `MODEL_ARTIFACT_FORENSICS.md` | **NEW** — 109 lines, 33 pt — **P0 INCOHERENCE** proven |
| 6 | `CONTRACT_AUDIT_REPORT.md` | **NEW** — 285 lines, `subagent-sa-2-628685f3@d3d8e11e` — **PASS with documented `scalp_v1` lag** |
| 7 | `MODEL_READINESS_REPORT.md` | **NEW** — 12 sections — `P0 ARTIFACT CONTRACT BLOCK` + ranked blockers + next verifiable step |
| 8 | `MAIN_INTEGRATION_REPORT.md` | **THIS FILE** — batch accounting + gate table |

Also: `SECURITY_AUDIT_SEC_CAPITAL_DATA_BROKER.md` (112 lines, 5 BLOCKED fixes) + machine dump `artifacts/forensics/model_artifact_forensics_20260904.json` (33 artifacts, `git add -f`).

---

## 6. Branch / Worktree / Tag State at Report

- **HEAD:** `979624fc` (main, origin/main, origin/HEAD)
- **Tags:** `v9.0.9@62cfc512`, `v9.0.8@3cd6b6a7` (no new tags)
- **Worktrees:** 22 total — 1 main + 18 alive detached/cache + 3 `scratch/rungate` prunable + `pinc-stash-rescue` @ `C:/tmp/pinc-stash-wt` (**preserve**)
- **Stashes:** 6 intact (0 dropped) — patches in `forensic_recovery_20260904/` + `/tmp/stash-{0..5}.patch`

---

## 7. Risk Assessment for What *Was* Merged

| Risk | Level | Mitigation |
|---|---|---|
| `b015db93` responsive CSS regresses trading UI | **LOW** | CSS-only media queries, no JS execution change; server route is static file serve; disjoint from stashes |
| `979624fc` docs noise destabilizes CI | **LOW** | Markdown-only, `.gitignore`-adjacent, no `src/` |
| Future Batch B/C drift while P0 holds | **MEDIUM** | **BLOCKED FROM MERGE (Batch B/C)** until coherent `[3,32]` bundle — enforced by this report |

No `torch.load / hot_swap / scaler / persistence` change shipped this window.

---

## 8. Remaining Blockers (ranked — do not downgrade)

| Pri | Blocker | Gate |
|---|---|---|
| **P0** | `MDL-INCOHERENCE` — `70d_liquidity` `[4,32]` vs meta 3 — re-emit via live 34×10 retrain | **BLOCKED FROM LIVE / MERGE B-C / PROMOTION** |
| **P1** | `SEC-CAPITAL-DESER` — 9 `torch.load` without `weights_only=True` | **BLOCKED** |
| **P1** | `SEC-CAPITAL-PATH+AUTH` — arbitrary `model_artifact_path` + unauth `model_hot_swap` + CORS `*` | **BLOCKED** |
| **P2** | `DATA-GOV-PROVENANCE` — `dataset_id/lineage/git_commit null` | FAIL |
| **P2** | `REL-BUG-160+BUG-239` — release staging + ISCC | DEFERRED (separate PR stack) |
| **P3** | `BUILD-SITE-FLAG` — dead `FLAG_BUILD_INDEX` tail | DEFERRED P3 PR |
| **P3** | `WORKTREE-PRUNE` — 3 prunable wts | LOW — after review |

---

## 9. Final Decision for Integration

```
CORRECTNESS:  PARTIAL — docs+perf correct; model bundle incoherent (pre-existing)
TESTING:      PARTIAL — docs/contract lanes PASS; model lane WITHHELD (correct)
REVIEW:       PASS — 6-stash triage + 33-pt forensics + contract audit all evidence-graded
OPERATIONS:   HEALTHY — no lost stash, no reset, retrain alive (26528/25032)
RISK:         MEDIUM (P0/P1 remain but are fenced, not shipped)

FINAL_DECISION: ACCEPT_WITH_NOTES — this Batch-A (docs + CSS + prior B fast-path) integration is safe;
                Batch B/C integration is BLOCKED until P0/P1 cleared and a coherent 70D 3-class bundle is proven.
SAFE LEVEL:   SAFE TO CONTINUE VALIDATION — SAFE FOR PAPER — BLOCKED FROM LIVE
```

---

## 10. Next Exact Actions

```bash
# 1. Publish current HEAD (already on origin/main)
git log --oneline -3   # 979624fc / b015db93 / a5e2ccc4 — all on origin

# 2. Commit & publish this recovery batch (docs-only, no stash drops)
git add FORENSIC_BASELINE.md STASH_INTEGRATION_MATRIX.md MODEL_ARTIFACT_FORENSICS.md \
        CONTRACT_AUDIT_REPORT.md MODEL_READINESS_REPORT.md MAIN_INTEGRATION_REPORT.md \
        SECURITY_AUDIT_SEC_CAPITAL_DATA_BROKER.md
git commit -m "docs(forensics): recovery-kit 979624fc — baseline + stash matrix + artifact/contract/readiness + security + main integration"
git push origin main   # Batch A docs — no model/B/C payload

# 3. Await coherent bundle from PID 26528 — then (separate PR):
#    sha256sum model.pt  → classifier.weight [3,32] → dataset_id + hash + seq_len + git_commit → 12 probes → OOS + calibration → (B,32,70) replay parity

# 4. Defer safely:
#    - git stash drop — NEVER (preserve 6 stashes as evidence)
#    - git worktree prune — only after stakeholders review pinc-stash-rescue
#    - P3 FLAG cleanup — dedicated PR with build_site rebuild gate
#    - P1 security fixes — Batch C PR (weights_only, path allow-list, auth/CORS)
```

*All decisions are evidence-backed (hashes, tensor shapes, process probes, patch exports at `forensic_recovery_20260904/stash-{0..5}.patch`) — not agent summaries.*
