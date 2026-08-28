# NexusTradingForexBot (NSE) — CI Architecture

Concise map of the self-defending CI system for NSE. This document describes
the **actual** repository architecture (pure Python + vanilla-JS Web + Docker,
primary runtime Windows/MT5). It does **not** import foreign workflows: there
is no C#/.NET source (the lone `NexusTradingForexBot.slnx` only references a
`.pyproj` Python project) and no native C++ outside `.venv`, so no `.NET` or
`native` CI lanes exist. See `agents/skill.md` for the full architecture map;
this file is the CI-specific companion.

## Workflow map

| Workflow | Trigger | Purpose | Lanes touched |
| :--- | :--- | :--- | :--- |
| `ci.yml` | push/PR main·develop·ci-tests; weekly cron; dispatch | Quality gate (ruff/mypy/pytest critical suite) + **CI self-integrity** + review-status | python, web, ci |
| `js-tests.yml` | push/PR main·develop·ci-tests; dispatch | Vanilla-JS syntax gate + node unit tests | web/js |
| `tests-os.yml` | push/PR main·develop·ci-tests; dispatch | Critical suite on Windows + macOS (not Ubuntu — covered by ci.yml) | python (cross-OS) |
| `security.yml` | PR main·develop; push ci-tests; weekly cron; dispatch | CodeQL (python) + Trivy fs scan | security |
| `osv-scanner.yml` | PR main·develop; push main; weekly cron; dispatch | Python dependency CVE scan (advisory) | security/deps |
| `lockfile-diff.yml` | PR main·develop; dispatch | Dependency manifest diff report (advisory) | deps |
| `docker.yml` | push `docker` branch; dispatch | Build + publish GHCR image | docker |
| `release.yml` | `v*` tags; dispatch | validate → quality gates → windows build/installer → publish | release/python |

## Lane architecture (canonical)

Change classification is **centralized** in `scripts/ci/classify_changes.py`.
Every job that needs to know "what changed" calls that ONE script instead of
re-deriving globs. Lanes:

```
python  - src/, tests/ (non-js), configs/, pyproject/requirements, *.py
web/js  - Web/, tests/js/
docker  - Dockerfile, docker-compose.yml, docker/
ci      - .github/workflows, scripts/ci, beforePush.ps1
deps    - pyproject.toml, requirements.txt, uv.lock
scripts - scripts/*.ps1|*.sh (non-ci, non-build)
release - installer/, release/, src/nexus_scalp/release/
docs    - docs/, agents/*.md, README.md
```

A file may map to multiple lanes. `docs_only` is derived so a doc-only PR does
not trigger the heavy Python gate. Unknown/missing file types are **fail-safe**:
they do NOT map to nothing that would let a gate think "docs_only", so the
workflow default (run Python) still fires.

## CI self-integrity (new)

`scripts/ci/check_workflows.py` statically analyzes every workflow in
`.github/workflows` and is wired as the first job (`ci-integrity`) in `ci.yml`.
It fails CI on:

* **Undefined job outputs** — a step references `needs.<job>.outputs.<name>`
  but `<job>` never declares that output (the exact "emits lane / never exposes
  lane" class of bug).
* **Local composite action without checkout** — a job using
  `./.github/actions/*` with no prior `actions/checkout` (jobs are isolated).
* **Matrix artifact collisions** — a matrix job uploading an artifact name with
  no matrix dimension (upload-artifact v4 rejects duplicates).
* **Silently-skipped required gates** — empty or statically-false `if:`; an
  `if:` reading an undeclared lane output.
* **Self-watching pollers** — a step polling run state via `GITHUB_RUN_ID`
  must use an explicit `TARGET_RUN_ID`, never its own run id.

The classifier is also exercised in `ci-integrity` so the same formula the jobs
branch on is validated on every run.

## Required checks (branch protection candidates)

* `ci.yml :: ci-integrity` — workflow wiring must stay valid.
* `ci.yml :: quality` — ruff lint/format, mypy, pytest critical suite, coverage.
* `js-tests.yml :: js-tests` — Web JS syntax + unit tests.
* `tests-os.yml :: tests-os` (windows-latest) — OS-parity for the critical suite.
* `security.yml :: codeql` + `trivy` — advisory, but should be green.

## Optional / advisory checks

* `osv-scanner`, `lockfile-diff` — dependency visibility, never blocking.
* `security.yml` scheduled run — weekly drift.
* `docker.yml` — only on the `docker` branch, never on normal dev.
* `tests-os.yml` macOS leg — parity, non-blocking for Windows primary.

## Windows / cross-platform coverage

* `tests-os.yml` runs the **critical suite on `windows-latest`** (the primary
  runtime is the packaged Windows EXE + `beforePush.ps1`). This catches
  path/CRLF/encoding regressions that an Ubuntu-green run would mask.
* `release.yml` builds the Windows PyInstaller onedir/onefile + Inno Setup
  installer and runs EXE smoke tests (`version`, `health --json`).
* `beforePush.ps1` is the local mirror of `quality` (fast critical local gate);
  it is NOT re-run in CI but parity is intentional (see below).

## ML / model contract coverage

* Feature-schema registry (`src/nexus_scalp/features/schema.py`) is the single
  source of truth for `scalp_v1` (50D, ACTIVE) and forward-declared candidate
  schemas (`scalp_v2` 60D, `scalp_v3`/`scalp_v4` 70D). Assertions reject silent
  dimension drift: `validate_vector`/`validate_columns` raise on arity mismatch.
* The **critical suite already gates**:
  * `test_70d_model_validation_task4.py`, `test_schema_70d_reconciliation.py`,
    `test_70d_contract_parity_task3.py`, `test_70d_bug106_incremental_phase19.py`
    — 70D tensor/feature-contract invariants.
  * `test_model_lifecycle_phase10.py`, `test_model_governance_phase16.py`,
    `test_model_benchmark_phase13b.py` — model lifecycle/governance gates.
  * `test_regime_calibration_bug132.py` — **added to the critical suite in this
    task**: deterministic regime-classifier regression (RANGING_MEAN_REVERSION,
    TRENDING_MOMENTUM, VOLATILITY_EXPANSION, HIGH_SPREAD_CHOP, MACRO_NEWS_FREEZE),
    decoupled `tick_velocity` (context field, not volatility proxy), and the
    hysteresis absorbing-state fix.
  * These run on every PR via `ci.yml` (critical suite) and the heavier model
    validation arm in `ci.yml :: heavy-ci` (ci-tests / dispatch full).

## Accounting / trading coverage

The **critical suite** now also gates:

* `test_accounting_core.py`, `test_accounting_advanced_metrics.py`,
  `test_accounting_hedging.py` — PnL aggregation, drawdown, hedging.
* `test_accounting_deduplication.py` — duplicate-trade protection (added here).
* `test_accounting_pnl_regression.py` — PnL regression invariants (added here).
* `test_accounting_timezone.py` — UTC day/week/month/year period boundaries and
  timezone correctness (added here).
* `test_risk_engine.py`, `test_order_lifecycle.py`, `test_order_manager_exit_bugs.py`
  — position sizing, risk limits, SL/TP/breakeven/trailing exit logic.

These previously could be skipped if the critical manifest drifted; they are now
an explicit required regression net.

## Risk / execution gates

`test_risk_engine.py` (dynamic lot sizing, margin clamping, `HARD_MAX_LOTS`),
`test_execution_architecture.py`, `test_order_lifecycle.py`,
`test_order_manager_exit_bugs.py` are in the critical suite, covering position
sizing, stop-loss/take-profit/breakeven/trailing, and execution-mode paths.

## Security gates

* `security.yml` — CodeQL (python) + Trivy filesystem scan (CRITICAL/HIGH),
  both pinned to immutable commit SHAs. SARIF uploaded to code-scanning.
* `osv-scanner.yml` — Python dependency CVE scan (advisory SARIF).
* `release.yml :: validate` — secret-shaped-string scan + dev-artifact check.
* **Workflow security**: every workflow uses `permissions: read-all` (or the
  narrowest needed scope — `packages: write` for docker publish, `contents:
  write` for releases); no `pull_request_target`; third-party actions pinned to
  SHAs (`docker.yml`, `security.yml`, `release.yml`).

## Artifact / status conventions

* `ci.yml :: quality` emits a single canonical `ci-results/` artifact (one run =
  one clean tree) plus a machine-readable `review-status.json`
  (`{job, status, workflow, run_id, sha, timestamp, details[]}`) written as the
  job completes, so aggregators/PR comments merge status without reassembling
  mutable JSON at workflow startup (`tests-os.yml` / `heavy-ci` mirror this via
  distinct artifact-name suffixes keyed by run number + sha).
* Artifact names embed `${{ github.run_number }}-${{ github.sha }}` to prevent
  cross-run collisions.

## Cache policy

* Python installs use `setup-python` pip cache keyed on `pyproject.toml` +
  `requirements.txt`. No large directory caches are introduced without evidence.
* The analyzer audits (and the docs state) that a cache must never make an
  incompatible dependency graph appear valid.

## Scheduled drift detection

* `ci.yml` weekly cron (Mon 05:13 UTC) re-runs the integrity scan so a hand
  edit to a workflow, an action SHA bump, or a base-image/linter shift is
  caught even without code changes.
* `security.yml` + `osv-scanner.yml` weekly crons catch dependency-world drift.
* None of these block normal PRs.

## CI invariants (regression-tested)

`tests/ci/test_classify_changes.py` and `tests/ci/test_check_workflows.py` lock
the infrastructure itself:

* classifier maps each language area to the right lane (and unknown files fail
  safe, not silent-skip).
* analyzer FAILS on undefined job outputs, local actions without checkout,
  matrix artifact collisions, empty/always-false `if:`, and YAML parse errors —
  and PASSES on correct input.
* self-watch poller guard detects `GITHUB_RUN_ID` polling but ignores harmless
  metadata use.

## beforePush.ps1 ↔ CI parity

`beforePush.ps1` is the local mirror of `ci.yml :: quality` (ruff lint/format,
mypy, critical-suite pytest, `ci-results/` tree, self-test). It is intentionally
fast (critical suite, RAM-aware xdist) so developers catch the same failures
before pushing; the full authoritative gate is CI. Parity is by design, not
duplication.

## What was deliberately NOT added

Per the audit, the following were rejected as not corresponding to NSE:

* **.NET / C# CI lane** — no `.cs`/`.csproj` source exists.
* **Native C++ CI lane** — no first-party C++ (only vendored `.venv` headers).
* **Rust / Nix / Docusaurus / Electron lanes** — absent from the repo.

Adding any of these later requires first adding the corresponding source and
then a lane to `scripts/ci/classify_changes.py` + a workflow job.
