# Nexus Installer Architecture (Windows)

This document describes the design of `installer/install.ps1`: the Nexus-native
Windows installation platform. It is an independent implementation - it adopts
proven architectural *patterns* (stage protocol, idempotent bootstrap, safe
update semantics) but contains no third-party installer code.

## 1. Design principles

```text
IDEMPOTENT      re-running never duplicates or corrupts state
SAFE            never destroys user data, local git work, or MT5 state
REPEATABLE      deterministic behavior from defaults/parameters
NO-ADMIN-FIRST  user-scoped installation under %LOCALAPPDATA%\Nexus
SCRIPTABLE      machine-readable JSON protocol on stdout
RECOVERABLE     partial installs resume; damaged pieces repair in place
DIAGNOSTIC      resolved-path report, bounded log, per-stage reasons
VERSIONABLE     installer_version + protocol_version are independent
```

Non-goals: the installer is not a runtime. It provisions software; trading,
risk, model lifecycle, and MT5 integration belong to the application.

## 2. Directory contract

```text
%LOCALAPPDATA%\Nexus\            <- NexusHome   (override: -NexusHome / NEXUS_HOME)
    engine\                      <- InstallDir: git checkout of NexusTradingForexBot
    venv\                        <- managed Python venv (OUTSIDE the repo tree)
    bin\                         <- managed uv, nexus.cmd shim (User PATH target)
    git\                         <- managed PortableGit (only when system Git unusable)
    config\                      <- USER configuration (live.yaml, base.yaml)
    state\install.json           <- non-secret install metadata
    logs\installer.log           <- bounded installer log
    cache\                       <- reserved for future download cache
```

Separation of concerns:

| Class | Location | Survives reinstall? |
|---|---|---|
| INSTALL (code, runtime) | `engine\`, `venv\`, `bin\`, `git\` | replaced by design |
| STATE (metadata) | `state\install.json` | rewritten each install |
| LOGS | `logs\installer.log` | appended, bounded |
| USER CONFIG | `config\` | **always preserved** (create-if-missing) |
| MODELS / RESEARCH / TRADING DATA | application-owned locations | never touched by installer |

The venv deliberately lives **outside** the repository working tree: the
application may delete files inside its own tree, and repo-relative cleanup
must never be able to destroy runtime state.

## 3. Stage protocol

Stages are the stable API consumed by CLI, CI, and a future GUI driver.

```text
environment -> runtime -> git -> node -> repository -> venv
-> dependencies -> node-deps -> config -> path -> verify -> state
```

Categories: `prereqs`, `install`, `finalize`. All stages are
`needs_user_input = false`; the installer never requires interactive input
internally (interactive UX is layered on top by the caller).

Single source of truth: the `$Script:InstallStages` table in `install.ps1`
drives `-Manifest`, `-Stage` dispatch, and full-install ordering. Stage names
are API; adding a stage is additive and does not bump the protocol version.

### Protocol surfaces

| Command | Output | Exit |
|---|---|---|
| `install.ps1 -ProtocolVersion` | `1` | 0 |
| `install.ps1 -Manifest` | manifest JSON | 0 |
| `install.ps1 -ShowResolvedPaths` | paths JSON (no mutation) | 0 |
| `install.ps1 -Stage <name>` | one result frame | 0 / 1 / 2 |
| `install.ps1 -Json` | full-install summary frame | 0 / 1 |
| `install.ps1 -Repair` | per-stage frames + repair summary | 0 / 1 |
| `install.ps1 -DryRun` | plan frame (no mutation) | 0 |

Result frame (exactly one JSON object per invocation on stdout):

```json
{"stage":"runtime","ok":true,"skipped":false,"reason":null,"duration_ms":1234}
```

`skipped=true` is reserved for **deliberate no-op detection** (e.g. the
optional `node` stage when no Node.js exists, `node-deps` when the checkout
has no `package.json`). A stage that performs real work is never reclassified
as skipped (BUG-185 rule). Unknown stage -> `ok:false` frame + exit 2.

### JSON stdout discipline

In driver mode (`-Json` or `-Stage`), the human-output helpers (`Write-Info`,
`Write-Success`, `Write-WarnMsg`, `Write-ErrMsg`) reroute to stderr via
`Write-Diag` (which uses `[Console]::Error.WriteLine`, verified to reach
callers on Windows PowerShell 5.1). Stdout therefore carries only documented
frames. The entry-point catch emits a structured error frame only if the stage
wrapper did not already emit the authoritative one - never double output.

## 4. Prerequisite provisioning

### Python (stage `runtime`)

- Managed `uv` at `<NexusHome>\bin\uv.exe`; install ladder: astral.sh installer
  -> GitHub-releases installer mirror. Existing managed uv short-circuits.
- Canonical Python `3.11` (single constant, matches pyproject
  `requires-python >=3.11`); fallbacks `3.12`, `3.13` are attempted and
  **reported** when used (`EffectivePythonVersion`).
- `uv python install` provisions missing interpreters user-scoped (no admin).
- System `python` is accepted only after: WindowsApps/Store-stub rejection,
  actual invocation (`python --version`), and a supported-version regex.
- Cross-process safety: stages may run in separate PowerShell processes, so
  `Resolve-UvCmd` / `Resolve-AvailablePythonVersion` re-derive uv and the
  effective interpreter instead of trusting in-memory state.

### Git (stage `git`)

- Fast path: existing `git` on PATH + Git Bash MSYS child-process probe
  (`bash -c "/usr/bin/true; /usr/bin/cat --version >/dev/null"`), which catches
  Mandatory-ASLR breakage that lets bash.exe start but kills msys children.
- Fallback: user-scoped PortableGit (self-extracting archive, silent extract)
  into `<NexusHome>\git`; PATH entries `cmd`, `bin`, `usr\bin` appended
  idempotently. System Git is never modified. `NEXUS_GIT_BASH_PATH` is
  persisted for the application.

### Node (stage `node` / `node-deps`)

Optional by design: Nexus has no Node workspace today. The stage detects
Node >= 18, reports honest `skipped` when absent, and (if a future checkout
ships `package.json`) installs via `npm ci` with an `npm install` fallback.

## 5. Source acquisition (stage `repository`)

Ladder: `git clone` over SSH (BatchMode, only succeeds if the user has keys)
-> `git clone` over HTTPS -> ZIP archive from GitHub.

Update path for an existing valid checkout (`Test-NexusRepoValid` requires a
resolvable HEAD - interrupted clones are treated as broken and re-acquired):

1. `git config core.autocrlf false` on the managed checkout (prevents
   fabricated CRLF dirt from blocking updates).
2. Dirty worktree -> `git stash push --include-untracked` (unmerged index
   entries cleared first via `git reset` - worktree changes kept).
3. `git fetch origin` then precedence resolution:
   - `-Commit`: applied unless it would roll HEAD backwards (ancestor check);
     `-ForceCommit` overrides. Post-checkout, HEAD is verified to equal the
     requested commit.
   - `-Tag`: exact tag fetch + detached checkout.
   - `-Branch`: `checkout` + `pull --ff-only`. On divergence the managed
     checkout resets to `origin/<branch>` - safe because local work was
     stashed in step 2.
4. Stash restore on success (drop on clean apply; preserve + print restore
   command on conflicts). A failure before restore leaves the stash in place
   and reports it - never silently dropped.

ZIP fallback: unique-per-session temp names, zip-slip-validated extraction,
repository-structure validation (`pyproject.toml` present), atomic move into
`InstallDir` (broken prior dir moved aside, never deleted), then `git init` +
fetch + pinned checkout so future updates still work.

## 6. Environment assembly

- **venv** (stage `venv`): transactional recreate. Old venv renamed to
  `venv.stale.<ts>-<guid>` (a rename survives DLL-locked files where delete
  would fail), replacement created with `uv venv --seed`, health-verified
  (interpreter runs, version matches, site-packages importable) before the
  parked tree is deleted. Failure -> rollback restore. A still-locked old venv
  parks until a later run.
- **dependencies** (stage `dependencies`): `pyproject.toml` is the single
  source of truth (`uv pip install -e .`); tiered fallback (`.[web]`,
  `--no-deps` core) with explicit reporting. Baseline import gate probes
  `nexus_scalp`, `typer`, `pydantic`, `structlog` through the venv's own
  interpreter. Entry-point presence (`nexus.exe`) verified.
- **config** (stage `config`): create-if-missing templates from
  `configs/base.yaml` and `configs/live.yaml.example` -> `<NexusHome>\config`.
  Existing files are kept verbatim; secrets are never written.
- **path** (stage `path`): only `<NexusHome>\bin` is added to the **User**
  PATH (front position, case-insensitively deduped, unrelated entries
  preserved). Legacy `venv\Scripts` entries are migrated off. `nexus.cmd`
  delegates to the venv entry-point exe so venv recreation never orphans the
  command. `NEXUS_HOME` persisted at User scope.
- **verify** (stage `verify`): venv health, `nexus version` exit code,
  repository validity, config presence - fail-closed with enumerated problems.
- **state** (stage `state`): `state/install.json` (installer/protocol
  version, timestamps, paths, repo HEAD, python/git versions, last stage).
  No secrets. Additionally, every successful stage flushes its per-stage
  result to the state file atomically (tmp file + rename), making install
  progress a durable ledger a driver can use to resume after a crash.

## 7b. Single-writer install lock

`Wait-NexusInstallerLock` opens `state\installer.lock` with `FileShare.None`
(mandatory Windows file lock). A second installer retries for ~5s, then
reports a deliberate skip - in protocol mode a well-formed
`ok:true/skipped:true` frame with a lock reason, exit 0 (never an error
shape). Because the lock lives in an OS file handle, a crashed installer
releases it automatically when its process dies; there is no stale-lock
cleanup path to get wrong. The detection-only `environment` stage is exempt
from the lock.

## 7c. Repair and dry-run modes

- `-Repair` runs a targeted safe subset (runtime, venv, dependencies, path,
  verify, state) under the install lock. The repository checkout is not
  forced (safe update semantics govern it) and config is create-if-missing
  by design, so repair can never destroy user data.
- `-DryRun` prints the resolved plan as JSON - paths, requested python/
  branch/commit/tag, and the would-run stage list - and performs no
  filesystem mutation whatsoever (even the writability probe is skipped).
- Full installs differentiate `first-install` vs `update` (valid-checkout
  detection) and report the mode in the banner and the JSON summary.

## 7. Windows hardening

- **8.3 short-path normalization**: `ConvertTo-LongPath` expands profile
  aliases through kernel32 `GetLongPathNameW`, COM `FileSystemObject`, or
  profile-root reconstruction, before any path is used. `%TEMP%`, `%TMP%`,
  `%LOCALAPPDATA%`, `%APPDATA%`, `%USERPROFILE%` normalized in-process.
- **PowerShell 5.1 compatibility**: pure-ASCII source; no PS7-only syntax;
  PS5.1's lack of `-TimeoutSec` on `Invoke-WebRequest` handled by job-based
  wall-clock ceilings; `ProgressPreference` silenced (5-100x download speedup);
  native stderr handled by relaxing EAP around invocations and checking
  `$LASTEXITCODE` + artifacts instead of stream semantics.
- **Architecture detection**: `Win32_Processor.Architecture` (emulation-
  invariant) with `PROCESSOR_ARCHITEW6432` fallback - correct on Windows-on-ARM.
- **Downloads**: `.partial-<pid>` staging + atomic rename (interrupted
  downloads never trusted), bounded retries with exponential backoff + jitter,
  explicit timeouts, empty-file rejection, and an optional SHA256 pin +
  size-floor integrity gate verified BEFORE the atomic move
  (`-ExpectedSha256` / `-MinBytes`; see docs/INSTALL_INTEGRITY.md). Every
  download records telemetry (host, bytes, attempts, digests, verification
  mode, outcome) into `state/install.json`.
- **Extraction**: zip-slip validation (absolute/UNC/drive-qualified/`..` entry
  names rejected; resolved targets re-verified inside the destination root).
- **Preflight** (stage `environment`): Windows-only guard, free-disk check,
  writability probes for NexusHome and InstallDir parent. Fail early, fail clear.

## 8. Safety boundaries

- No admin elevation anywhere in the code path; user-scoped installs only.
- Never kills processes; a locked venv parks instead of force-deleting.
- Never touches MT5: the terminal is external; only import-detection of the
  Python package happens, read-only (`-Ensure mt5`).
- Never downloads/replaces model artifacts; model health belongs to the
  application (`nexus doctor`).
- Never overwrites user configuration; create-if-missing only.
- Never issues destructive git operations without first stashing local work.
- Secrets never appear in logs or state; the log is bounded (~1MB).

## 9. Testing

`tests/installer/` runs the installer's protocol surfaces and key pure
functions against a real PowerShell on this machine (PS 5.1 or 7, whichever
is present), using temp `-NexusHome` overrides so the developer machine is
never mutated. Coverage: protocol outputs (manifest shape, protocol version,
resolved-paths), stage frames, unknown-stage exit code, JSON-stdout purity,
8.3 path normalizer, zip-slip extraction guard, User PATH dedup helper,
download integrity (SHA256 pin/mismatch/malformed, truncation floor, empty
payload, retry-then-block, telemetry), and the state-ledger availability
fields. A repository E2E (full acquisition into a temp home) is env-gated via
`NEXUS_INSTALLER_E2E=1` since it performs network downloads.

The download → install → activation → first-run integrity truth table
(AVAILABLE / DEGRADED / BLOCKED states, per-stage gates, remaining risks)
lives in [`docs/INSTALL_INTEGRITY.md`](INSTALL_INTEGRITY.md).
