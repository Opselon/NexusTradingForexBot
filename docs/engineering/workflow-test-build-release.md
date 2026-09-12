---
title: Test, Build & Release Workflow
description: The end-to-end project workflow — branches, PR gates, cross-platform testing, release cuts, hotfixes, and the GitHub Actions surface behind them.
lang: en
---

# Test, Build & Release Workflow

This is the operational workflow every change follows from working tree to
published release. It unifies rules that were previously scattered across
[`agents/multi-agent-git-contract.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/multi-agent-git-contract.md),
[`docs/CI_ARCHITECTURE.md`](../CI_ARCHITECTURE.md),
[`docs/RELEASE.md`](../RELEASE.md), and the DEC-0006/DEC-0008 decision
records. Where this page and a decision record disagree, the decision record
(`agents/decisions/`) wins — say so and fix this page.

Core principles:

1. **`origin/main` is integration truth.** Nobody pushes to `main` directly —
   branch protection rejects it; everything arrives by PR.
2. **Local gates mirror CI gates.** `beforePush.ps1` (Windows) and
   `beforePush.sh` (Linux/macOS) delegate to the same canonical
   `scripts/ci/check_local.py` that CI parity-checks in `ci-integrity`.
   A change that is red locally is never pushed to "let CI find out".
3. **Releases are tag-triggered and fail-closed.** A release run that fails
   any gate publishes nothing; artifacts are verified and signed before the
   GitHub Release becomes visible.
4. **Cross-platform means: test everywhere, publish Windows x64.** The
   dependency stack (MetaTrader5) is Windows-only; see
   [What is deliberately NOT a release channel](#43-what-is-deliberately-not-a-release-channel).

## 1. Day-to-day flow (change → main)

```text
claim task (agents/taskboard.md row)
    │
    ▼
branch off origin/main ──► work in small coherent commits ──► beforePush locally
    │
    ▼
push branch ──► open PR to main ──► 11 required contexts green
    │
    ▼
coordinator approval (DEC-0008) ──► SQUASH merge ──► CI re-fires on main ──► taskboard row closed
```

### Branch naming

| Prefix | Use | Lifetime |
| :--- | :--- | :--- |
| `agent/<owner>/<topic>` | agent-scoped feature/fix work | deleted after merge (squash auto-delete) |
| `fix/<slug>`, `chore/<slug>` | human-scale fixes and chores | deleted after merge |
| `integration/<wave>` | convergence branch when parallel refs must merge before landing | deleted after its PR merges |
| `hotfix/<slug>` | urgent production fix (see [Hotfix flow](#5-hotfix-and-rollback)) | deleted after merge |
| `dependabot/*` | automated dependency PRs (weekly, grouped) | handled per [Dependency workflow](#7-dependencies) |
| `recovery/*`, `archive/*` | forensics/recovery refs — coordinator-owned | do not delete without coordinator |

### PR contract

- **Required contexts on `main` (all must be green before merge):**
  CI Integrity and Change Classification · Code Quality & Tests ·
  Py Tests (windows-latest) · Py Tests (macos-latest) · Frontend JS Unit Tests ·
  Validate documentation · Dependency drift (lock vs pyproject) ·
  Migration safety (fail-loud + version postconditions) · CodeQL Analysis ·
  Trivy Vulnerability Scan · OSV Scanner / osv-scan.
  Each required context's workflow fires on `push: branches: [main]` **and**
  on PRs by trigger-alignment contract (see `docs/CI_ARCHITECTURE.md`), so
  post-merge main never sits pending.
- **Squash-only.** The repo rejects merge commits (405). The squash carrier
  loses branch SHAs — put the branch tip SHA in the commit body and verify
  tree parity (`git rev-parse <merge_sha>^{tree}` == branch tip tree) before
  moving local main.
- **Merge requires explicit coordinator approval** — green CI is a
  precondition, not an authorization (DEC-0008).
- **Stale base:** merge `origin/main` INTO the PR branch (`git merge --no-ff`),
  never rebase a shared branch. `swarm_log.md` and `taskboard.md` are the
  chronic conflict files — keep ALL rows/lines in chronological order.
  Before pushing to a shared branch: `git ls-remote origin <ref>`; if
  non-FF, check whether the content already landed via a parallel PR.
- **Never force-push or delete shared remote refs** (coordinator-owned;
  `--force-with-lease` at most on integration branches).
- **No weakening** a test, golden, or CI check to get green. Known-foreign
  reds are disclosed in the PR body with evidence (stash-probe baseline),
  not silenced.
- Multi-agent race: commit task-scope files immediately (parallel agents can
  wipe or sweep uncommitted work); `git diff --cached --name-only` right
  before `git commit` to catch index contamination.

## 2. Cross-platform testing policy

The test surface runs on three OS families with different jobs. Ubuntu is the
workhorse; Windows/macOS are parity gates; nothing about running tests on
Linux makes Linux a *supported runtime* — see the matrix in §4.

| Lane | Runner(s) | What runs | Fires on | Blocking? |
| :--- | :--- | :--- | :--- | :--- |
| `ci.yml :: ci-integrity` | ubuntu | workflow static analysis, DEC-id uniqueness, **local-gate parity**, change classification | PR + push main/develop/ci-tests | required |
| `ci.yml :: quality` | ubuntu | ruff lint+format → torch.load guard → critical manifest → mypy → pytest critical suite + coverage → E2E smoke → runtime certification | PR + push main/develop/ci-tests | required |
| `tests-os.yml` | **windows-latest + macos-latest** | the same critical suite (xdist + coverage) — catches path/CRLF/encoding regressions an Ubuntu-green hides | PR + push main | required (both legs) |
| `js-tests.yml` | ubuntu | `node --check` on every shipped `Web/*.js` + `tests/js/*.test.js` (buildless SPA) | PR + push main | required |
| `docs.yml :: validate` | ubuntu | docs doctor (links/anchors/translations/secrets/drift) + Pages deploy on main | PR + push main + release events | required ("always-report no-op" when docs-unaffected, DEC-0008) |
| `security.yml`, `osv-scanner.yml`, `dependency-lock.yml` | ubuntu | CodeQL, Trivy, OSV, lock drift, migration safety | PR + push main (weekly schedules too) | required |
| `qa-deep-assurance.yml`, `nightly-qa.yml`, `nightly-e2e.yml` | ubuntu | deep property/invariant + slow suites, real-browser E2E | PR/push main + nightly/weekly cron | advisory — but a nightly red is triaged like a P0 the next day |
| `ci.yml :: heavy-ci` | ubuntu matrix | integration / e2e / research / model arms | `ci-tests` branch or `workflow_dispatch: full=true` | opt-in |
| `release.yml` gates | ubuntu + **windows-latest build** | see §3 | `v*` tags + dispatch | release-blocking |
| `docker.yml` | ubuntu | GHCR image build+publish | `docker` branch push + dispatch | separate channel (see gap note §4.3) |

Windows/macOS dev parity rules:

- The repo is `core.autocrlf=true` (`i/lf w/crlf`): compare blob-vs-blob after
  CRLF normalization or formatting-only diffs look like conflicts.
- Pytest locally on Windows: `./.venv/Scripts/python.exe -m pytest <targets>
  -p no:cacheprovider > pytest_out.txt 2>&1`, then read the file (console
  output is unreliable on this host).
- Linux/WSL2: the MT5-under-Wine test/paper platform is documented in
  [`docs/RELEASE.md` §3](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/RELEASE.md)
  and `docs/linux_wsl2_mt5_platform.md`; direct Python `MetaTrader5` IPC does
  not work under Wine — use `RemoteMT5GatewayAdapter`.
- macOS: no MT5, no packaged runtime — CI parity only (path-separator,
  timezone/clock-domain, maintenance-window flakes are its main catches).

## 3. Build pipeline (what gets built and how it is proven)

Single production build path: `release.yml` on a `v*` tag (plus
`workflow_dispatch` with an explicit version input). The local mirror for
iteration is `scripts/build/build_release.ps1` + `verify_release.ps1` +
`clean_install_test.ps1` (Windows).

```text
validate      tag == pyproject version · secret scan · no dev artifacts tracked
  └► gates    ruff · mypy · critical suite · release-hardening smoke
       └► build-windows-x64 (windows-latest)
            PyInstaller onedir EXE + onefile CLI
            build-info.json stamp (identity tripwire: commit non-null,
            build_timestamp immutable across invocations — BUG-174)
            EXE smoke: `version --plain`, `health --json`
            stage tree → BUG-166 pre-staged verification contract
            Inno Setup installer (embeds SHA256SUMS subset + manifest)
            checksums → release-manifest.json → SBOM (SPDX-lite)
            Ed25519 sign update-manifest.signed.json (P0 trust root;
            client updaters reject unsigned releases fail-closed)
            verify-release full self-check · optional Authenticode gate
            (NSE_REQUIRE_CODE_SIGNING=true ⇒ signtool verify must pass)
       └► arm64-report        explicit, loud UNSUPPORTED (never silent)
       └► release             publish + post-publish API verification of
                              tag, assets, sizes (zero assets = failure)
```

Every step is release-blocking; a broken artifact is never published.
Artifact naming is canonical:
`NexusScalpEngine-<version>-win-x64-setup.exe`,
`NexusScalpEngine-<version>-win-x64.zip`, `cli/NexusScalpEngine-CLI.exe`,
plus `checksums/`, `manifests/`, `sbom/`.

## 4. Release workflow (the cut, step by step)

### 4.1 Preflight (operator/coordinator)

```bash
git fetch origin
# 1. main tip is green:
gh run list --branch main --limit 6        # CI + OS matrix + docs all success
# 2. last nightly-qa / nightly-e2e / qa-deep-assurance on main: success
# 3. no open P0 rows on agents/taskboard.md blocking release
# 4. UPDATE-SIG-OPERATOR-type parked blockers reviewed (release key present)
```

### 4.2 Version bump → tag → watch → verify

1. **Bump PR.** One commit changes `pyproject.toml` `version = "X.Y.Z"`
   (semver: `Z` fixes · `Y` features · `X` breaking; prereleases use
   `X.Y.Z-rcN`, which `release.yml` publishes as a GitHub **prerelease**).
   Docs never hard-code a conflicting version — `check_docs.py` drift
   detection enforces the single source, and the site build reads
   `pyproject.toml`. Normal PR rules apply.
2. **Tag after the bump PR is squash-merged** — the tag must point at the
   `main` commit whose release run is expected to pass:
   ```bash
   git checkout main && git pull
   git tag -a vX.Y.Z -m "release vX.Y.Z" origin/main   # annotated, on main tip
   git push origin vX.Y.Z
   ```
   Never cut release content on a side branch; `release.yml` `validate`
   fails a tag whose tree doesn't match `pyproject`.
3. **Watch the run.** `gh run watch` (or poll
   `GET /repos/:o/:r/actions/workflows/release.yml/runs`). Do not retry a
   failed publish by hand — fix forward or re-cut.
4. **Tag discipline (BUG-152).** A tag whose release run failed is
   **re-cut, not explained away**: delete the stale tag (remote via REST,
   local too), re-annotate on the verified commit, push again. Two tags may
   be in flight; per-ref concurrency keeps them from racing each other, and
   same-tag re-pushes replace the in-flight run.
5. **Post-release verification (the publisher's job, not the workflow's):**
   - `gh release view vX.Y.Z` shows all assets (setup.exe, zip, CLI,
     SHA256SUMS.txt, release-manifest.json, update-manifest.signed.json,
     sbom.spdx.json) with non-trivial sizes.
   - Checksum spot-check: download the zip, `Get-FileHash` vs `SHA256SUMS.txt`.
   - Docs site refreshed release metadata (docs.yml fires on `release:`
     published → `fetch_releases.py` regenerates What's New / releases page).
   - Announce: taskboard row + `swarm_log.md` entry with tag, run id,
     release URL — same "claims require verified state" rule as PRs.

### 4.3 What is deliberately NOT a release channel

- **Linux/macOS binaries:** none published. Tests are cross-platform; the
  runtime stack is Windows-x64-bound (see §2 and `docs/RELEASE.md` §3).
- **Windows ARM64:** explicitly UNSUPPORTED (no torch/polars/MT5 wheels);
  the `arm64-report` job makes the non-support loud every release.
- **Docker (GHCR):** a *separate, opt-in channel* built only from the
  `docker` branch — never from normal dev pushes. Gap: the `docker` branch
  does not currently exist on origin, so no image is published; creating it
  (or wiring `docker.yml` to release tags) is an open infra decision, not a
  silent default.

## 5. Hotfix and rollback

**Hotfix (production defect on the latest release):**

```bash
git checkout -b hotfix/<BUG-id-short-slug> origin/main
# fix + reproducer test (BUG fixes REQUIRE a regression test — same rule as
# feature work); beforePush locally; PR to main; label/priority P0 in body.
```

Merge follows the same rules (all contexts green + coordinator approval).
Then cut the next patch release (`X.Y.Z+1`) per §4.2 — a hotfix is not
"deployed from the branch", it is released like everything else.
If the defect is in the *release tooling itself* (wrong artifact published),
revoke is the client-side answer: the updater filters draft/revoked
releases; delete-down-and-re-cut of the assets, then re-tag per BUG-152.

**Rollback (client side):** `nexus update rollback` (ends `FAILED_SAFE`
exit 1 on failure — never a half-installed state); release-identity lock +
checksum verification mean a bad release can be rolled back to the previous
exact release without operator intervention beyond the command.

**Rollback (CI/main):** history is never rewritten. A landed defect gets a
forward `fix(...)` PR (revert commit if clean) through the same gates.
Absorbed/contaminated commits are disclosed via
`ABSORPTION-DISCLOSURE-<shorthash>` taskboard rows instead of force-pushes.

## 6. Long-lived branch roles

| Branch | Role | Notes |
| :--- | :--- | :--- |
| `main` | protected integration + release source | every push runs the full required-context set |
| `develop` (when created) | pre-integration soak | all workflows already accept it as a trigger; optional, not currently present |
| `ci-tests` (when created) | heavy-matrix opt-in lane (`heavy-ci` fires there) | also a trigger in ci/tests-os/js-tests; not currently present |
| `docker` | GHCR image channel | must be created deliberately; see §4.3 |

## 7. Dependencies

Dependabot (`.github/dependabot.yml`) opens **weekly, grouped** PRs for pip
(root), npm (`frontend/`), and GitHub Actions. Governance rules:

- Dependency PRs must keep `requirements.lock` regenerated in the same PR —
  the "Dependency drift (lock vs pyproject)" context is required and has
  fired true positives (e.g. upstream tzdata 2026.3→2026.4, closed via
  PR #152 with `check_dependency_drift.py --regen`).
- Actions stay pinned to **immutable commit SHAs** in
  `release.yml`/`security.yml`/`docker.yml` (supply-chain hardening);
  `ci.yml` still has floating tags — open follow-up (§8).
- Lockfile changes ride `lockfile-diff.yml` (PR-only review visibility) and
  `dependency-lock.yml` (migration-safety probe on disposable DBs).
- Merge dependency PRs like any PR (contexts + approval); batch-merge
  grouped PRs in one go to keep the OS-matrix legs warm.

## 8. Known gaps and follow-ups (state as of 2026-09-12)

| # | Gap | Impact | Owner/next step |
| :--- | :--- | :--- | :--- |
| 1 | Updater never fetches `update-manifest.signed.json` | client trust chain ends at SECURITY_BLOCKED — releases not auto-updatable | Agent-10 CI/CD audit finding; wire discovery to the signed asset |
| 2 | Release manifest uses SHORT git sha | exact-release identity weaker than design | emit full `git rev-parse HEAD` in `build-info.json` |
| 3 | `ci.yml` floating action tags | supply-chain drift vs the SHA-pinned lanes | pin to SHAs (bot PRs in flight, #143/#144) |
| 4 | `docker.yml` branch absent | no published image despite the workflow | decide: create `docker` branch or retrigger on tags |
| 5 | Release lane independent of main CI head | gates re-run in release context (by design) but release run doesn't consult main's last-green | acceptable — documented trade-off |

## 9. Command cheat-sheet

```bash
# local gate (any OS)
./beforePush.ps1 -SkipPush        # Windows          (beforePush.sh on Linux/macOS)

# PR lifecycle
git push -u origin agent/<you>/<topic>
gh pr create --base main ...
gh pr checks <n> --watch          # all required contexts
gh pr merge <n> --squash          # coordinator-approved only

# release cut
# (bump pyproject version in its own PR first, merge it)
git tag -a vX.Y.Z -m "release vX.Y.Z" origin/main && git push origin vX.Y.Z
gh run list --workflow release.yml --limit 3
gh run view <id> --log-failed
gh release view vX.Y.Z

# failed release run → re-cut (BUG-152)
git tag -d vX.Y.Z; gh release delete vX.Y.Z --cleanup-tag --yes
# fix, land on main, then re-tag per above

# rerun CI forensics
gh run rerun <id> --failed
```

Related: [Quality & Testing](quality.md) · [CI Architecture](ci.md) ·
[Release Process](release-process.md) · [Security](security.md) ·
full operational detail in
[`docs/RELEASE.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/RELEASE.md)
and [`docs/CI_ARCHITECTURE.md`](../CI_ARCHITECTURE.md).
