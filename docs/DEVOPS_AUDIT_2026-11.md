# NSE DevOps Audit — 2026-11-06

Full DevOps engineering audit of the CI/CD architecture performed on the
`devops/full-pipeline-audit-optimization` branch (PR branch). Every claim below
was verified against the repository at commit `99d1e97c` (v9.0.23), the
GitHub Actions history (runs through 2026-11-06), branch protection (read-only
API), and local test execution. This document is the evidence base for the
changes in the accompanying PR.

## 1. Current architecture (verified)

### Workflows (20 files, 5,030 lines of YAML)

| Workflow | Trigger | Role |
| :--- | :--- | :--- |
| `ci.yml` (1,801 ln) | push/PR main·develop·ci-tests, Mon cron, dispatch | Primary PR gate: integrity, quality, runtime provider matrix, DB fabric, Wine MT5 runtime, verdict, extended (main), heavy-ci (ci-tests) |
| `tests-os.yml` | push/PR main·develop·ci-tests, Mon cron, dispatch | Required `Py Tests (windows-latest/macos-latest)` contexts; lane-classified (PR=skew subset, main=full) |
| `dependency-lock.yml` | push/PR main·develop | `Dependency drift` + `Migration safety` required contexts |
| `dependency-autoregen.yml` | push/PR (pyproject), daily cron | Lock regeneration bot |
| `security.yml` / `osv-scanner.yml` | PR + cron | CodeQL, Trivy, OSV required contexts |
| `js-tests.yml`, `go-api.yml`, `docs.yml` | PR/push, path-filtered | Required `Frontend JS Unit Tests`, Go gate, `Validate documentation` + Pages |
| `release.yml` (882 ln) | `v*` tags, dispatch | fail-closed SHA-evidence auth gate → validate → extended gates → windows-x64 build → signed publish |
| `nightly-qa.yml`, `nightly-e2e.yml`, `qa-deep-assurance.yml`, `nightly-ml-benchmark.yml` | cron | Deep validation tiers |
| `perf-benchmark.yml`, `pr-evidence.yml`, `lockfile-diff.yml`, `official-model.yml`, `docker.yml`, `drift-debug.yml` | PR paths / dispatch | Advisory lanes |

### Required status checks (branch protection, 11 contexts, enforce_admins=true)

`CI Integrity and Change Classification`, `Code Quality & Tests`
(both `ci.yml`), `Py Tests (windows-latest)`, `Py Tests (macos-latest)`
(`tests-os.yml`), `Dependency drift (lock vs pyproject)`, `Migration safety
(fail-loud + version postconditions)` (`dependency-lock.yml`), `Frontend JS
Unit Tests` (`js-tests.yml`), `CodeQL Analysis`, `Trivy Vulnerability Scan`
(`security.yml`), `OSV Scanner / osv-scan` (`osv-scanner.yml`),
`Validate documentation` (`docs.yml`).

`scripts/release/release_auth_gate.py:REQUIRED_MAIN_CI_CHECKS` mirrors exactly
these 11 contexts; a release cannot publish without all of them green on the
exact release SHA (fail-closed, verified at release.yml:42-66).

**Gap:** the release gate does NOT require the runtime certification lanes —
`Full Runtime Overall Verdict` (SQLite/PostgreSQL real-process runtime + MT5
terminal runtime) and `DB Fabric Certification` are absent from
`REQUIRED_MAIN_CI_CHECKS`, so a release can publish from a SHA whose runtime
certification never ran or failed (those lanes run on PRs but are not
required contexts).

### PR critical path (measured, 6 successful PR runs, 2026-11-05/06)

| Metric | Value |
| :--- | :--- |
| Wall clock (median) | **9m55s** (range 9m13s–10m42s) |
| Queue time (median) | 4s (one 93s outlier) |
| Summed job time (median) | 30.3 runner-min, all ubuntu |
| Longest job | `Code Quality & Tests` — 550s median (9m10s) |
| Second chain | provider soak (314s) → DB fabric (204s), parallel to quality |
| `mt5-wine-runtime` | 488s median (8m08s); slow-path incidents hit 34m of winehq download (runs 42116643396, PR #721/#723 history) |

### Action pinning

116/116 third-party and first-party action refs are pinned to immutable commit
SHAs (the two non-SHA grep hits are comment text). `osv-scanner` is pinned to
the `google/osv-scanner-action` v2.7.0 SHA. Default workflow token permissions
are `read` (repo setting), per-workflow grants are least-privilege, and
`persist-credentials: true` appears only in `dependency-autoregen.yml:79`
where committing regenerated locks requires it.

## 2. Verified problems (with evidence)

| # | Problem | Evidence | Severity |
| :--- | :--- | :--- | :--- |
| P1 | **No native Windows MT5 certification exists.** The only terminal runtime lane is Wine (`ci.yml:1308-1443`). `MetaTrader5` wheels are win32-only; `DirectMT5Adapter` (native IPC) is the production Windows path (`src/nexus_scalp/adapters/mt5/mt5_adapter.py:150`), but no CI job ever calls `mt5.initialize()` on a Windows runner. `tests/integration/test_mt5_api_probes.py` (real IPC suite) runs only in `release.yml:165` on **ubuntu**, where the wheel cannot install → every probe skips. Native Win32 IPC is certified by zero CI evidence. | grep of all 20 workflows; tests-os.yml has no MT5 content | Critical |
| P2 | **MT5 installer integrity is recorded, never verified.** `scripts/linux/mt5_runtime.sh:102` writes `mt5setup.exe.sha256` but compares it to nothing; no Authenticode verification anywhere. | mt5_runtime.sh:97-112 | High |
| P3 | **Linux Gateway test lane gap.** `tests/unit/test_remote_gateway.py`, `test_gateway_e2e.py`, `test_gateway_secrets_contract.py`, `test_mt5_parity_fix1.py` (HMAC, fail-closed, default-credential refusal, order-write uncertainty) appear in NO CI manifest — they execute only when a developer runs the full local suite. Linux CI sees only 2 adjacent files via `tests/critical_suite.txt`. | grep across `tests/*.txt`, workflows, scripts/ci: zero hits | High |
| P4 | **Release gate omits runtime/DB-fabric certification** (see §1). | release_auth_gate.py:75-87 | High |
| P5 | **Nightly deep-validation tiers have been red ≥5 consecutive days** — `nightly-qa` (5 failures, slow-suite: `test_accounting_api.py` assert 0==2, `test_cli_end_to_end.py` export sanitize, `test_shadow_phase11.py` NEXT_RUN_FAILED), `nightly-e2e` (3 failures), `qa-deep-assurance` (5 failures). Real test/product drift, not infra. The red signal is currently ignored. | run 42184149823 (2026-11-06 07:56) job logs | High |
| P6 | **Committed junk artifacts** (~3.5 MB): `cov_probe.json` (3.3 MB), `cov_baseline_run.txt`, `cov_probe.txt`, `audit_findings.json`, `scratch_audit/lane_results/sqlite_trap_manifest.json` (stale evidence; the trap test recreates it at runtime). Committed by one-off feature commits; referenced by no workflow, script, or test. Two candidates wereinvestigated and deliberately KEPT: `cov_critical_baseline.xml` (2.8 MB — read by `tests/ci/test_critical_manifest_gate.py:95` as the measured baseline; the test skips when absent) and `scratch/` (go-api.yml documents `scratch_ground_truth.py` as the local route-parity tool). `.bench-baseline.json` is used by perf-benchmark.yml — NOT junk. | `git log --follow`; repo-wide grep | Medium |
| P7 | **Wine fragility on the required path.** Two incidents in two days (runner contention, winehq CDN at ~150 KB/s → 34m download); timeout now 75m (commit 2eca47c5). Wine can never certify native Win32 IPC (documented `-10005 IPC timeout` under Wine, `scripts/linux/README.md:17-20`), yet it is the only MT5 runtime line and it gates `Full Runtime Overall Verdict` and release readiness. | ci.yml:1311-1329 comments + git history | High |
| P8 | **Stale comments:** `dependency-lock.yml:10` claims "The repo venv builds through the lock (see ci.yml 'Install via lock')" — no such step exists anywhere in ci.yml; all installs are plain `pip install -e ".[dev,web,...]"` (lock used only by `migration-safety`). `scripts/ci/db_leak_guard.py` docstring claims it "runs in CI" — no workflow invokes it. | grep | Low |
| P9 | **macOS cost on main pushes.** `tests-os.yml` runs the FULL critical suite on macOS for every main push (~8m25s at the 10× macOS multiplier ≈ 84 of ~119 billable min per main run). macOS ships nothing (release builds windows-x64 only); its role is drift detection, which the Monday 07:17 UTC FULL sweep already provides. | run 42171331955 timings; tests-os.yml:18-25 comments | Medium |
| P10 | **Artifact retention:** several nightly/assurance uploads keep 90 days (`nightly-qa.yml`, `qa-deep-assurance.yml`) — excessive for advisory lanes. | grep retention-days | Low |

## 3. Suspected / needs evidence

- `uv.lock` (438 KB) is committed but no workflow installs through it; the
  documented lock flow is `uv pip compile` → `requirements.lock`. Whether
  `uv.lock` is maintained by local dev tooling needs owner confirmation
  (left untouched in this PR).
- `heavy-ci` research-backtest/model-validation arms now run only 2 files /
  1 file respectively — heavy matrix overhead (4 installs) vs signal is thin,
  but it is the surviving lane for genuinely heavy suites; kept.

## 4. Non-problems (verified healthy, deliberately not "fixed")

- Workflow self-integrity scanner (`check_workflows.py --strict`) passes with
  0 findings; decision-ID, ML-contract-drift, gate-parity, git-governance
  checks are unusual and valuable — kept.
- `quality` job's fail-closed final gate (`ci_final_gate.py`) correctly treats
  missing results as failures; skipped-as-pass is already prevented in every
  aggregate (`if: always()` + explicit `needs.*.result` checks).
- Branch protection `strict: false` is acceptable (fast-forward PRs dominate).
- `tests-os.yml` SKIP-REPORT honesty contract (DEC-0008) is a deliberate,
  documented design; required contexts never go MISSING.
- Supply chain: SHA pinning, least-privilege permissions, no
  `pull_request_target`, no secrets to forks, release signing (Ed25519),
  SBOM, checksums — already strong.

## 5. Changes implemented in this branch (mapped to problems)

| Commit | Fixes |
| :--- | :--- |
| `ci: add native Windows MT5 certification` | P1, P2 — `mt5-native-windows.yml`: Authenticode-verified official installer, silent install, real `mt5.initialize()/terminal_info()/version()` IPC probe, adapter-level probe, demo-gated Level 2/3 (secrets present → demo market-data; `NSE_DEMO_TRADE_TEST=true` → order test with account-type enforcement). Wired into `Full Runtime Overall Verdict`. |
| `ci: separate Wine compatibility workflow` | P7 — after native certification proved green in CI, Wine moves to `mt5-wine-compat.yml` (dispatch + weekly cron + Linux/Wine compat path filter), independent of PR verdicts and releases. |
| `ci: close Linux gateway test lane gap` | P3 — 4 orphaned gateway test files added to `tests/critical_suite.txt` (verified locally: 55/55 pass with full dev env). |
| `release: require runtime + DB fabric certification` | P4 — `REQUIRED_MAIN_CI_CHECKS` += `Full Runtime Overall Verdict`, `DB Fabric Certification (SQLite ↔ PostgreSQL)`. |
| `perf: install via lock (uv) + split quality job` | P-none — restore the documented install-via-lock flow and split lint/type from tests under the preserved `Code Quality & Tests` context; benchmarked before/after in the PR. |
| `chore: remove committed junk artifacts` | P6 + `.gitignore` hardening (cov_probe*, cov_baseline_run, audit_findings.json, scratch_audit/, ci-results*/). |
| `docs: correct stale CI claims` | P8 — dependency-lock.yml comment, db_leak_guard docstring. |
| `perf: trim macOS main-push lane` | P9 — macOS main-push returns to SKIP-REPORT (Monday FULL sweep unchanged). |
| `chore: trim advisory artifact retention` | P10. |
| Nightly red suites (P5) | NOT fixed here — root cause is product/test drift in `nightly-qa` slow suite, `nightly-e2e`, `qa-deep-assurance` (assert failures in accounting API, CLI export sanitizer, shadow phase11). Fixing them is an application change outside this DevOps PR's scope; every failure is documented with run IDs so the product team can triage. |

## 6. Nightly failure evidence (P5)

| Workflow | Latest failing run | Failing tests (samples) |
| :--- | :--- | :--- |
| `nightly-qa.yml` | 42184149823 (2026-11-06T07:56, 4m17s) | `test_accounting_api.py::test_performance_overview` (assert 0 == 2), `::test_performance_intelligence_endpoint`, `::test_period_endpoints_all_granularities`, `::test_strategies_endpoint_linked`, `test_account_summary_never_serves_synthetic_numbers` (assert 0 == 2), `test_cli_end_to_end.py::test_e2e_13_diagnostics_export_is_sanitized_zip_without_secrets` (assert 2 == 0), `test_shadow_phase11.py::test_next_run_failure_is_isolated` (NEXT_RUN_FAILED not logged) |
| `nightly-e2e.yml` | last 3 scheduled runs failure, ~9-10m each |
| `qa-deep-assurance.yml` | last 5 runs failure, ~3-4m each |

## 7. Transition outcome + administrator actions (review round 1, 2026-11-06)

Execution policy is now: **Windows primary and mandatory; Wine and macOS are
compatibility platforms, weekly + dispatch, never blocking.**

| Line | Execution policy |
| :--- | :--- |
| Native Windows MT5 IPC | Every PR + push via ci.yml → `mt5-native` (workflow_call); fail-closed member of `Full Runtime Overall Verdict`. **VERIFIED IN REAL CI**: run 42265803091 — Authenticode Valid, terminal build 6306, `mt5.initialize()` tiered attach (A credentialless ×3 → -10005; B pre-launched ×2 → -10005; **C startup-config → OK**), `mt5.version()`=(500, 6306, '6 Nov 2026'), `terminal_info() connected=True`, `DirectMT5Adapter.connect()` PASS. The discovered failure mode (fresh headless installs never reach IPC-ready credentiallessly) is pinned by `tests/ci/test_mt5_native_cert_contract.py` (7 tests). |
| Wine | `mt5-wine-compat.yml` only — weekly cron + dispatch; the ci.yml job was REMOVED from the mandatory verdict. Wine never affects PRs, the verdict, or releases. First independent run: PASS 6m38s (run 42262434360). |
| macOS | `tests-macos-compat.yml` only — weekly cron + dispatch, advisory. tests-os.yml keeps a transitional ALWAYS-SKIP-REPORT macOS leg solely to emit the context name branch protection still requires. |
| Release gate | `REQUIRED_RELEASE_RUNTIME_CHECKS` = Full Runtime Overall Verdict + DB Fabric Certification; Wine never required directly or indirectly. |

### Administrator migration bundle (repository settings — NOT executed by this task)

Branch protection currently requires `Py Tests (macos-latest)` and does not
require the runtime verdict. One authorized protection edit migrates both:

1. **ADD** required status check context: `Full Runtime Overall Verdict`
   (this makes the fail-closed provider + native Windows verdict a PR merge
   gate — review item 5).
2. **REMOVE** required status check context: `Py Tests (macos-latest)`
   (its real coverage moved to tests-macos-compat.yml, advisory).
3. Immediately after: delete the transitional macOS leg from tests-os.yml,
   remove `"Py Tests (macos-latest)"` from
   `scripts/release/release_auth_gate.py:REQUIRED_MAIN_CI_CHECKS`, regenerate
   `artifacts/forensics/branch_protection_required_checks.json`, and update
   `tests/unit/test_release_auth_gate.py::test_required_list_matches_live_branch_protection`
   — the drift test stays truthful at every point in the sequence (it compares
   the gate tuple against the LIVE protection snapshot).

Until step 2 happens, the transitional leg is a ~15s no-op report and the
release gate still honors the live protection contract.

## 8. Performance baseline (before) — see PR description for after

- PR wall 9m55s median; quality job 550s median (critical path).
- Main-push Actions cost ~119 billable min (tests-os macOS 10× dominates).
- Per-PR ubuntu runner-minutes ~30.3 (no paid multipliers on PRs).
