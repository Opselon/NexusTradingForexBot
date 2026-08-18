# Nexus Scalp Engine — Release & Distribution

This document is the operational guide for the Release Engineering system.
The trading engine itself is documented in `agents/skill.md`; this file covers
packaging, installer, CLI, architecture support and release verification.

---

## 1. What a normal user gets

1. **Download** the installer (or portable ZIP) from GitHub Releases.
2. **Run** `NexusScalpEngine-<version>-win-x64-setup.exe` — compatibility check,
   install directory, shortcuts, uninstall entry.
3. **First-run wizard** (`nexus setup`): compatibility report → modes → symbol
   → health check. Defaults to **PAPER** — never silently LIVE.
4. **Run**: `nexus start` (paper) / `nexus start --mode shadow` /
   `nexus start --mode live` (explicit confirmation required).
5. **Health check**: `nexus health` / `nexus doctor`.
6. **Update / repair / uninstall**: `nexus update`, `nexus repair`,
   `nexus uninstall` — user data (config/logs/databases/models) is always
   preserved unless explicitly deleted.

No Python, pip, PyTorch or virtual-environment knowledge is required: the
installer bundles the complete Python runtime via PyInstaller.

## 2. Artifacts per release (windows-x64)

| Artifact | Path (under `release/vX.Y.Z/windows/x64/`) | Purpose |
| :--- | :--- | :--- |
| Installer | `NexusScalpEngine-X.Y.Z-win-x64-setup.exe` | normal users |
| Portable ZIP | `NexusScalpEngine-X.Y.Z-win-x64.zip` | portable / diagnostic users |
| CLI onefile | `cli/NexusScalpEngine-CLI.exe` | automation / ops |
| Onedir bundle | `portable/` (extracted layout) | diagnosis-friendly EXE tree |
| Checksums | `checksums/SHA256SUMS.txt` | integrity |
| Manifest | `manifests/release-manifest.json` | authoritative artifact map |
| SBOM | `sbom/sbom.spdx.json` | dependency inventory (SPDX-lite) |

## 3. Supported platforms (exact, evidence-based)

| Platform | Architecture | Status | Reason |
| :--- | :--- | :--- | :--- |
| Windows 10/11 | x64 | **SUPPORTED** | torch/polars/pyarrow/MetaTrader5 x64 wheels |
| Windows | ARM64 | **UNSUPPORTED** | no Windows ARM64 wheels for torch/polars/MetaTrader5 |
| Linux | x64 | developer/Docker only | remote-gateway adapter; no packaged release |

Only **windows-x64** is published. ARM64 is explicitly and loudly reported
unsupported (evaluator BLOCKED, update planner refuses ARM64 artifacts).

## 4. Build pipeline

Local (Windows):

```powershell
.\scripts\build\build_release.ps1 -SkipCleanInstallTest   # full build
.\scripts\build\verify_release.ps1                        # self-check
.\scripts\build\clean_install_test.ps1 -SetupExe <path>   # installer smoke
```

CI (GitHub Actions, `.github/workflows/release.yml`):

```
tag v9.0.0 ─► validate (tag==pyproject version, secrets scan)
           ─► gates (ruff / mypy / pytest unit+integration)
           ─► build windows-x64 (PyInstaller onedir + onefile)
           ─► EXE smoke ─► stage portable tree
           ─► Inno Setup installer ─► checksums + manifest + SBOM
           ─► verify-release (secrets scan, no-LIVE check)
           ─► publish GitHub Release (assets attached)
           ─► arm64-report job (explicit UNSUPPORTED)
```

Any failure stops the release — a broken artifact is never published.

## 5. Installer design (Inno Setup 6)

* Per-user install (`{localappdata}\Programs\NexusScalpEngine`) — no admin.
* **User data separated**: config/logs/databases/models live in
  `{localappdata}\NexusScalpEngine` — upgrades, repairs and uninstalls never
  touch them unless the uninstall checkbox is explicitly ticked.
* Idempotent: re-running the installer is an upgrade; databases are never
  reset (schema migration is handled by the engine's own phase stores).
* Architecture gate: refuses anything but x64 install mode with a clear
  message (no silently-broken ARM64 payload).
* Post-install: health check + first-run wizard launch.

## 6. CLI surface (`nexus`, also `nse` for legacy)

```
nexus version | health | doctor | status
nexus test --mode quick|unit|integration|health|release|all
nexus logs [--tail N] [--errors] [--worker] [--export file.zip]
nexus config [--validate path] [--show] [--json]
nexus repair [--recreate-config] [--news-db]
nexus diagnostics | export-diagnostics
nexus verify-release [--root dir]
nexus update [check|status|history|rollback|doctor] [--channel ...] [--dry-run]
nexus setup | install
nexus uninstall [--keep-data|--no-keep-data]
nexus start [--mode paper|shadow|live] [--gateway] [--daemon]
nexus stop | restart
```

### Exit codes (stable contract)

| Code | Meaning | Used by |
| :--- | :--- | :--- |
| 0 | success | all commands |
| 1 | runtime/validation failure | config invalid, health NOT READY, test failures |
| 2 | invalid usage | bad `--mode`, unknown command (Typer `BadParameter`) |
| 3 | environment blocked | ARM64 / unsupported platform (never install blindly) |
| 4 | release verification failure | tamper, checksum mismatch, secret found, missing artifact |

`--json` output for `verify-release` and `status` includes an `exit_code`
field so automation can branch without scraping stdout.

### Runtime test suite

```
.\tests\runtime\test_packaged_cli.ps1          # onefile CLI: help/version/health/doctor/status
.\tests\runtime\test_packaged_engine.ps1       # onedir EXE + LIVE-safety negative test
.\tests\runtime\test_health_runtime.ps1        # 19 categories on the real EXE
.\tests\runtime\test_no_python_dependency.ps1  # self-containment (stripped PATH)
.\tests\runtime\test_installer.ps1             # install/reinstall/user-data/uninstall
.\tests\runtime\test_repair.ps1                # damage + repair on the real EXE
.\tests\runtime\run_runtime_tests.ps1          # runs all of the above
```

Safety:
* `nexus start` defaults to **PAPER**.
* `--mode live` shows account/broker/symbol/risk/kill-switch and requires an
  explicit interactive confirmation.
* `--json` / `--plain` / `--no-color` for CI and automation (no ANSI in JSON).
* Packaged help strings are ASCII-only — non-ASCII (em dash, arrow) in a
  Typer `help=` string aborts the frozen onefile `--help` with
  `UnicodeEncodeError` on code-page consoles (BUG-037).

## 7. Health / Doctor

`nexus doctor` checks 19 categories (SYSTEM, RUNTIME, CONFIGURATION, DATABASE,
MODEL, FEATURE_SCHEMA, GPU, MT5, NETWORK, DISK, MEMORY, LOGGING, WORKERS,
NEWS, EXPERIENCE, RESEARCH, TRAINING, SHADOW, ACCOUNTING) and reports
READY / DEGRADED / NOT READY with exact reasons and suggested fixes.
`nexus export-diagnostics` creates a sanitized ZIP (no passwords/tokens/
credentials/database contents).

## 8. Update / Rollback (TASK-9 end-user update path)

`nexus update` is the FULL installed-user update system:

```
nexus update check      # discovery only (never fabricates latest)
nexus update            # check -> download -> verify -> backup -> migrate
                        #   -> install -> health -> COMPLETED
nexus update --dry-run  # full plan, zero mutation
nexus update --channel beta|nightly
nexus update status     # observable state machine + crash recovery
nexus update history    # persisted update log (jsonl, never credentials)
nexus update rollback   # restore prior application (user data intact)
nexus update doctor     # github/disk/mode/db/config/process/lock checks
nexus update --yes      # skip prompts (never bypasses security checks)
```

* Update source = GitHub Releases API (never main.zip / source archives).
* Version comparison is SEMANTIC (9.10.0 > 9.9.0); downgrades blocked.
* Every artifact must carry a SHA-256 digest; the embedded release manifest
  is verified inside the payload; unverified artifacts are discarded.
* User data (config/logs/databases/models/secret store) lives OUTSIDE the
  replaceable payload in %LOCALAPPDATA%\NexusScalpEngine and is backed up
  atomically before any migration. App-tree runtime dirs (artifacts/data/
  logs, legacy portable layout) are preserved across the swap (BUG-091).
* Telegram credentials stay in the DPAPI secure store (secrets.enc) + the
  isolated app_settings.db — an update NEVER moves them to plaintext.
* LIVE engines BLOCK the update (UPDATE_BLOCKED_WHILE_LIVE) unless the user
  explicitly invokes the documented --force maintenance quiesce flow.
* Rollback restores the prior application ONLY; database/config rollback is
  version-aware (old artifacts/data/logs never restored over migrated data).
* Crash mid-update is persisted; `nexus update status` reports
  ROLLBACK_REQUIRED — a half-installed state is never reported healthy.
* Single-instance lock (UPDATE_IN_PROGRESS); exit code 5 = update not
  applicable/failed (additive contract).

## 9. Reproducibility & security

* One canonical version: `pyproject.toml` (`version`), stamped into
  `build-info.json` → embedded in every artifact + `nexus version`.
* Every artifact has a SHA-256 in `SHA256SUMS.txt` and the manifest.
* Builds record git commit, dirty-tree flag, Python version, architecture,
  channel and build mode.
* Secrets scan runs in CI (gates + release) and in `verify-release`;
  `artifacts/audit.db`, dev DBs, credentials and configs are never packaged.
* Code signing: the pipeline supports an optional signing step when a
  certificate is configured; unsigned status is documented, certificates are
  never committed.

## 10. Docs for the docs-before-build rule

Read `agents/skill.md` first when touching the engine; this release system is
additive and never modifies trading logic except where startup compatibility
requires it.