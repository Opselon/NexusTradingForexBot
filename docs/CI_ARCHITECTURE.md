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
| `ci.yml` | push/PR main·develop·ci-tests; weekly cron; dispatch | Quality gates (lint/types ∥ tests, split 2026-11 under one required verdict) + runtime provider matrix + DB fabric + **CI self-integrity** + review-status | python, web, ci |
| `mt5-native-windows.yml` | `workflow_call` (from ci.yml); dispatch | **Native Windows MT5 certification** (primary runtime platform): Authenticode-verified installer + real `mt5.initialize()` IPC + `DirectMT5Adapter` connect; demo (Level 2/3) legs are dispatch-only | python/runtime |
| `mt5-wine-compat.yml` | weekly cron + dispatch ONLY | Wine compatibility soak (independent, advisory — never gates PRs, the runtime verdict, or releases) | python/compat |
| `js-tests.yml` | push/PR main·develop·ci-tests; dispatch | Vanilla-JS syntax gate + node unit tests | web/js |
| `tests-os.yml` | push/PR main·develop·ci-tests; Monday cron; dispatch | **Linux critical suite, self-hosted-first with hosted fallback** (runner-routing architecture); emits the required Windows/macOS contexts as NOT-EXECUTED routing statuses | python (Linux) |
| `runner-select` (composite action) | invoked from each lane's preflight job | Centralized runner-availability preflight and class selection | ci |
| `tests-windows.yml` | `workflow_dispatch` ONLY | **Manual Windows suite** — full critical suite + Windows-skew lane, self-hosted-ready (`ENABLE_WINDOWS_SELF_HOSTED`) | python (Windows) |
| `tests-macos.yml` | `workflow_dispatch` ONLY | **Manual macOS suite** — full critical suite, self-hosted-ready (`ENABLE_MACOS_SELF_HOSTED`) | python (macOS) |
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
* **Unpinned / phantom action refs** (`action-pin-shape`, `action-pin-phantom`)
  — every third-party `uses:` must be a full 40-hex commit SHA, never a tag or
  branch name, and never one of the SHAs in the `PHANTOM_PINS` ledger. The
  #139 wave "pinned" `astral-sh/setup-uv@94527f2e… # v7.6.0` and
  `…@e58605a9… # v5.4.2` (those are *annotated-tag objects*, not commits) and
  `actions/upload-artifact@d3f86a10…` (a download-artifact SHA). All three are
  valid hex, so shape alone cannot catch them — hence the ledger.

  **The scanner stays offline and deterministic by design.** It enforces shape
  and blocks known phantoms only. Proving a well-formed SHA is the *correct
  commit for its advertised tag* is a mandatory MANUAL audit on every pin
  change:
  `git ls-remote https://github.com/<owner>/<repo> 'refs/tags/<v>*'` and pin
  the peeled `^{}` object (a tag ref with no `^{}` line is lightweight and is
  itself the commit). Dependabot PRs are not exempt: their pin comments can
  mislabel too.

The classifier is also exercised in `ci-integrity` so the same formula the jobs
branch on is validated on every run.

## Required checks (branch protection candidates)

* `ci.yml :: ci-integrity` — workflow wiring must stay valid.
* `ci.yml :: quality` — ruff lint/format, mypy, pytest critical suite, coverage.
* `js-tests.yml :: js-tests` — Web JS syntax + unit tests.
* `tests-os.yml :: tests-linux-*` — Linux critical suite (self-hosted first).
* `security.yml :: codeql` + `trivy` — advisory, but should be green.

> **Branch-protection migration (administrator action, section below).**
> `main` currently requires the contexts `Py Tests (windows-latest)` and
> `Py Tests (macos-latest)`. Under the runner-routing architecture those two
> names are emitted by `tests-os.yml` as explicit **NOT-EXECUTED routing
> statuses** (Windows/macOS are manual-only). A GitHub-skipped job is treated
> as "Success" for protection purposes, so PRs still merge — but the ideal
> end state is to remove both names from protection and rely on the manual
> lane evidence in `scripts/release/release_auth_gate.py`, which already
> refuses a release whose SHA carries no green Windows lane.

## Runner routing architecture (2026-11)

Centralized, single-source-of-truth runner selection. No workflow hard-codes
a runner decision; every lane calls the same reusable preflight.

```
GitHub event (push / PR / schedule / dispatch)
        │
        ▼
  tests-os.yml  ──►  runner-select (composite action,
                         run on a GitHub-hosted preflight job)
                         │  scripts/ci/runner_select.py
                         │  GET /repos/{o}/{r}/actions/runners
                         ▼
                 runner_class = self-hosted | github-hosted
                         │
        ┌────────────────┴────────────────┐
        ▼                                 ▼
  tests-linux-self-hosted          tests-linux-hosted
  runs-on: [self-hosted,Linux,X64] runs-on: ubuntu-latest
        │                                 │
        └──────────┬──────────────────────┘
                   ▼
        Windows / macOS: manual only
        tests-windows.yml / tests-macos.yml (workflow_dispatch)
```

### Components

| Component | Role |
| :--- | :--- |
| `scripts/ci/runner_config.py` | Centralized labels, hosted fallbacks, enablement-flag defaults, cache namespace. `--snippet` emits the YAML env fragment. |
| `scripts/ci/runner_select.py` | Availability preflight + runner-class decision. Pure `select()` function; fail-open to github-hosted on ANY error. |
| `.github/actions/runner-select` | Composite action: availability preflight + class decision, used by every lane's preflight job. (A reusable *workflow* cannot declare workflow-level `outputs:` in this repository — verified by experiment — so the shared component is an action.) |
| `scripts/ci/decide_os_lane.bash` | Shared FULL / SKEW / SKIP-REPORT lane logic used by both Linux legs and both manual platform workflows. |
| `.github/workflows/tests-windows.yml` | Manual Windows suite (full coverage preserved) + `ENABLE_WINDOWS_SELF_HOSTED` flag. |
| `.github/workflows/tests-macos.yml` | Manual macOS suite (full coverage preserved) + `ENABLE_MACOS_SELF_HOSTED` flag. |

### Linux — self-hosted first, hosted fallback

* Primary: the repository runner matching **`self-hosted,Linux,X64`**,
  preferring an **online and idle** machine.
* Fallback to `ubuntu-latest` when the runner is offline, busy, disabled,
  unconfigured, when the API errors, when the credential is missing, or when
  the run comes from an **untrusted fork PR** (fork PRs never execute on the
  persistent machine and never see the status credential).
* The preflight is a **hint, not a reservation**: another workflow may take
  the runner between the check and the assignment. That race is inherent to
  GitHub Actions (a job `runs-on` labels queue until a matching runner is
  idle; GitHub re-queues an unclaimed assignment after 60 s and fails it
  after 24 h). We mitigate by (a) checking at the last possible moment in a
  fast preflight, (b) never using `timeout-minutes` as a runner-assignment
  timer, and (c) documenting it instead of pretending to eliminate it.
* **Credential.** `NSE_RUNNER_STATUS_TOKEN` (a fine-grained PAT or GitHub
  App installation token with repository **Administration: read**) is the
  ONLY consumed secret, and only by the preflight. It is never printed,
  never forwarded to test scripts, and its absence fails open to the hosted
  fallback. The workflow's own `GITHUB_TOKEN` has no Administration
  permission and cannot read runner state.

### Windows — manual-only, self-hosted-ready

* **Automatic PR/push execution: removed.** `tests-os.yml` no longer
  schedules a Windows test leg; `ci.yml :: mt5-native` runs only on
  schedule/dispatch; `go-api.yml :: go-api-windows` runs only on
  schedule/dispatch.
* **Nothing was deleted.** The full Windows critical suite, the
  Windows-skew fast lane, the MT5 native IPC certification and the Go
  Windows leg are all intact and executable on demand.
* Manual entry: **Actions → `Tests (Windows, manual)` → Run workflow**
  (`tests-windows.yml`, `workflow_dispatch`). Inputs: `full_suite`
  (force the FULL critical suite) and `runner_class` (override:
  `auto` | `self-hosted` | `github-hosted`).
* Feature flag: repository variable **`ENABLE_WINDOWS_SELF_HOSTED`**
  (default **disabled**). Set it to `true` and the manual workflow
  automatically prefers an eligible `self-hosted,Windows,X64` runner with a
  `windows-latest` fallback — **no workflow edit, no redesign**.

### macOS — manual-only, self-hosted-ready

* Same architecture as Windows, flag **`ENABLE_MACOS_SELF_HOSTED`**
  (default **disabled**), labels `(self-hosted, macOS, ARM64)`, fallback
  `macos-latest`.
* **Architecture note (ARM64 vs x64).** `macos-latest` is Apple Silicon
  (ARM64). The self-hosted label set is deliberately ARM64 so both runner
  classes share ONE architecture — a verdict means the same thing on
  either. A self-hosted **Intel** Mac must instead be registered as
  `(self-hosted, macOS, X64)` with `macos-13` (the last x64 image) as its
  fallback; `macos-latest` would silently change the architecture under the
  suite. That is the single line to change in
  `scripts/ci/runner_config.py`.
* The former weekly `tests-macos-compat.yml` schedule is removed; its full
  critical suite is preserved in `tests-macos.yml` (dispatch).

### Fork-PR trust boundary

Fork PRs (`github.event.pull_request.head.repo.full_name != github.repository`)
pass `untrusted: true` to the preflight. The decision is then **pre-determined**
to `github-hosted` and **no credential is consulted**, so untrusted code never
runs on the persistent runner and never sees runner-management data.
`workflow_dispatch` requires repository write access, so the manual lanes
cannot be reached by a fork contributor either.

## Caching

| Lane | Cache | Notes |
| :--- | :--- | :--- |
| Self-hosted legs | `astral-sh/setup-uv` `enable-cache: true`, `actions/setup-python` pip cache | Namespaces are separated per OS/arch and per **trust boundary** (hosted jobs never restore a cache written from an untrusted fork run on the same OS). |
| GitHub-hosted legs | pip cache only | Ephemeral runners; the uv Actions cache is inert on a 20-minute runner, so it is deliberately NOT enabled there. |

Cleanup/invalidation stays bounded by `requirements.lock`
(`cache-dependency-glob`) and retention-days on artifacts.

## Observability

Every selection logs `runner_class`, `self_hosted_eligible` and a
secret-free `reason`, plus a step-summary table (requested platform,
requested labels, whether self-hosted was enabled, chosen class, reason,
untrusted flag). The reusable workflow emits a `::notice::` annotation so
the decision is visible in the PR Checks UI without opening the log.

## Optional / advisory checks

* `osv-scanner`, `lockfile-diff` — dependency visibility, never blocking.
* `security.yml` scheduled run — weekly drift.
* `docker.yml` — only on the `docker` branch, never on normal dev.
* `tests-windows.yml` / `tests-macos.yml` — manual platform lanes.

## Windows / cross-platform coverage

* `tests-windows.yml` (manual, `workflow_dispatch`) runs the **critical
  suite on Windows** (the primary runtime is the packaged Windows EXE +
  `beforePush.ps1`). This catches the path/CRLF/encoding regressions an
  Ubuntu-green run would mask. It is the same coverage the old automatic
  `tests-os.yml` Windows leg ran, preserved in full.
* `release.yml` builds the Windows PyInstaller onedir/onefile + Inno Setup
  installer and runs EXE smoke tests (`version`, `health --json`). Its
  Windows build leg is tag-triggered only, so it is unaffected by the
  manual-only routing.
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

## Threat model — runner trust boundaries

| Boundary | Trust level | Exposure | Controls |
| :--- | :--- | :--- | :--- |
| GitHub-hosted runners | **Untrusted** (ephemeral, multi-tenant) | Public image, no persistent state | Runs all untrusted fork-PR code; `permissions: contents: read` job-scoped; no runner-management credential ever reaches it |
| Linux self-hosted runner (`self-hosted,Linux,X64`) | **Trusted** (persistent, repo-owned) | Compromise persists across runs | ONLY same-repo PR/push code runs there (fork PRs are pre-routed to hosted); the status credential is read-only and consumed only by the preflight on a hosted VM; jobs run as the runner user with no Docker socket grant; workspace is cleaned per job |
| Windows self-hosted runner (`self-hosted,Windows,X64`) | Trusted (currently **disabled**) | Same as Linux, plus MT5 terminal state | Gated by `ENABLE_WINDOWS_SELF_HOSTED` (default false); reachable only via `workflow_dispatch` (write-access users) |
| macOS self-hosted runner (`self-hosted,macOS,ARM64`) | Trusted (currently **disabled**) | Same as Linux | Gated by `ENABLE_MACOS_SELF_HOSTED` (default false); dispatch-only |
| Fork-originated pull requests | **Untrusted code** | Arbitrary `runs:` steps | Never routed to any persistent runner; never receives `NSE_RUNNER_STATUS_TOKEN`; secrets marked unavailable by GitHub for fork runs anyway |
| Organization/owner repositories | Administrative control | Runner registration, protection rules | Runner registration requires owner admin; the release gate (`release_auth_gate.py`) independently verifies check evidence per SHA |

**`pull_request_target` review:** no workflow in this repository uses
`pull_request_target`. Untrusted PR code is executed only from the ordinary
`pull_request` event (secrets withheld), on GitHub-hosted runners — never on
a `pull_request_target` trigger that would expose the installation token to
untrusted code. This is enforced by `check_workflows.py`, which fails on any
trigger that grants write access alongside untrusted checkout.

## Known GitHub Actions scheduling limitations

1. **Runner availability checks are not atomic reservations.** The preflight
   observes state; it does not hold it. Between the check and the assignment
   another workflow may take the runner, or it may go offline. The
   self-hosted leg will then queue (GitHub re-queues an unclaimed assignment
   after 60 s and fails it after 24 h). We deliberately do NOT use
   `timeout-minutes` as a runner-assignment timer — it bounds the whole job,
   including a long test suite, not the wait for a runner.
2. **Schedule drift.** `cron` times are UTC and GitHub does not guarantee
   exact-minute execution under load; scheduled runs may be delayed or
   skipped entirely. Nothing here depends on a schedule for correctness.
3. **Concurrent workflows.** Two pushes in quick succession run
   `cancel-in-progress: true` groups, but a scheduled run and a push on the
   same ref can overlap and contend for the same self-hosted runner. The
   preflight makes the contention visible (reason: "no online+idle runner")
   and the hosted leg absorbs the overflow.
4. **Check staleness.** A required check must have completed within the past
   seven days on the chosen repository, and it must be on the latest commit
   SHA. At the time of writing, `Py Tests (windows-latest)` exists in branch
   protection only as a name — its last actual execution is historical. The
   release gate no longer relies on it.

## Troubleshooting

| Symptom | Meaning | Action |
| :--- | :--- | :--- |
| `Py Tests (linux, hosted)` ran instead of the self-hosted leg | The preflight found no eligible runner | Read the `selection_reason` in the run summary. Most common: runner offline, runner busy, or `NSE_RUNNER_STATUS_TOKEN` not set. |
| `Py Tests (windows-latest)` reports NOT EXECUTED | Correct behavior — Windows is manual-only | Run `Tests (Windows, manual)` from the Actions UI when you need Windows evidence. |
| Release blocked: "Windows lane evidence" missing | `release_auth_gate.py` found no green Windows lane on the release SHA | Dispatch `tests-windows.yml` on that exact SHA (or let the scheduled MT5 line produce it), then re-run the release. |
| Manual Windows run chose hosted despite the flag | `ENABLE_WINDOWS_SELF_HOSTED` not set, or no eligible runner | Set the repository variable to `true` AND register a `(self-hosted, Windows, X64)` runner. The override input `runner_class=self-hosted` shows the routing decision either way. |
| Self-hosted leg queued for a long time | The race above: runner was taken after the preflight | Either wait for the re-queue, or let `cancel-in-progress` supersede the run. The hosted leg will be used by the next run. |
