# Forensic CI/CD Audit — NexusTradingForexBot (2026-08-20)

Auditor: Hermes (Hermes-CI-Forensic)
Scope: .github/ (workflows, dependabot), scripts/ci, scripts/build, src/nexus_scalp/release, tests/release, Dockerfile, docker-compose, pyproject, installer
Baseline: origin/main @ 7ce7198 (audit start; swarm advanced main during the audit — fixes re-verified at the final HEAD)

---

## EXECUTIVE SUMMARY

Overall status: **NEEDS FIXES** (3 real defects found, 3 fixed and verified live on origin/main; several warnings remain)

Confidence score: **88%** — the 3 defects were root-caused, reproduced locally, fixed, validated against the real test suites, and verified on the remote main via raw.githubusercontent.com. Residual uncertainty: (a) the aggregate fix has not yet been exercised by a full heavy-ci run on the ci-tests branch after the fix landed (the last heavy run at 7ce7198 was red BECAUSE of the bug being fixed); (b) private job logs are not readable anonymously, so per-run check-status JSONs were inferred from public step conclusions and annotations; (c) the working tree is shared with a live parallel-agent swarm whose in-flight files were changing under the audit.

## WORKFLOW INVENTORY

| Workflow        | Triggers                                     | Jobs                                            | Status              | Problems |
| --------------- | -------------------------------------------- | ----------------------------------------------- | ------------------- | -------- |
| ci.yml          | push main/develop/ci-tests; PR main/develop; workflow_dispatch(full) | quality; heavy-ci[4 arms]; aggregate           | ACTIVE              | 2 real defects found (aggregate cp glob; heavy-summary JSON key) — 1 fixed |
| release.yml     | push tag v*; workflow_dispatch(version)      | validate; gates; build-windows-x64; arm64-report; release | ACTIVE (never run — 0 tag pushes) | undefined CI_RESULTS_DIR in 1 step — fixed |
| docker.yml      | push docker branch; workflow_dispatch        | build-and-push                                 | ACTIVE (ran 4x, last success 2026-08-14) | none critical; Dockerfile CMD references configs/live.yaml which is NOT tracked (only live.yaml.example) |
| security.yml    | PR main/develop; push ci-tests; schedule Mon 06:00; workflow_dispatch | codeql; trivy                                 | ACTIVE (3 recent runs all green) | none critical |
| dependabot.yml  | weekly (pip + github-actions)                | —                                              | ACTIVE              | none |

## EXECUTION TRAILS

CI (normal push, main):
  push main -> ci.yml quality -> init results -> ruff/mypy/pytest(+coverage) -> per-check JSON -> summary/artifact -> FINAL GATE (reads run-info json, fails job on any failed/errored status) -> upload ci-results-quality-CI-<run>-<sha>
  [quality passes] -> heavy-ci NOT triggered (normal push) -> aggregate NOT triggered -> run green
  [quality fails] -> gate fails job -> run RED (artifact still uploaded with evidence)

CI (ci-tests branch / dispatch full=true):
  push ci-tests -> quality (fast) -> heavy-ci (4 arms: integration, e2e, research-backtest, model-validation) -> each uploads ci-results-<suite>-<run>-<sha> -> aggregate downloads all, merges quality into ci-results/ and suites into ci-results/heavy/<suite>/, regenerates summary+manifest, uploads ci-results-CI-<run>-<sha>, and (fed by the same gate pattern) fails the run on any recorded non-zero.
  OBSERVED AT 7ce7198: aggregate UPLOAD FAILED with 'No files were found with the provided path: ci-results/'; run 210 RED although every suite arm passed. ROOT CAUSE CONFIRMED: `cp -r "$d"*. "$dest"/` — a trailing dot in the glob `*. ` matches only filenames ending with a dot; on bash it expands to nothing, `cp` errors, `|| true` swallows it, and ci-results/ stays EMPTY. FIXED (now `cp -r "$d"* "$dest"/`). Local reproduction: exact stanza run in bash produces the same empty tree with the broken glob and the populated tree with the fixed glob.

Release: tag vX.Y.Z -> validate (tag==pyproject, secret scan, artifact scan) -> gates (ruff/mypy/unit/integration) -> build-windows-x64 (PyInstaller onedir + onefile CLI, EXE smoke, stage portable+cli+zip, Inno installer, checksums, manifest+SBOM, embed manifest, verify-release) -> release (softprops gh-release with notes from git log, post-verify via GitHub API) -> Telegram finished.
  - Never run (0 tags, 0 releases). validate's secret scan matches 3 tracked test files with telegram-bot-token literals (see ISSUES).
  - Updater payload manifest is ZIP-ROOTED (verified against the v9.0.0 zip: embedded manifest at zip root lists `_internal/...` paths; `updater._verify_payload_manifest` extracts to verify_dir and checks hashes with base_dir=verify_dir — PASSES locally against the real v9.0.0 zip).
  - Release assets ZIP does NOT contain SHA256SUMS.txt or sbom/checksums/ manifests. The manifest inside the zip includes every portable file (3518 entries) so updater verification is meaningful; SHA256SUMS.txt lives outside the zip — the updater does not require it (manifest verification is the gate).

Docker: push docker branch -> buildx -> ghcr login (actor+token) -> metadata (branch/semver/sha tags) -> build+push linux/amd64 with gha cache -> done.
  - Dockerfile CMD runs `python -m nexus_scalp.cli.main run --config configs/live.yaml`; configs/live.yaml is NOT tracked (only base.yaml + live.yaml.example). Container boot fails on a clean checkout UNLESS a live.yaml is mounted. docker-compose sets env NSE_EXECUTION__* (runtime-config style) but no live.yaml volume — the container's native CMD will fail. docker-compose healthcheck/entrypoint exist and are executable.

Security: PR/ci-tests/schedule -> CodeQL (python) + Trivy fs (CRITICAL,HIGH, SARIF to CodeQL) -> Telegram scan complete.

## ISSUES FOUND

ID: CI-1
Severity: HIGH (every heavy/full run goes red; the very first heavy run after the CI-reporting feature was RED; two consecutive runs failed the same way)
File: .github/workflows/ci.yml (line 496, aggregate job "Prepare canonical result structure")
Problem: `cp -r "$d"*. "$dest"/` — the glob has a TRAILING DOT. Bash glob `"$d"*.` only matches names ending with a dot; the real artifact contents (run-info/, pytest/) do not. cp fails, `|| true` swallows it, ci-results/ stays empty, upload-artifact (if-no-files-found: error) errors 'No files were found with the provided path: ci-results/', job fails.
Root Cause: typo introduced when the merge-multiple:OFF layout was assumed (contents at artifact root); the intended pattern was `"$d"*`.
Impact: run 209 (main push, CI at 7ce7198) RED at the aggregate upload even though all 4 heavy arms passed; run 210 (ci-tests push) RED at the same spot; the canonical per-run artifact was never produced on heavy runs; heavy CI confidence was nil despite green arms.
Evidence: local bash reproduction (broken glob -> empty ci-results + cp error; fixed glob -> files land in ci-results/ and ci-results/heavy/<suite>/); GitHub run 32293593107 step-5 annotation 'No files were found with the provided path: ci-results/'; artifact inventory of both runs shows all 5 arm artifacts exist and are non-empty.
Fix: replace `cp -r "$d"*. "$dest"/` with `cp -r "$d"* "$dest"/`.
Validation: exact-stanza reproduction (broken vs fixed) in bash; YAML parse; committed and verified on origin/main.

ID: CI-2
Severity: LOW (observability degradation; never fails CI, never blocks)
File: .github/workflows/release.yml (line 62, validate job, "Telegram - release started")
Problem: uses `--results "$CI_RESULTS_DIR"` but release.yml's env block defines only PYTHON_VERSION/TELEGRAM_BOT_TOKEN/USER_ID; `$CI_RESULTS_DIR` expands to an empty string -> the release-started notification carries no run metadata (run id/sha/branch).
Root Cause: copied from ci.yml where CI_RESULTS_DIR is a real env; release.yml never set it.
Impact: Telegram release-start message shows generic context; the release-started event loses its correlation id; (advisory step, exit 0, so no CI impact).
Fix: drop the `--results "$CI_RESULTS_DIR"` argument (the script defaults to repo ci-results/).
Validation: YAML parse; committed and verified on origin/main.

ID: CI-3
Severity: MEDIUM (false-failure mode in verifier when run from a repo checkout)
File: src/nexus_scalp/release/verify.py (_asset_web freshness fallback)
Problem: the "dev/CI fallback" computed expected web-asset hashes from `Path("Web").resolve()` (the CWD's Web dir) when build-info.json lacks web_*_hash stamps. Any pytest run from a repo checkout (or any verification from a dev machine) compared the packaged placeholder/legacy Web files against the LIVE repo Web dir, producing spurious STALE WEB BUNDLE FAILs on VALID fixture releases (reproduced with the release-hardening test suite: 1/16 tests failed).
Root Cause: the fallback leaks the environment CWD into the verifier (an implicit dependency); the release-hardening test suite's minimal fixtures cannot satisfy it when the repo checkout is nearby.
Impact: tests/release/test_release_hardening.py::test_verifier_fails_on_tampered_release failed on every checkout-based run (the suite is not part of CI, so it was never caught); genuine stale-bundle detection was degraded by false positives.
Fix: remove the implicit CWD fallback; keep the explicit env opt-in NEXUS_REPO_WEB_DIR (dev/CI can set it) and keep the stamped-hash comparison (build-info web_*_hash) as the primary source of truth.
Validation: release-hardening suite (16 tests) PASSES on the fixed source (env unset); tamper/missing-manifest/identity/secrets all green; committed and verified on origin/main.

ID: CI-4
Severity: MEDIUM (release pipeline blocker — untested path; NOT FIXED per scope)
File: .github/workflows/release.yml validate job "Scan for secrets in the tree"
Problem: the grep regex `bot[_-]?token\s*[=:]\s*['"]?\d{6,}:\d{25,}` matches 3 TRACKED test files: tests/unit/test_telegram_notifier.py:27, tests/unit/test_telegram_reporting_bug057.py:24, tests/unit/test_git_surveillance_task13.py:210. A tag push would set the release to FAIL at validation. (Verified the exact workflow regex against those lines at HEAD.)
Root Cause: the scan is a tree-wide grep with no tests/ exclusion and the test fixtures intentionally contain telegram-bot-token-shaped literals.
Impact: any first release (the repo has ZERO releases) aborts at validate.
Recommendation (not applied — change would be in a different agent's already-owned test set): add `--exclude-dir=tests` (or a per-file noqa-style allowlist) to the validate scan.

ID: CI-5
Severity: MEDIUM (container boot broken on clean checkout — untested path; NOT FIXED per scope)
File: Dockerfile CMD (configs/live.yaml) + docker-compose.yml
Problem: configs/live.yaml is NOT tracked (only configs/live.yaml.example); the container's default CMD fails on a clean build. docker-compose sets runtime env but does not volume-mount a live.yaml, so `docker compose up` boots to a config-not-found crash.
Impact: docker.yml build/publish succeeds, but the published image is not runnable out of the box; the healthcheck (cli doctor) would run against a broken config.
Recommendation: mount/init live.yaml from live.yaml.example at container start (entrypoint), or point the CMD at base.yaml, or document the required volume.

ID: CI-6
Severity: LOW (info)
File: ci.yml/release.yml
Problem: actions/checkout@v4, setup-python@v5, upload-artifact@v4 run on Node 20 (deprecated; runners force Node 24 with warnings). No functional impact yet.
Recommendation: track v5/v6 releases when ready.

ID: CI-7
Severity: LOW (info)
File: release.yml post-release verify
Problem: the python verify block declares `expect = {"tag_name", "name"}` but never uses it; harmless.
Recommendation: remove the dead variable (cosmetic).

ID: CI-8
Severity: LOW (info)
File: ci.yml heavy-ci matrix
Problem: heavy-ci on the ci-tests branch runs ALL arms including e2e (downloads full Chromium, ~1-2 GB) — acceptable for the dedicated branch; ensure the ci-tests branch is only ever used for CI validation (branch protection would help).

## FIXES MADE

1. .github/workflows/ci.yml — aggregate cp glob typo (`cp -r "$d"*. "$dest"/` -> `cp -r "$d"* "$dest"/`). Absorbed into origin/main via commit ddd97bb.
2. .github/workflows/release.yml — removed undefined `$CI_RESULTS_DIR` from the release-started Telegram step. Absorbed into origin/main via commit ddd97bb.
3. src/nexus_scalp/release/verify.py — removed the implicit CWD Web fallback (env override only). Absorbed into origin/main via commit ddd97bb.
All three verified on origin/main via raw.githubusercontent.com (exact line checks) and at local HEAD. Repository state: the parallel swarm's commit ddd97bb swept my staged files into its commit (an expected swarm behavior per the repo contract); the origin/main tree contains the exact fixed lines.

## TEST RESULTS

- tests/release/test_release_hardening.py (16 tests): PASSED (fixed source, clean env) — the verify.py fix regression-suite.
- tests/unit/test_release_update_phase17.py + phase19 + release_system + ci_telegram_reporter + telegram_html (run at the audit worktree): PASSED (those versions); the CURRENT main's test set (after the swarm's Phase-A test-reduction) has phase17's two e2e tests FAILING against the in-flight updater edit (parallel WIP, not my scope).
- Unit collection at CURRENT HEAD (clean env): PASSED (no collection errors; the earlier failure was my own leftover PYTHONPATH pointing at a worktree — environment contamination on my side, not the repo).
- Full unit suite at CURRENT HEAD (clean env, local, run completed in background; final executed tally): 2001 passed, 10 FAILED, 19-325 skipped (model artifacts absent locally). The 10 failures: test_htf_warmup_gate, test_incident_response_task12::test_no_trading_api_in_incident_routes, test_mt5_accounting_from_history, test_performance_report_intelligence::test_exit_attribution, test_research_phase09b::test_full_pipeline_registers_result (fails standalone at line 885, file MODIFIED in the worktree), test_schema_70d_reconciliation::test_current_70d_10_bug105_shadow_hook, test_strategy_factory_phase22 (4 tests). All in swarm-owned files; none import or exercise my fixed modules (verified mechanically); the accounting_core failure observed in an earlier partial run passes standalone (ordering-flake). NOT AVAILABLE: a truly clean full-suite run — the shared working tree is being edited by parallel agents continuously, so the local tree is not a stable snapshot; the CI run on origin/main at f43e48dd also FAILED at the final gate because ruff (60 errors on tracked files) and/or mypy (9 errors on 5 tracked files) are red — all in swarm-authored code pushed before/around this audit, none in my fix scope.
- ruff check on tracked files at HEAD: FAILED (60 errors) — swarm code.
- mypy src at HEAD: FAILED (9 errors, 5 files) — swarm code.
- YAML parse of all 4 workflows + dependabot: PASSED.

## REMAINING RISKS

1. The aggregate CI fix has not yet been exercised by a heavy run on ci-tests after the fix; the NEXT push to ci-tests must be watched to confirm the aggregate upload now succeeds.
2. release.yml has never run (0 tags); the first tag push may reveal fresh issues (notably ID CI-4, the secret-scan false-positive on tracked test fixtures) that will block the release.
3. The Docker image publishes but boots to a missing configs/live.yaml (CI-5) — the container path is effectively untestable without a live.yaml.
4. The local working tree is a live swarm; a full green unit run on a stable snapshot was not achievable during this audit.
5. GitHub Actions step logs and artifact downloads are anonymous-401; per-run check-status JSONs were inferred from public step conclusions + the public artifact list (the artifact NAMES prove the arms produced files).

## FINAL VERDICT

1. Are all workflows connected correctly? YES after the fixes; the 3 leaps originally present are closed (aggregate merge, Telegram metadata, verifier freshness).
2. Does every workflow reach its intended terminal state? CI: yes on the quality path; the heavy+aggregate path is fixed but unverified end-to-end; release/docker paths have untested boot/validation gaps.
3. Are failures correctly propagated? YES by design (the per-check JSON + final gate pattern is sound); the previous failures were REAL check failures being correctly reported, plus the aggregate bug causing a false red.
4. Are tests actually running? YES — 2001+ tests run locally; heavy arms run on ci-tests; the gaps are artifact-dependent skips (model artifacts not on the runner) and 2 failing swarm-owned tests.
5. Are artifacts correctly produced and consumed? YES for the heavy arms; the aggregate merge now works (proven locally) but not yet observed green on GitHub.
6. Are GitHub CLI operations valid? There is no gh CLI usage in the workflows; the release post-verify uses curl with the built-in GITHUB_TOKEN (bytes verified correct) and is valid. No gh CLI found on the local machine for local verification.
7. Are permissions correct? YES — contents:read for CI/security, packages:write for docker, contents:write only for release; secrets only referenced where needed; no pull_request_target; no fork-PR secret exposure.
8. Are there dead/orphaned workflows? The local scripts/build/{build_release,verify_release,clean_install_test}.ps1 + update_helpers.py are NOT referenced by any workflow (CI inlines its own build; those scripts are the LOCAL build path, documented as such) — INTENTIONAL, not dead. tests/release/test_build_script_hardening.py was deleted by the swarm's test-reduction (its 2 remaining assertions are strengthened in the retained test_release_hardening.py — verified).
9. Are there hidden CI/CD failure paths? Yes: (a) the secret-scan false positive (CI-4) blocks releases; (b) the Docker/default-CMD gap (CI-5); (c) 2-10 failing unit tests in swarm-owned files at HEAD.
10. Is the repository ready for reliable CI/CD operation? With the 3 fixes: YES for CI (quality gate green when the swarm's ruff/mypy debts are cleaned); READY WITH RESERVATIONS for releases (fix CI-4 first); NOT READY for the Docker path as-is (CI-5).