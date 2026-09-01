# Nexus CLI Reference (`nexus`)

The `nexus` command is the **application/user-facing CLI**. The PowerShell
installer (`installer/install.ps1`) owns installation lifecycle; the CLI owns
operating Nexus: diagnostics, engine lifecycle, configuration, research, and
release management. Those boundaries never mix.

- Canonical entrypoints (`pyproject.toml` `project.scripts`):
  - `nexus` → `nexus_scalp.release.cli_shim:app` (packaged/installed command)
  - `nse` → `nexus_scalp.cli.main:app` (developer alias, identical surface)
- One authoritative help surface: `nexus --help` and `nexus help` are the
  same output; `nexus help <command>` shows that command's detail.

## Quick Start

```powershell
1. iex (irm https://raw.githubusercontent.com/Opselon/NexusTradingForexBot/main/installer/install.ps1)
2. Open a NEW PowerShell (PATH refresh)
3. nexus help            # discover the command surface
4. nexus version         # build identity
5. nexus doctor          # full system diagnostics (read-only)
6. nexus status          # health + environment + version
7. nexus config          # inspect the active configuration
8. nexus start           # PAPER mode by default, never LIVE silently
```

## Exit-code contract (stable, Typer/Click-consistent)

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | runtime / validation failure (honest, actionable) |
| 2 | usage error (unknown command/option, missing argument) |
| 3 | environment-blocked (safety policy refusal) |
| 4 | release verification failure |
| 5 | update not-applicable / failed (e.g. already-current reported honestly) |

`--json` modes emit **valid JSON only on stdout**; human diagnostics go to
stderr. Unknown commands/options print a readable panel + usage hint and exit
2 - never a traceback wall.

## Command reference

Verified against the real Typer app (`tests/cli/test_cli_subprocess.py`
asserts the golden list; run `nexus help` on your install for the live list).

### Core / lifecycle

| Command | Purpose | Side effects | JSON | Notes |
|---|---|---|---|---|
| `nexus help [cmd]` | Show the command reference (same as `--help`) | none | n/a | RC 0; unknown topic RC 2 |
| `nexus version` | Canonical version + build identity | none | `--json`, `--plain` | fast, no model/MT5/DB work |
| `nexus doctor` | Full system doctor (SYSTEM..ACCOUNTING) | none by default | `--json` | `--fix` mutates derived state only |
| `nexus health` | Quick READY / DEGRADED / NOT READY summary | none | `--json` | |
| `nexus status` | Health + environment + version | none | `--json` | read-only |
| `nexus start` | Start the engine (**paper default**) | starts engine process | — | `--mode live` requires explicit confirmation |
| `nexus stop` | Stop the background engine (pidfile-based) | stops engine | — | never kills arbitrary processes |
| `nexus restart` | stop + start | engine | — | explicit only |
| `nexus run` | Start with explicit config (legacy) | engine | — | |
| `nexus update` | Check/download/verify/install newest release | code + deps (bounded, verified) | `--json` | `check` is discovery-only; `--dry-run` plans; honest `NO_UPDATE` |
| `nexus repair` | Repair non-destructive derived state | derived state only | `--json` | NEVER deletes user data |
| `nexus setup` / `nexus install` | First-run wizard (compat → mode → health) | writes user config choices | — | default: PAPER |
| `nexus uninstall` | Uninstall helper; **keep-data default** | removes install (data kept unless explicit) | — | explicit intent required |
| `nexus release` | Installed release metadata | none | `--json` | |
| `nexus verify-release` | Verify a release tree (EXE/checksums/secrets) | none | — | |

### Configuration & diagnostics

| Command | Purpose | Side effects | JSON |
|---|---|---|---|
| `nexus config` | Inspect/validate active config | none | `--json`, `--show`, `--validate <path>` |
| `nexus config-validate` | Syntax/schema/migration/secret-masking validation | none | — |
| `nexus settings` | User-settings store (secrets masked) | none | `--json` |
| `nexus logs` | Tail/filter/export engine logs | read-only | — |
| `nexus diagnostics` / `nexus export-diagnostics` | Sanitized diagnostics archive (no secrets) | writes archive | — |
| `nexus forensic` | Forensic health matrix + deploy gate | read-only | `--json` |
| `nexus incidents` | Incident response diagnostics | read-only by default | — |
| `nexus test` | Run test suites (never live broker tests) | runs tests | — |
| `nexus analyze` | Static code diagnostics (dev tool) | none | — |

### Model factory (artifact-first; never touches the Champion automatically)

`model-dataset-build` · `model-experiment-create` · `model-train` ·
`model-train-3` · `model-validate` · `model-inspect` · `model-replay` ·
`model-doctor` — deterministic, artifact-first flows. Training candidates
never auto-promote; the 70D `scalp_v3` canonical contract is respected.

### Data / infrastructure

`db` (schema migration & management) · `db-portability` (SQLite ↔ PostgreSQL)
· `dependency` (dependency intelligence) · `audit-purge` (retention-bounded
telemetry purge).

## Safety invariants (verified by tests)

- No CLI diagnostic command sends MT5 orders or touches broker state.
- No CLI command silently replaces the champion model or downgrades the 70D
  `scalp_v3` contract.
- Destructive behavior (`uninstall`) requires explicit intent; keep-data is
  the default.
- Secrets are never echoed in `config`, `settings`, JSON, or logs.
- `nexus update` never silently downgrades and protects a dirty checkout
  (the installer's stash/restore semantics are authoritative).

## Configuration discovery

The CLI reads config via the application's `AppConfig` layer; discovery is
deterministic and independent of the caller's CWD (`nexus version/doctor`
behave identically from `C:\`, `%TEMP%`, an unrelated repo, or the Nexus
checkout).

## Troubleshooting

| Symptom | Action |
|---|---|
| `nexus` not found | Open a new terminal (PATH refresh); `Get-Command nexus` / `where.exe nexus`; re-run the installer `-Stage path -Json` |
| venv unhealthy / import errors | `nexus repair` (or `installer/install.ps1 -Repair`) |
| doctor reports DEGRADED | Read the failing category lines; `nexus doctor --json` for tooling |
| update fails | `nexus update check --json` for the honest state; check network/proxy |
| permission error | Install is user-scoped; ensure `%LOCALAPPDATA%\Nexus` is writable |
| MT5 unavailable | Start the MT5 terminal; diagnostics degrade gracefully and stay honest |
