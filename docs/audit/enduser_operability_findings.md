# END-USER OPERABILITY FORENSIC REPORT
### Lane: ENDUSER-OPERABILITY-HARDENING · branch `agent/enduser-operability-hardening`

Product under test: the **real shipped v9.0.14 installer**
(`NexusScalpEngine-9.0.14-win-x64-setup.exe`, downloaded from the GitHub release),
installed and uninstalled on this Windows machine. Every claim below is traced to
an executed command against that artifact or to the source that built it.

---

## 0. Executive answer

**No — a non-developer cannot currently use NSE without developer tooling, and
two of the blockers actively put a stranger's trading data on their machine.**

The install itself works (silent install exit 0, no Python/Node/npm/Git required).
What fails is everything after "the installer finished":

| # | Finding | P | User-visible consequence |
|---|---------|---|--------------------------|
| EU-01 | The shipped installer contains the CI build machine's runtime databases, paper state and logs | **P0** | A fresh user opens NSE and sees a stranger's 52-table audit history, 23 trade signals and 405 news rows — a stranger's trading history, presented as theirs. `nexus db status` then reports `DB_MIGRATION_PENDING` on databases the installer just delivered. |
| EU-02 | `nexus uninstall` deletes `{app}` wholesale via `[UninstallDelete]` while the dialog says data is preserved | **P0** | The user's own databases, paper state and logs are permanently destroyed. There is no recycle bin — `shutil.rmtree` semantics on Windows. |
| EU-03 | No browser ever opens, no tray icon, no window — the app is a headless web server the user must find by port | **P1** | Double-clicking the Start Menu icon shows a console with `Uvicorn running on http://127.0.0.1:8080` and nothing else. The only way to the dashboard is knowing the URL. |
| EU-04 | The app binary is `NexusScalpEngine.exe`; the installer's AppName and every shortcut caption say `NexusTraderBot` | **P1 (owned by TASK-WINDOWS-UX-001)** | Taskbar, Start Menu, Add/Remove Programs and docs all disagree with the executable the user is looking at. Support search and pinning break. Reverted here — see §3. |
| EU-05 | `nexus logs` + the LOGGING health check glob a path the engine never writes | **P1** | `nexus doctor` reports "no log files yet" on every real install while a full `logs/<severity>/<YYYY>/<MM>/` tree exists elsewhere. A user reporting a problem is told they have no logs. |
| EU-06 | Port 8080 occupied → raw `PortOccupiedError`, exit 1, no remediation | **P1** | A user with anything on 8080 (or a previous NSE instance) gets a fatal crash with no next action and no port fallback. |
| EU-07 | Two post-install tasks ran concurrently (`nowait`) | **P1** | The interactive setup wizard races the health check on first launch. |
| EU-08 | No one-command support bundle | **P2** | A user reporting "NSE is not working" cannot produce a redacted diagnostic package without knowing what logs/config files exist. |
| EU-09 | No canonical product lifecycle state | **P2** | "is it running / ready / trading / armed / live" is scattered across `engine_running: bool`, `runtime_mode: str`, `data_source: str`. GUI, CLI and API can disagree and nothing is wrong. |
| EU-10 | No guided first-run readiness flow | **P2** | The user must know to run `nexus setup`; nothing walks them to a verified READY state. |

Evidence for the two P0s (executed, not read):

```
$ python -c "sqlite3 'target-app/artifacts/audit.db' ..."
  table_count: 52   user_version: 0   rows audit_signals: 23   rows news_articles: 405
$ # the same tree also carries audit.db-wal, data/paper_state.json, archive/, logs/
```

```
$ # v9.0.14 SHIPPED installer  (pre-fix behavior)
  [DELETED] artifacts/  data/  logs/  archive/   -> exit 0
  USER DATA LOST: audit.db paper_state.json engine.log
$ # installer rebuilt from THIS branch (post-fix behavior)
  [PRESERVED] artifacts/ data/ logs/ archive/    -> exit 0
  USER DATA PRESERVED: audit.db paper_state.json engine.log
```

---

## 1. Current real user journey (as executed)

### DOWNLOAD → INSTALL
Works. `install_sandbox.ps1` with the v9.0.14 setup.exe: **exit 0**, per-user
install under `%LOCALAPPDATA%\Programs`, no admin prompt, no Python/Node/npm/Git.
The install registers `HKCU:\Software\NexusScalpEngine` `DataRoot`.

### FIRST LAUNCH
The user double-clicks the **NexusTraderBot** Start Menu shortcut (EU-04) which
runs `NexusScalpEngine.exe`. What they get is a console that says
`Uvicorn running on http://127.0.0.1:8080` — and **never opens a browser**
(EU-03; a repo-wide grep for `webbrowser` returned zero hits before this lane).
There is no tray icon, no window, no toast. The dashboard exists but is
unreachable without knowing the URL.

### READINESS / MODE
`nexus doctor` reports installation health (config, model, DB, storage) but
**cannot** say whether the product is running, ready, armed or live. That
answer did not exist as one thing (EU-09).

### THE "WHERE IS MY PROGRAM?" QUESTION
Before this lane there was no `nexus dashboard` command at all. Now
`nexus dashboard` probes the recorded address, opens it if interactive, and if
nothing is answering prints exactly one next action.

### ERROR / RECOVERY
Port collision (EU-06) exits 1 with a raw error. Log discovery (EU-05) reports
the wrong directory. Uninstall (EU-02) destroys user data while promising it is
preserved. None of these had a recovery path.

### UPDATE
`build_release.ps1` stages the payload; the release workflow runs the packaged
EXE's start-smoke **before** staging (correct ordering for catching a broken
first-start) — but that smoke run *writes the CI machine's runtime state into
the payload*, and staging copies it verbatim (EU-01).

---

## 2. Personas A–T, resolved against evidence

| Persona | Verdict before this lane |
|---|---|
| A fresh user | Blocked at first launch by EU-01/EU-03/EU-04; received a stranger's trading history |
| B upgrading | At risk from EU-02 (data loss on uninstall path) and EU-01 |
| C normal restart | Fine |
| D previous crash | OK — pidfile + workspace re-anchored |
| E bad config | Partial: `nexus config --validate` exists |
| F MT5 unavailable | Out of scope — MT5 lane |
| G DB unavailable | `DB_MIGRATION_PENDING` false positive from EU-01's shipped databases |
| H occupied port | Blocked — EU-06 |
| I no internet | Fine (offline default) |
| J permissions | Fine — per-user, `PrivilegesRequired=lowest` |
| K broken optional component | OK — model-provision reports honestly |
| L own model | Supported, model-validate exists |
| M paper | Default, safe |
| N live | Requires `--yes` + confirmation (safe boundary, unchanged) |
| O invalid data | Partial — model-validate exists |
| P non-technical | Blocked — no browser launch, no state answer, no bundle |
| Q GUI-only | Blocked by EU-03 |
| R CLI-only | Partially served — no `dashboard`, no `logs --json` |
| S both | Terminology fractured by EU-04/EU-09 |
| T update failure | Out of scope — update-engine lane |

---

## 3. What this lane changed

**EU-01 (P0) — build payload hygiene.** New `scripts/build/clean_payload.ps1`
removes `artifacts/*.db*`, `data/`, `logs/`, `archive/`, `update/`, `nexus.pid`
and the never-ship security markers (`auth-key.txt`, `auth_key.pub`,
`.first_run`, `*lock`, `settings.json.bak`) from the bundle, preserving
`artifacts/models/`, `configs/`, `Web/`, `docs/` and all real application files.
It then hard-fails (exit 4) if any forbidden file survives. Wired in three
places: `build_release.ps1` before the onedir staging copy, `release.yml` as a
named `Payload hygiene gate (EU-01)` step before `Stage release tree`, and the
new test suite verifies the behaviour against the real script.

**EU-02 (P0) — uninstall data preservation.** Removed the blanket
`[UninstallDelete] Type: filesandordirs; Name: "{app}"` directive. Inno's own
uninstall log removes installer-written files; the previous directive
additionally deleted the runtime tree that `paths.py` anchors inside `{app}`
when frozen (`{app}\artifacts`, `{app}\data`, `{app}\logs`). The section now
states why it is empty, and the dialog text matches reality. Verified by real
install/uninstall on both the v9.0.14 artifact and a rebuilt installer:
SHIPPED deleted the data, FIXED preserved it.

**EU-03 (P1) — dashboard discovery.** New `nexus dashboard` command. It resolves
the engine's actual bound address (honouring the auto-incremented port), probes
it — any HTTP status means "running", transport failure means "not running" —
opens the browser when interactive (`NSE_NO_BROWSER`/`CI`/no-TTY suppress it),
and when nothing answers prints one actionable panel. It also refreshes the
`<app data root>/dashboard.url.txt` marker the installer writes, so the
"where is my program?" pointer is always the live address.

**EU-04 (P2, REVERTED to another lane) — product identity.** I verified the
shortcut captions say `NexusTraderBot` while the executable is
`NexusScalpEngine.exe`, wrote the fix, then found `agents/taskboard.md` assigns
the captions + `MyAppName` to **TASK-WINDOWS-UX-001** (IN_PROGRESS; PR #415
"expose NexusTraderBot application identity in taskbar" is already merged as
366cbe58 — the name is deliberate). Per the multi-agent contract I reverted the
caption change entirely. This lane keeps only the disjoint part it owns: a guard
that every `[Icons]` **target** points at the executable the installer ships
(a caption can outlive a build and leave a dead Start Menu entry). Asserted by
`test_eu04_shortcuts_target_the_shipped_executable`.

**EU-05 (P1) — one log-truth owner.** New `paths.get_engine_log_root()` is the
single owner of "where does the engine write logs" (`runtime_workspace/logs`).
`nexus logs` and the LOGGING health check both resolve through it, with the
legacy per-user root as a fallback, and both now recurse the real
`<severity>/<YYYY>/<MM>/*.log` layout. Before: `nexus doctor` said "no log
files yet" on a real install. After, on the same tree: `PASS ... 16 bytes,
last write 0d ago`. `nexus logs` also gained `--json`.

**EU-07 (P1) — postinstall race.** The two post-install tasks are now sequential
(`nowait` removed); the health check completes before the setup wizard starts.

**EU-09 (P2) — canonical product state.** New
`src/nexus_scalp/release/product_state.py` deriving four axes
(`application` / `engine` / `execution_mode` / `trading`) from the live engine
when it answers and reporting honestly when it does not, with per-axis reasons
and a single human summary. `nexus status` now carries it at the JSON payload
root — so `engine RUNNING` never implies trading, and `application READY`
cannot coexist with a stopped engine. Verified for: stopped, idle, paper,
live-armed, shadow-mismatch.

---

## 4. Out of scope (documented, not absorbed)

Per the exclusion list: frontend/Vite packaging, SQLite/PostgreSQL parity,
PG connection timeout, `model.pt` lifecycle, MT5 bootstrap, MT5/Replay/Backtest
parity, i18n, CodeQL alerts, Live Decision Trace, DB-fabric migration. The
EU-01 finding touches the release *pipeline* (a user-facing failure: a stranger's
data on the user's machine), not the DB layer itself — the integration change is
a hygiene gate, not a migration.

---

## 5. Evidence appendix

Executed commands and results (full output preserved in session history):

| Claim | Command | Result |
|---|---|---|
| Silent install works | `install_sandbox.ps1 -SetupExe ...v9.0.14...exe` | exit 0 |
| Shipped exe has no version metadata | `Get-Item ... .VersionInfo` | FileVersion/ProductVersion/CompanyName/FileDescription/ProductName all blank |
| Shipped installer contains build-machine DBs | `sqlite3 artifacts/audit.db` | 52 tables, 23 signals, 405 news rows, `user_version 0` |
| Shipped installer destroys user data | uninstall probe on v9.0.14 | artifacts/data/logs/archive deleted, exit 0 |
| Rebuilt installer preserves user data | uninstall probe on this branch | all preserved, exit 0 |
| Installer source compiles | `ISCC.exe NexusScalpEngine.iss` | `ISCC_RC=0` |
| EU-05 fix is real | health `check_logging` on a nested log tree | `PASS ... 16 bytes, last write 0d ago` (was WARNING) |
| State model derives correctly | `derive_product_state` over 5 cases | axes correct, READY⇏stopped, RUNNING⇏armed |
| New suites pass | `pytest tests/release/test_enduser_operability.py tests/cli/test_cli_enduser_journey.py` | 12 + 13 green |
| No `nexus help` regression | `pytest ...::TestHelp::test_help_is_fast_and_side_effect_free` x5 | 5/5 < 15s (my branch first regressed it to 36s via a top-level torch import; fixed by deferring heavy imports) |
