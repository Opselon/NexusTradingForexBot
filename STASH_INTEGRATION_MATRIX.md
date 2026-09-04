# STASH INTEGRATION MATRIX — 2026-09-04

> **Source of truth:** `FORENSIC_RECOVERY_REPORT.md` (patch exports under `forensic_recovery_20260904/`).  
> **Policy:** Apply only byte-identical, lint-clean, test-proven production code. Generated `site/_site/` is never integrated from a stash.

| Stash | Owner / Source | Base | Scope | Non-site `src` change? | Overlap | Risk | Evidence | Decision |
|---|---|---|---|---|---|---|---:|---|
| `stash@{0}` `b92cbd90` | SA-1 `16e86d70` — `hold-304` | `16e86d70` | 0 tracked files (empty working) — `stash@{0}^3` absent | No | — | None | `git diff stash@{0}^1..stash@{0}` → 0 lines; `stash@{0}.patch` 0 lines | **REJECT (empty)** — duplicate clean stash, nothing to integrate |
| `stash@{1}` `07b2ef5d` | SA-2 `959d7d90` — `hold-303` | `959d7d90` (117 commits behind `main`) | 339 files `+344 −3352` — `site/_site/*.html` rev footer `d267fbd→959d7d9` + **deletion** of 1504-line `FLAGSHIP 9000 JS PREMIUM PACK` in `site/assets/search.js` | No (`src/` absent; `walk_forward_trainer` diff vs `main` would *revert* `3cd6b6a7` int-label fix) | Site footer + search.js FLAG removal overlap `stash@{3}/@{4}` | **HIGH if merged** — stale base would revert 3cd6b6a7 (parquet int labels) and era fixes; site `_site` is generated | `stash-1.patch` 7457 lines, parent grep `parquet integer` = 0/0 | **REJECT (obsolete/duplicate)** — stale docs hold, superseded by `b50f85a6/7150e3de` flagship merges on `main` |
| `stash@{2}` `483fc0c3` | SA-1 `16e86d70` — `hold-303` | `16e86d70` | **3 tracked** `+16 −4` — `docs/project/status.md` 6→8, `scripts/docs/build_site.py` (+`docs-enhance` wiring), **`src/nexus_scalp/model_generation/three_model.py` 70d→fast** + 30 untracked probes (`scratch/ns_*`, `.hermes/plans`, `_splice.py`) | **Yes** — `three_model.py` `compute_70d_frame → compute_70d_frame_fast` (70d variants) | `three_model` byte-identical to `stash@{3}`; docs wiring overlaps `stash@{3}`/`stash@{4}`; scratch noise unique | **LOW** for `three_model` alone; **MEDIUM** for docs wiring bundled in same patch | `compute_70d_frame_fast` docstring `byte-identical`; isolated 200-bar slow-vs-fast `max diff 0.0` `cols 80/80` `rows 146=146`; raw patch `F401` → repaired; `ruff` + `test_three_model 5/5` `test_buf106 2/2` green | **INTEGRATED (partial)** — `three_model` fast fix merged via `forensic/recover-three-model-fast` (`6bb76497` → merge `3179df9c`); `status.md` bump **ignored** (main now `9.0.9`); docs-enhance wiring **COMPLETED P3 (pushed 4261c3d2)**; untracked probes **REJECTED** |
| `stash@{3}` `e90013d5` | main `66555ea7` — `hold-302` | `66555ea7` | 334 files (filtered non-site: same `status.md` 6→8, `build_site.py` **615-line `FLAG_BUILD_INDEX_0000…0140` deletion + `docs-enhance` wiring**, `site/assets/search.js` FLAG-JS deletion, `three_model.py` same fast fix) + `site/_site` regen | **Yes** — same `three_model` as `stash@{2}` | `three_model` = `stash@{2}`; build_site FLAG cut = `stash@{4}`; site = `stash@{1}/@{4}` | LOW for `three_model`; LOW but noisy for FLAG cut | `stash-3.patch` 13826 lines; `FLAG` tail after `raise SystemExit` — dead code, `py_compile`/`ruff` clean even without removal | **INTEGRATED (deduped)** — `three_model` already covered by `stash@{2}` integration; remaining FLAG/site/docs-enhance **COMPLETED P3 (pushed 4261c3d2)** |
| `stash@{4}` `7ad11d9f` | main `66555ea7` — `tmp-site` | `66555ea7` | `scripts/docs/build_site.py` 615-line FLAG cut + `site/assets/search.js` FLAG removal + `site/_site` regen | No | Build_site/site overlaps `stash@{3}`; opposite polarity to `stash@{5}` (+FLAG) | LOW (cosmetic/docs) | `stash-4.patch` 13785 lines, all site/build_site | **COMPLETED P3 (pushed 4261c3d2)** — docs-only, no production code; to be handled as one coherent cleanup PR |
| `stash@{5}` `d3cab5bc` | SA-2 `16e86d70` — `css-js-old` | `16e86d70` | 2 files `+302 −1` — `site/assets/styles.css` (+288 cosmetic) `site/assets/search.js` (+14 inlines) | No | Contradicts `stash@{1}/@{3}` (add FLAG vs remove) | LOW but contradictory | Oldest stash (08:04); superseded by later flagship `3501+2849+1332` merges on `main` | **REJECT (superseded/contradicted)** — asset delta already subsumed by `b50f85a6/7150e3de` |

### Out-of-stash branches / worktrees referenced

| Artifact | Decision | Reason |
|---|---|---|
| `pinc-stash-rescue` `0c90725b` (wt `C:/tmp/pinc-stash-wt`) | **REJECT** | Would revert 4 P0/P1 gates: `ScalerBundle.is_ready` zero-std, `LiveEngine` temporal contract, `CHECK-MDL-03` 70D era fix, `ARCH-SEQ-UNIFY` SSoT — see Recovery Report §3 |
| `nse/checkpoint/mt5-pipeline-stash-20260903` | **REJECT** | Bulk deletions, foreign-wip checkpoint, not for `main` |
| `nse_qa_head_wt` `tests/unit/test_release_system.py` +119 (BUG-160 fail-before) | **DEFERRED P2** | Sound proofs; integrate via `BUG-160` branch (needs `release.yml` pre-stage + ISCC contract) |
| `nse_bug223_failsbefore` CRLF warning only | **IGNORE** | No content diff |
| `nse-relcert/scripts/cert/` untracked | **DEFERRED P2** | Pending BUG-160 tooling branch |
| `hermes-subagent/subagent-sa-0-8c5a9a11` / `sa-1-6eccae4e` / `sa-2-4e6dc39d` etc. (90 branches) | **NO-OP** | `1177+ ahead` is stale-fork artifact; effective non-site diff empty or docs-only already merged |

### Batch integration log

| Batch | Content | Commits | Verification |
|---|---|---|---|
| **Batch 1 — Low-risk foundational** | *(none required — stashes carried no CI/tooling fix beyond site regen)* | — | — |
| **Batch 2 — Contract-preserving prod fix** | `three_model.py` 70d `compute_70d_frame → compute_70d_frame_fast` (BUG-106 completion) | `6bb76497` (forensic branch) → merge `3179df9c` on `main` | `ruff check` clean (F401 repaired), `py_compile` clean, `test_three_model_pipeline 5/5`, `test_70d_bug106_incremental_phase19 2/2`, isolated slow-vs-fast `maxdiff 0.0` |
| **Batch 3 — High-risk** | *(none — `pinc` reverts intentionally excluded; no model-swap/execution change in stashes)* | — | gated |
