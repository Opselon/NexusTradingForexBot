# Installing Nexus on Windows

The Nexus installer is a single PowerShell script that provisions everything
Nexus needs **without Administrator privileges**: Python (uv-managed), Git
(if missing), the engine source (GitHub), a virtual environment, Python
dependencies, configuration templates, and the `nexus` command on your PATH.

## One-line install

```powershell
iex (irm https://<your-nexus-domain>/installer/install.ps1)
```

Or download and run with options:

```powershell
irm https://<your-nexus-domain>/installer/install.ps1 -OutFile install.ps1
.\install.ps1
```

Requirements: Windows 10/11, PowerShell 5.1 or 7+, internet access.
Admin rights are **not** required for the standard install.

## One-line packaged-release install

The bootstrap installer above provisions a **source** checkout. To install the
**packaged release** instead (the Windows build published on GitHub Releases —
no Python, no Git, no venv), use `installer/install-release.ps1`:

```powershell
iex (irm https://raw.githubusercontent.com/Opselon/NexusTradingForexBot/main/installer/install-release.ps1)
```

Same posture, no admin, and a mandatory integrity chain: it resolves the
release, downloads the payload plus its `SHA256SUMS.txt` / `release-manifest.json`
contract, verifies the payload's SHA-256 **before** installing, installs
per-user, then makes the installed build prove itself with `verify-release`.
A missing checksum entry, a malformed digest, a digest mismatch, or a
SHA256SUMS-vs-manifest disagreement BLOCKS the install with a precise reason
and a distinct exit code — an unverified artifact is never installed.

```powershell
.\install-release.ps1 -Version 9.1.0   # pin (no GitHub API call at all)
.\install-release.ps1 -Portable        # install the ZIP instead of setup.exe
.\install-release.ps1 -DryRun          # resolve + plan, mutate nothing
.\install-release.ps1 -Repair          # re-install the recorded version
.\install-release.ps1 -ShowResolvedPaths   # resolved-path JSON, no network
.\install-release.ps1 -Json            # one machine-readable frame on stdout
```

| Parameter | Purpose |
|---|---|
| `-Version <x.y.z>` | Pin an exact release (`v` prefix accepted). Builds the download URLs directly and skips the GitHub API lookup — the recommended form for CI and rate-limited networks. Empty = latest stable release via the GitHub API. |
| `-InstallDir <path>` | Install directory. Default: `%LOCALAPPDATA%\Programs\NexusScalpEngine` (matches the Inno `DefaultDirName`). |
| `-Portable` | Expand the portable ZIP instead of running the Inno setup.exe. |
| `-DryRun` | Resolve the release and print the plan; download nothing, install nothing. |
| `-Repair` | Re-run the install for the recorded version, bypassing the already-current short-circuit. User data is never touched. |
| `-NoLaunch` | Never execute the installed EXE. The post-install proof is then skipped and the install is recorded `DEGRADED`, never `AVAILABLE`. |
| `-Json` | Emit exactly one machine-readable JSON frame on stdout; human diagnostics go to stderr. |
| `-ProtocolVersion <n>` | JSON protocol version. An unsupported version is refused (exit 2) rather than misinterpreted. |
| `-ShowResolvedPaths` | Print resolved paths as JSON and exit — no network, no mutation (first probe for any path question). |

Exit codes (`exit_code` is mirrored into the `-Json` frame):

| Code | Meaning |
|---|---|
| 0 | success: installed / repaired / already current / dry-run plan |
| 1 | unexpected internal failure |
| 2 | usage error (bad parameters, unsupported `-ProtocolVersion`) |
| 3 | environment error (not Windows x64, no `%LOCALAPPDATA%`, unwritable staging) |
| 4 | release resolution failed (release/asset not found, no assets, draft) |
| 5 | GitHub API rate limited (unauthenticated 60/h) — pin with `-Version` |
| 6 | download failed (HTTP error, timeout, empty or undersized payload) |
| 7 | integrity blocked (no SHA256SUMS entry, malformed digest, digest mismatch, contract disagreement) — nothing installed |
| 8 | install failed (Inno setup.exe non-zero exit, ZIP expansion failure) |
| 9 | post-install verification failed (`version` / `verify-release`) |

State and provenance are recorded in
`%LOCALAPPDATA%\NexusScalpEngine\state\install-release.json` (version, asset
name, expected/actual SHA-256, verification mode, `install_state` of
`AVAILABLE` / `DEGRADED` / `BLOCKED`, download telemetry) — the same
truth-table contract the bootstrap installer uses
([`docs/INSTALL_INTEGRITY.md`](INSTALL_INTEGRITY.md)). A bounded log is kept at
`%LOCALAPPDATA%\NexusScalpEngine\logs\install-release.log`.

User data (`config`, `logs`, `data`, `models`) is never deleted or
overwritten: the script only adds its own state and log files under the same
root. Re-running it over an already-current install exits 0 without
reinstalling.

## What it installs and where

Everything lives under `%LOCALAPPDATA%\Nexus` by default (override with
`-NexusHome` or the `NEXUS_HOME` environment variable):

```text
%LOCALAPPDATA%\Nexus\
    engine\            Nexus source (git checkout of NexusTradingForexBot)
    venv\              Python virtual environment (managed, outside the repo tree)
    bin\               managed uv + nexus.cmd launcher (PATH entries)
    git\               managed portable Git (only when no system Git is usable)
    config\            user configuration (live.yaml) - survives updates
    state\install.json non-secret install metadata (versions, HEAD, last stage)
    logs\installer.log bounded installer log (no secrets)
```

User data separation: **updating or reinstalling never touches** your
configuration (`config\`), models, research artifacts, or trading data. The
installer never deletes the engine directory wholesale; broken or partial
checkouts are moved aside to `engine.broken-<timestamp>` instead.

## Installer parameters

| Parameter | Purpose |
|---|---|
| `-NexusHome <path>` | Installation root (default `%LOCALAPPDATA%\Nexus`; `NEXUS_HOME` env var respected). Precedence: parameter > env var > default. |
| `-InstallDir <path>` | Engine checkout directory (default `<NexusHome>\engine`). |
| `-Branch <name>` | Branch to install/update (default `main`). |
| `-Tag <tag>` | Install exactly this tag (overrides branch). |
| `-Commit <sha>` | Install exactly this commit (highest precedence). |
| `-ForceCommit` | Allow a commit pin to roll the checkout BACKWARDS (downgrades are refused without it). |
| `-PythonVersion <ver>` | Override the canonical Python minor (default 3.11; fallbacks 3.12/3.13 are used and reported when 3.11 is unavailable). |
| `-NoVenv` | Skip venv creation/dependency install (advanced). |
| `-SkipOptional` | Skip optional heavyweight stages (none mandatory today). |
| `-NonInteractive` | Disable every prompt; all decisions from defaults/parameters. |
| `-Json` | Full install with a JSON summary frame on stdout. |
| `-Manifest` | Print the stage manifest JSON and exit (no mutation). |
| `-ProtocolVersion` | Print the stage-protocol version and exit. |
| `-Stage <name>` | Run a single stage and print one JSON result frame. |
| `-ShowResolvedPaths` | Print resolved paths JSON and exit (no mutation). |
| `-Ensure <dep>` | Lazily ensure a named dependency: `python`, `git`, `mt5`, `node`. |
| `-PostInstall` | Read-only post-install posture report (model/MT5). |
| `-Repair` | Repair runtime pieces (runtime/venv/dependencies/path/verify) without touching user data or the repository checkout. |
| `-DryRun` | Print the install plan as JSON and exit; no filesystem mutation. |

### Version pinning semantics

Precedence: **Commit > Tag > Branch**.

- Fresh install with `-Commit <sha>`: exact detached checkout; HEAD is verified
  against the requested commit after acquisition.
- Existing install with `-Commit <sha>`: applied only when it would not move
  the checkout backwards. A pin that is an ancestor of current HEAD is skipped
  with a warning unless you pass `-ForceCommit` explicitly.
- Existing install without a pin: `git fetch` + fast-forward update of the
  branch. Local changes are stashed (including untracked files) before the
  update and restored afterwards; on restore conflicts the stash is preserved
  and the exact restore command is printed. Nothing is discarded silently.

## Stage protocol (for drivers / GUI / CI)

The installer is drivable one stage at a time:

```powershell
.\install.ps1 -Manifest                 # what stages exist (JSON)
.\install.ps1 -Stage runtime -Json      # run one stage
.\install.ps1 -Stage verify -Json
```

- In `-Json` / `-Stage` mode stdout carries **only** documented JSON frames;
  human-readable diagnostics go to stderr.
- Exit codes: `0` success or deliberate skip, `1` stage failure, `2` unknown
  stage.
- Stage result frame:

```json
{"stage":"runtime","ok":true,"skipped":false,"reason":null,"duration_ms":1234}
```

- Stages: `environment`, `runtime`, `git`, `node`, `repository`, `venv`,
  `dependencies`, `node-deps`, `config`, `path`, `verify`, `state`.

## Update / recovery / repair

Running the installer again over an existing installation is safe and
idempotent: healthy pieces are detected and skipped (or fast-forwarded), a
partially completed install resumes from where it stopped (per-stage progress
is recorded durably in `state\install.json` after every successful stage),
and a corrupted checkout is moved aside and re-acquired. Individual broken
pieces can be repaired without a full reinstall:

```powershell
.\install.ps1 -Stage venv -Json        # recreate the venv transactionally
.\install.ps1 -Stage dependencies -Json
.\install.ps1 -Stage verify -Json
.\install.ps1 -Repair                  # full runtime repair (no user-data loss)
.\install.ps1 -DryRun                  # show the plan as JSON, mutate nothing
```

A single-writer install lock (`state\installer.lock`) prevents two
concurrent installers from mutating the same installation; a second
installer reports a well-formed skipped frame instead of corrupting state.
If the network fails mid-install, simply re-run the same command; completed
stages are detected and skipped.

## Download integrity (INSTALL-VERIFY)

Every artifact the installer downloads passes an integrity gate before it is
used:

- **Partial-file staging + atomic move** — an interrupted download can never
  be mistaken for a complete artifact.
- **Size floor (`-MinBytes`) + empty-file rejection** — truncated downloads
  are retried, never installed.
- **SHA256 pin (`-ExpectedSha256`)** — when a digest is configured (e.g. the
  PortableGit pin in the script, or a release checksum manifest), a mismatch
  **BLOCKS** the install: the artifact is deleted and the stage fails with the
  expected vs actual digest.
- **Telemetry** — every download records host, byte count, attempt count,
  expected/actual SHA256, verification mode and outcome into
  `state\install.json` (`download_telemetry`) and `logs\installer.log`.
- **Availability states** — `state\install.json:install_state` is
  `AVAILABLE` (verified complete), `DEGRADED` (usable but via a fallback
  path: ZIP acquisition, unhashed third-party asset, partial progress) or
  `BLOCKED` (a failed gate). Never trust "the download started" as "the
  install succeeded" — check the state file.

The complete download → install → activation → first-run truth table lives in
`docs/INSTALL_INTEGRITY.md`.

## Python / Git / Node details

- **Python**: provisioned user-scoped through `uv` into `<NexusHome>\bin`.
  Canonical version 3.11 (matches `pyproject.toml` `requires-python >=3.11`);
  3.12/3.13 fallbacks are used and reported if 3.11 cannot be provisioned.
  Microsoft Store python stubs are detected and rejected.
- **Git**: an existing system Git is used if present and healthy (including a
  Git Bash child-process probe). Otherwise a user-scoped PortableGit is
  installed to `<NexusHome>\git` - your system Git is never modified.
- **Node**: optional. Nexus is Python-only today; if a future checkout ships
  a `package.json`, the installer runs `npm ci` (lockfile-authoritative) with
  an `npm install` fallback.

## MT5 and the local model

- **MT5 is an external/online component.** The installer never installs,
  updates, restarts, or modifies your MetaTrader 5 terminal. It only detects
  whether the `MetaTrader5` Python package is importable (`-Ensure mt5`).
  The package itself is a Windows-conditional dependency of `pyproject.toml`
  and is installed automatically with the rest of the dependencies.
- **The model is local/offline.** Model artifacts (the 70D `scalp_v3` bundle)
  are application-owned. The installer never downloads, replaces, or
  migrates model artifacts. Verify model health after install with:

```powershell
nexus version
nexus doctor
```

## Uninstall boundaries

Deleting `%LOCALAPPDATA%\Nexus\engine`, `venv`, `bin`, and `git` removes the
software. Keep `%LOCALAPPDATA%\Nexus\config` (your configuration) plus any
models/research/trading data outside the install root unless you explicitly
want them gone. No registry entries are created beyond the `NEXUS_HOME`
user environment variable and User PATH entries pointing at `<NexusHome>\bin`.

## Troubleshooting

- `.\install.ps1 -ShowResolvedPaths` - what paths the installer resolved
  (first question for any path-related failure, especially on Windows
  profiles with spaces/Unicode in the username).
- `%LOCALAPPDATA%\Nexus\logs\installer.log` - per-stage log.
- `.\install.ps1 -Stage <stage> -Json 2>install-stderr.txt` - capture a
  single stage's machine-readable result plus its human diagnostics.
- Corporate proxies: the uv installer tries astral.sh then the GitHub
  mirror; repository acquisition tries SSH, HTTPS, then a ZIP archive.
- "venv locked" errors mean a Nexus process is running; stop the engine and
  re-run the venv stage (the previous venv is parked, never force-deleted).
