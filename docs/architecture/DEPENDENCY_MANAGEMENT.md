# Dependency Management & Supply-Chain Governance

> SSOT for how dependencies are declared, updated, audited, and merged in this
> repository. Established by TASK-DEVOPS-DEPENDABOT-HARDENING (2026-09-11).
> Every claim below is backed by CI evidence or a verified GitHub API read;
> commands marked with a source line are the repository's own verified tooling.

## 1. Dependency sources (what is authoritative)

| Ecosystem | Manifest (hand-edited SSOT) | Generated artifacts (DO NOT EDIT) | Production runtime? |
|---|---|---|---|
| Python | `pyproject.toml` | `requirements.lock`, `requirements.txt` (both `# GENERATED - DO NOT EDIT`) | Yes — the engine runs from the lock (see `ci.yml` "Install via lock") |
| Frontend (`/frontend`) | `package.json` | `package-lock.json` (npm-managed, in lockstep) | **No — build/dev-time only.** Node never runs the production engine; `frontend/` is compiled to static assets. Do not introduce Node into the runtime path. |
| GitHub Actions | `.github/workflows/*` | — | n/a (CI supply chain) |

`uv.lock` exists as a resolution artifact referenced by `lockfile-diff.yml`,
`osv-scanner.yml`, and `scripts/ci/classify_changes.py`; the install/drift
contract below runs through `requirements.lock`.

## 2. Python lockfile synchronization (verified contract)

Source of truth: `scripts/ci/check_dependency_drift.py` and the header of the
generated artifacts. The authoritative regeneration command is:

```bash
python scripts/ci/check_dependency_drift.py --regen
# = uv pip compile pyproject.toml --all-extras --universal --generate-hashes
```

Rules (enforced fail-closed by the required check **"Dependency drift (lock vs
pyproject)"**):

1. `pyproject.toml` is the ONLY hand-maintained dependency definition.
2. Any PR that changes `pyproject.toml` MUST regenerate `requirements.lock`
   AND `requirements.txt` in the SAME PR. A hand-edited generated artifact is
   a CI red by design.
3. The resolver must see a Python 3.11 interpreter (project floor
   `requires-python >=3.11`); the CI job pins 3.11 and uv **0.12.10**
   (`dependency-lock.yml` comment block documents why both sides move
   together). Do not bump uv on one side only.
4. `requirements.txt` is a generated thin compatibility artifact parsed from
   `requirements.lock` — regenerate it via the same script, never by hand.

Dependabot pip PRs modify only the version pins inside the generated
artifacts. That is allowed (the artifacts are regenerated outputs), but the
PR must still leave `pyproject.toml` and the artifacts mutually consistent —
the drift check passes iff the resolution matches, regardless of which file
Dependabot edited.

## 3. Frontend (npm) lockfile synchronization

`package.json` and `package-lock.json` must move together. Verification before
merge — use the scripts that actually exist in `frontend/package.json`
(`dev`, `build`, `preview`, `typecheck`; there is **no `test` script — do not
invent one**):

```bash
cd frontend
npm ci           # clean install strictly from package-lock.json (lockstep proof)
npm run typecheck
npm run build    # tsc -b && vite build
```

`npm ci` failing = lockfile out of sync = the PR does not merge. Never accept a
PR where only `package.json` changed.

## 4. Dependabot configuration (`.github/dependabot.yml`)

| Ecosystem | Directory | Schedule (Etc/UTC) | PR limit | Groups |
|---|---|---|---|---|
| pip | `/` | weekly, Sunday 04:00 | 5 | `python-production`, `python-dev-tools` |
| npm | `/frontend` | weekly, Sunday 04:30 | 5 | `frontend-framework`, `frontend-build-tools` |
| github-actions | `/` | weekly, Sunday 05:00 | 5 | `github-actions` |

Commit-message prefixes: `chore(deps-py)`, `chore(deps-fe)`, `ci(actions)`
(all with scope). Labels: `dependencies` + `python` / `javascript` (both
labels exist on the repo) / `github-actions`.

## 5. Security-update policy (security-first)

- **Groups apply to version updates only** (`applies-to: version-updates`).
  Dependabot security-update PRs are never grouped and never suppressed — they
  arrive individually and are triaged ahead of routine bumps.
- **There are deliberately no `ignore` rules.** A broad ignore (e.g. "ignore
  all torch updates") would also suppress security remediation. Do not add
  blanket ignores.
- Risky packages (torch, polars, numpy, pydantic, ...) are bounded by the
  pyproject.toml **version ranges** (e.g. `torch>=2.2.0,<3.0.0`,
  `polars>=0.20.0,<2.0.0`). Dependabot respects those ranges, so ordinary
  updates stay inside the tested compatibility window while a genuine security
  fix inside the window still flows. Changing a range is an architectural
  decision, not a governance flip.
- Severity classification used for triage: CRITICAL / HIGH / MODERATE / LOW
  security vs ORDINARY version. Security remediation must not wait behind
  grouped freshness PRs.

## 6. Grouping policy and noise control

- One PR per ecosystem per weekly window (plus security PRs as they arise):
  maximum routine load ≈ 3 grouped PRs/week, capped at 5 open PRs per
  ecosystem.
- Python production and dev tooling are separate groups; a broken dev-tool
  bump can be reverted without touching the runtime group.
- Frontend framework (`react*`, `@tanstack/*`, `zustand`) and build tools
  (`vite`, `typescript`, `@types/*`, `@vitejs/*`) are separate for the same
  reason.
- No cross-ecosystem grouping (GitHub `multi-ecosystem-groups` is available but
  intentionally unused): Python, npm, and Actions have different validation
  paths, and separate PRs keep rollback and CI diagnosis deterministic.

## 7. GitHub Actions SHA pinning

Every external `uses:` reference in `.github/workflows/` is pinned to a full
40-character commit SHA with a human-readable version comment:

```yaml
uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
```

- Resolved 2026-09-11 from the upstream repos (tag→SHA via `git ls-remote`
  / GitHub API; SHA is authoritative, the comment is a label).
- Floating tags (`@v4`) are mutable references and are NOT accepted as pins.
- `uses: ./local-action` and `docker://` references follow separate rules;
  there are currently none in production workflows.
- The pinned Actions ecosystem in Dependabot keeps the pins current — bumping
  a pin = update SHA + version comment together.
- Enforcement: a deterministic scan over `uses:` lines (fail on any non-40-hex
  external ref) was run at implementation time; keep it green. Local actions
  and `secrets`/`vars` usage are out of scope of the scan.

## 8. Required CI before merge (dependency PRs)

Branch protection on `main` requires (relevant subset):

- **Python PRs:** `Dependency drift (lock vs pyproject)` + `Code Quality &
  Tests` + `Py Tests (macos-latest)` + `Py Tests (windows-latest)`.
- **Frontend PRs:** `Frontend JS Unit Tests` (Web/ JS suite) — plus run the
  §3 commands locally; they are not (yet) all CI-required for `frontend/`.
- **Actions PRs:** `CI Integrity and Change Classification` + `Validate
  documentation` (workflows are classified as CI changes) — and CI green on
  the PR itself.
- Advisory-but-expected: `Lockfile Diff` job summary shows reviewers exactly
  which dependency manifests moved (it never fails the build; the drift gate
  is the enforcing control).

Never weaken, skip, or context-swap a required check to land a dependency PR.

## 9. Security PR priority, manual overrides, exceptions, rollback

1. Security remediation > grouped freshness. A security PR may land with
   routine grouped PRs still open (limits are per-ecosystem and independent).
2. Manual override: a maintainer may pause Dependabot version updates
   (repo settings) during a release freeze without disabling alerts or
   security updates. Record the freeze window in this file.
3. Exceptions to grouping (e.g. pin a package out of a group) go here with the
   reason and removal condition.
4. Rollback: dependency PRs squash-merge (the repo's merge method — merge
   commits are 405-disallowed on this repository); revert the squash commit on
   `main` via a normal PR. The grouped-PR shape (one ecosystem, one area) makes
   a revert surgical; if a grouped PR must be partially rolled back, revert the
   whole group and re-open the good packages in the next window.

## 10. GitHub security posture (verified 2026-09-11)

| Control | State | Evidence |
|---|---|---|
| Dependency graph | ENABLED | SBOM endpoint `GET /dependency-graph/sbom` → HTTP 200 |
| Dependabot alerts | ENABLED, 0 open | `GET /vulnerability-alerts` → 204; `GET /dependabot/alerts?state=open` → [] |
| Dependabot security updates | ENABLED, not paused | `GET /automated-security-fixes` → `{"enabled": true, "paused": false}` |
| Grouped security updates | UI-only toggle; repo-level API does not expose it (404) | API 404 on `automated-security-fixes/grouped`; operator verifies/sets in Security → Dependabot if UI shows the toggle |
| Malware (dev-environment) alerts | Not exposed by repository admin API | No repo-level endpoint; not configurable by this credential/plan |
| Secret scanning + push protection | ENABLED | `security_and_analysis` in repo metadata |
| Code scanning (CodeQL) / Trivy / OSV-Scanner / Dependency Review | Present as CI workflows (`security.yml`, `osv-scanner.yml`, `lockfile-diff.yml`) with required contexts | `.github/workflows/` |
| Dependabot rules (org-level policy rules) | No repository-visible API (404); no org policy rule interacts with `.github/dependabot.yml` | API evidence as above |

## 11. Operator/owner notes

- The `python` label did not exist before this task; Dependabot creates missing
  labels itself on first use. The `github-actions` label likewise.
- Grouped security updates, malware alerts, and org Dependabot rules are
  org/UI-scoped controls — see the final audit report for the exact evidence
  and remaining operator actions.
