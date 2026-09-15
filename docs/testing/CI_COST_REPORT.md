# CI Cost Report — Radical Test Reconstruction (2026-09-14/15)

All numbers are MEASURED, not estimated. Provenance marked per row.

## BEFORE

**Suite scale (measured — AST inventory `scratch/recon/inventory_raw.json`):**
- 481 test files (`test_*.py`), 5,810 collected test functions
  (1,461 in the then-117-file critical suite; 3,311 unit-collected in the
  baseline census run)
- 125,404 lines of test code
- 14 workflows

**PR/push gate cost (measured — GitHub Actions, run set 34866523178–34866523254, 2026-09-14 main push, 11 required contexts):**
| job | OS | wall | note |
|---|---|---|---|
| CI (ci.yml quality, full critical suite) | ubuntu | 6 min | the gate proper (~5.5 min xdist per its own in-file comment) |
| Tests (OS Matrix) windows leg | windows | **16 min** | identical manifest re-run, REQUIRED |
| Tests (OS Matrix) macOS leg | macos | 5–14 min | identical manifest re-run, REQUIRED |
| Security (CodeQL+Trivy) | ubuntu | 3 min | every PR, no path filter |
| QA Deep Assurance | windows | 2 min | full torch install for an ~8 s battery, every PR, NOT required |
| Deps/JS/Docs/OSV | ubuntu | <1–2 min each | no path filters |

- **The critical suite executed 3× per PR/push** (ubuntu + windows + macOS).
- Merge latency floor = slowest required leg ≈ **16 min** (Windows), typically
  ~10–20 min end-to-end; heavy/extended tests had **no lane at all** on PR or
  main push (ci-tests branch or manual dispatch only).
- **65% of the critical suite wall time** came from 3 files
  (measured, `scratch/recon/crit_durations.json`, local xdist worker-seconds):
  `test_bug276_unique_constraint_shadow` 1252s,
  `test_perf_deadletter_skeleton_repro` 730s,
  `test_news_db_truncation_honesty` 428s.

**Local baseline (measured, this machine, 8-core, xdist):**
- full unit census: 3,311 tests, 1,438 worker-s aggregate, **68 failed**
  (order-dependence + environment) — run interrupted at ~90% (KeyboardInterrupt),
  still ~24 min wall before cancel
- then-117-file critical suite: 1,571 tests, **662s wall**, 1 failed

## AFTER

**PR-critical gate (T1) — measured, this machine:**
- **127s wall** (down from 662s = **5.2× faster**), 1,851 tests, 143 files
  (+29 P0 promotions, −3 cost outliers moved to T2)
- 1 real failure: `test_smoke_self::test_full_smoke_under_budget` — PRE-EXISTING
  (also red in the BEFORE baseline and the first AFTER run; green on CI ubuntu:
  the smoke check-complement is environment-tiered; open item documented in
  TEST_ARCHITECTURE.md, NOT silenced)

**Per-event cost (policy — GitHub-enforced; PR legs measured where runnable):**
| event | runs | expected wall |
|---|---|---|
| feature-branch push | T0 + T1 ubuntu | ≈ CI 6 min |
| PR | T0 + T1 ubuntu + T3 Windows-skew (~60 worker-s skew suite) + macOS/deps/JS/OSV always-report green (~1 min, no torch) | PR-blocking path ≈ max(6, ~4) min, down from ~16 |
| main push | above + T2 extended + T3 Windows-FULL + macOS-FULL | ~20–25 min post-merge |
| nightly 01:17 | T4 slow/chaos/mutation | unchanged |
| release tag | release-auth → T2 extended on SHA + smoke → build | gates drop ~8–10 min of duplicated ruff/mypy/critical re-run (measured duplication) |

**T3 OS PR lane (measured):** Windows skew manifest = 60 tests, **<60 worker-s**
aggregate (`per_file_costs.json`). macOS full suite runs only on main push /
Monday schedule / dispatch — its 5×-billed per-PR re-execution removed.

## Duplication removed (measured file-level)
1. Windows + macOS full-suite re-runs ×2 per PR (identical to ubuntu run) — now
   skew-subset on Windows, always-report on macOS, full only on main/schedule.
2. QA Deep Assurance Windows+torch on every PR for a ~8 s fast battery — PR/push
   triggers deleted.
3. `release.yml:gates` re-ran ruff+format+mypy+full critical suite that
   release-auth already proved green on the same SHA (measured ~8–10 min of CI
   job time; replaced with the T2 extended suite the required checks never ran).
4. `heavy-ci` integration arm vs research-backtest/model-validation arms
   re-running `test_research_api.py`/`test_model_lifecycle_api.py` within one run
   — deduped (2026-09-03 comments had fixed quality-vs-heavy but left
   heavy-vs-heavy overlap).

## Test-count deltas (not a goal, recorded anyway)
- deleted 5 files (mock-return no-ops / CI-skips-only / dead-functionality /
  cross-run-file golden) + 1 moved to `tests/manual/`; each justified in
  `test_value_matrix.md`.
- 29 critical-gate promotions; 3 measured cost outliers relocated T1→T2.
- 5 new Tier-1 batteries; all mutation-validated: **9/9 KILLED**
  (`scripts/testing/mutation_check.py`).

## Honest caveats
- Local xdist worker-seconds ≠ GitHub runner-minutes (different CPU, no coverage
  in local timing, torch from cache not install). The GitHub run set is quoted
  where CI minutes are the claim.
- 396/481 files still UNREVIEWED (verdict-salvage limit) — deletion policy
  protects them; tier assignment is by machine signals until the next wave.
- The reconstructed PR lane was not yet exercised end-to-end on GitHub (this is
  the push that starts that measurement); the T1 timing above is the authoritative
  local number and matches the structural budget argument (143 files, none of the
  3 cost outliers, promoted set ~20 s).

---

# WAVE 2 — Agent-Loop Reduction (measured 2026-09-15, this machine, 8-core)

## Success metrics (§33), before → after, measured not estimated

| metric | wave-1 end | wave-2 end | how measured |
|---|---|---|---|
| test files on disk | 486 | 481 | AST inventory (`inventory_raw.json`, 490 raw incl. manual/) |
| collected tests | 5,891 | 5,851* | pytest collection (same basis both runs) |
| T0 FAST lane | none (fast_suite unwired) | **25 files, 20–26 s wall**, wired into `check_local.py` | `-n 4` loadgroup, two runs |
| T1 files / tests / wall | 143 / 1,931 / 127 s | **163 / 1,768 / 141 s**, 0 failed | `-n auto` junit (`junit_w2_final.xml`) |
| T1 worker-seconds | 542 | 525 | junit sum (net −17 s at +22 files) |
| T2 lane | 3 files (never executed) | **14 files, 81 s, runs on every main push + release SHA** | `ci.yml:extended` (green on main, GitHub run 34909926825) |
| files with forensic verdict | 110 (salvaged) | **~340 (63 PROTECTED + 277 reviewed)** | `merge_verdicts.py` + `test_protection.json` |
| CR-registry owners actually manifested | 41 (drift: recon batteries + named owners missing) | **all named owners manifested** (+22 files, +50 ws) | manifest diff + `verify_critical_suite_manifest` 163/163 |
| silent-zero-collection files fixed | 1 (hist_sim deleted) | +1 more: `tests/cli/test_docs_consistency.py` (0 test functions → real T1 test) | run rc=0 |
| evidence-backed deletions | 6 | +5 (merged-duplicate + delegation mirror; §14-verified, superset counts 108=87+21, 92=66+26) | `classification_overrides.json` |
| agent edit→feedback loop | pytest tests/unit ≈ 24 min | **20 s FAST, 141 s CRITICAL** | above |

## Value/cost model (brief §16) — what moved and why
- **Low value / high cost, deleted**: 4 merged incident/forensic source files
  (unmanifested, 0 CR rows, superset-verified) + dead-letter delegation mirror.
- **High value / high cost, DEMOTED not deleted** (−74 ws off PR lane, still
  main-push+release executed): forensics 237→12.3 ws-share, incidents,
  walk_forward_trainer, lifecycle_e2e_v3, outcome_recovery_sweep, +6 family pins.
- **High value / low cost, PROMOTED** (+50 ws, 22 files): every CR-named owner
  incl. the five mutation-KILLED recon batteries, CI meta-gates (tests/ci/*),
  docs-CLI consistency.
- **Kept despite cost with a rewrite ticket** (fixture census, est. recoverable
  seconds): gate_bypass 5213 s→materialize-once (~350 s), live_state_contract
  539 s→module app (~350 s), bug276/deadletter/migrations ~2.4k s→blessed-DB
  backup-copies (~1.9k s), news seed 428 s→single txn (~300 s). Deferred with
  evidence in `wave2_findings_open`; NOT deleted (§35).

## Honest caveats (wave 2)
- 141 s T1 wall on an 8-core dev box ≠ GitHub ubuntu-4core minutes (CI stays
  ~6 min with install; the +14 s net cost lands well inside the ≤5 min budget).
- Two registry owners stay UNPROTECTED-by-manifest pending rewrites, disclosed:
  `packaged_db_and_mode_bug146_149` (order-dependent boot) and
  `launcher_paper_boundary_bug212` (split mandate) — invariant currently
  carried by bug232/replay_toggle/marketplace in T1; registry cells updated.
- `rejected_candidate_not_persisted` discovered as a deterministic local RED
  (early-stop vs accept-persist contract, both pre- and post-#199 trees) — a
  real production question filed for the trainer owner, not hidden (§26/§42).
