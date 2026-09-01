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
