# End-User Release Wave — Audit Findings (2026-09-23)

> Lane: `agent/hermes/end-user-release` (worktree `source/repos/nse-eu-release`, base `origin/main` b3038b99)
> Scope: the end-user installation / CLI / packaging / first-run surface (master brief: every §)
> Method: real-subprocess CLI probes from a clean directory (never the dev checkout), static build-wiring audit, ICO container verification.

The audit tested the actual user-facing contract, not source-mode convenience.
Where a probe looked red and was not, that is recorded below as a **false
positive** rather than a fixed bug — the distinction matters for anyone
re-running this audit.

---

## Support matrix (discovered from evidence, not assumed)

| Surface | Supported | Evidence |
| :--- | :--- | :--- |
| Windows x64 | YES — the only packaged target | `installer/NexusScalpEngine.iss` `ArchitecturesAllowed=x64compatible`; `build_release.ps1` fails on non-x64; ARM64 explicitly refused by the Inno `[Code]` guard |
| Windows ARM64 | NO (deliberate) | `.iss` InitializeSetup blocks with the PyTorch/Polars/MetaTrader5 reason |
| Linux / macOS | Source-only (`pip install -e .[dev]`); no packaged artifact | no PyInstaller/linux build step in `.github/workflows/release.yml`; `docs/getting-started/installation.md` Path 3 |
| Python | 3.11+ (`requires-python >=3.11`) | `pyproject.toml` |
| GPU / CUDA | OPTIONAL — training-only, never needed to start | `nexus model-train-env` gating; PyTorch excluded from the CLI onefile; `docs/CLI.md` |
| Admin / elevation | NOT required — per-user install | `.iss` `PrivilegesRequired=lowest`; `install.ps1` "No admin anywhere in the code path" |
| MT5 terminal | Required only for LIVE/SHADOW; PAPER + diagnostics degrade honestly | `docs/CLI.md` safety invariants |

Three install routes (all documented, all audited): Inno Setup EXE / portable
ZIP (Path 1), PowerShell bootstrap installer (Path 2), developer source (Path 3).

---

## FIXED in this lane

### F1 — `nexus --version` was a usage error (contract violation, user-visible)

**Severity:** high — this is the first command a user types to check what they installed.

- Symptom: `nexus --version` -> exit **2**, `No such option: --version`.
- Root cause: the Typer app (`src/nexus_scalp/cli/app_factory.py`) registered no
  root callback, so Typer has no `--version` flag. The detailed command form
  `nexus version` existed, but the universal CLI convention did not.
- Fix: an eager root callback registering `--version`, printing the same
  one-line identity `nexus version --plain` produces (same producer, same
  test-patchable seam, full commit SHA — not a truncated 7-char form, which
  the first attempt shipped and the parity test caught).
- Regression tests: `tests/cli/test_cli_subprocess.py::TestVersion` (4 new:
  RC 0, byte-parity with `version --plain`, works from an unrelated CWD,
  genuine usage errors still exit 2).
- Doc: `docs/CLI.md` quick-start + command table now list `nexus --version`.

### F2 — No application icon on any shipped executable (branding gap)

**Severity:** medium — the product looked unfinished in exactly the surfaces a
user judges it on (taskbar, file explorer, Start Menu, uninstall entry).

- Symptom: both PyInstaller outputs carried PyInstaller's generic placeholder.
- Root cause: **three** independent build paths invoked PyInstaller and none
  passed `--icon`: `scripts/build/build_release.ps1` (2 builds),
  `scripts/release/build_artifact.py`, `.github/workflows/release.yml`
  (2 builds). `installer/NexusScalpEngine.iss` points
  `UninstallDisplayIcon` at the installed EXE, so the placeholder also leaked
  into the Start Menu / uninstall icon. No `.ico` existed in the repo.
- Fix:
  - `scripts/build/generate_app_icon.py` (new) generates
    `installer/NexusScalpEngine.ico` from the canonical brand asset
    `frontend/public/icon-512.png` (PWA manifest / Apple touch icon / OG card
    source — no new art). Multi-resolution: 256/128/64/48/32/16. Pillow is
    build-time only (never a runtime dependency).
  - All 3 build paths generate the icon before PyInstaller, pass `--icon`,
    and fail loudly if it is missing.
- Artifact: `installer/NexusScalpEngine.ico` (29,279 bytes) — verified as a
  genuine 6-entry ICO directory (reserved=0, type=1, 256/32/16 entries
  present), not a placeholder/garbage file.
- Regression tests: `tests/installer/test_app_icon.py` (8 tests) pin all three
  build paths + the ICO container + the Inno icon contract, so a dropped
  `--icon` fails loudly instead of shipping a placeholder again.

---

## FALSE POSITIVE recorded (probed red, actually correct — NOT a bug)

### FP1 — "`nexus repair --json` pollutes stdout with log lines"

An in-process `typer.testing.CliRunner` probe showed structlog lines
("Initialized High-Performance SQLite WAL storage") mixed into the `--json`
output, which would break every JSON-parsing caller.

**Re-tested with a real subprocess (separate stdout/stderr capture): stdout is
100% pure JSON.** The CliRunner harness merges stderr into stdout by default,
creating the illusion. The CLI's documented contract holds; no code changed.

Lesson for re-runners: the JSON-purity contract can only be proven with a real
subprocess, never in-process. This class of probe artifact is why the golden
CLI suite (`tests/cli/test_cli_subprocess.py`) exists.

---

## Documented gaps NOT fixed in this lane (out of scope / needs design owner)

These are real findings, verified with evidence, but each touches documented
installer behavior or a wider blast radius than this lane should absorb. They
are recorded here so the next owner has the evidence without re-discovering it.

### G1 — The two installers write to two different user-data roots

- Inno Setup (Path 1) + the application: `%LOCALAPPDATA%\NexusScalpEngine`
  (config file `nexus.yaml`, from `release/paths.py::app_data_root`).
- PowerShell bootstrap (Path 2): `%LOCALAPPDATA%\Nexus`
  (config files `base.yaml` + `live.yaml`, from `installer/install.ps1`).
- Consequence: the templates Path 2 provisions in its `config` stage are never
  read by the application, which looks for `nexus.yaml` one directory over.
  Path 2 still works (the app falls back to hard defaults and bootstraps), so
  this is a silent-config-fragmentation issue, not a crash.
- Also: `NEXUS_HOME` is documented as a Path-2 override
  (`docs/INSTALL_WINDOWS.md`, `-NexusHome`) but the application reads no
  `NEXUS_HOME` variable at all — the documented override does not reach the app.
- Not fixed: unifying this means editing `release/paths.py` (hot-path-adjacent,
  every health check / repair / diagnostic resolves through it) plus both
  installers and the docs; it needs its own lane with invariant analysis.

### G2 — EXE version metadata (FileVersion / ProductVersion) is not stamped

PyInstaller gets no `--version-file`, so the Windows property sheet on the
shipped EXEs lacks FileVersion/ProductVersion/CompanyName. The application's
own version identity IS correct at runtime (`nexus version` reads the stamped
`build-info.json` payload — verified); this is only the shell-level metadata.
Deliberately not added: a malformed PyInstaller version file would RED the
release workflow, and generating a correct one is a follow-up that must be
validated against a real `ISCC`/`pyinstaller` run.

### G3 — First-run marker is written but never read

`installer/NexusScalpEngine.iss` writes
`%LOCALAPPDATA%\NexusScalpEngine\config\.first_run` ("the CLI reads this to
know the wizard should run"), but no `src/` code reads that file. The setup
wizard still launches through the documented `[Run]` post-install step, so the
user-facing behavior is correct — the marker is dead weight today and either
the reader was dropped or the comment overstates the contract.

---

## Verified working (evidence, not claims)

Probed as a real user from `C:\Users\...\AppData\Local\Temp\nse-user-sim`
(NEXUS_HOME redirected, cwd != repo, no repo-relative assumptions):

| Probe | Result |
| :--- | :--- |
| `nexus version --plain` | RC 0, one line, real identity |
| `nexus version --json` | RC 0, valid JSON on stdout, runtime_snapshot present |
| `nexus --version` | RC 0 (was 2) — byte-identical to `version --plain` |
| `nexus doctor --json` / `health --json` / `status --json` | RC 0, valid JSON |
| `nexus repair --json` | RC 0, **stdout pure JSON** (see FP1) |
| `nexus update check --json` | RC 0, honest `NO_UPDATE` |
| `nexus config --json` | RC 0, valid + path reported |
| `nexus logs` (no engine yet) | RC 0, friendly "No logs yet" panel with recovery hint |
| `nexus model-provision --status` | RC 1, honest `serving slot: missing` + recommended next action |
| `nexus verify-release` | RC 4 (release-verification contract, expected on a dev tree) |
| `nexus nonexistent-command` | RC 2, readable error panel + recovery hint (no traceback) |
| `nexus uninstall --help` | RC 0, keep-data default documented |
| CWD-independence | all probes ran from an unrelated directory; identity stable |

Safety invariants confirmed: no diagnostic command touches broker state;
`uninstall` defaults to keep-data; secrets masked in `config`/`settings`/JSON;
no elevation anywhere in either installer; user data physically separated from
application files in every documented route.
