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
