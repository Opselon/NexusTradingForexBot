# INSTALL / UPDATE / REPAIR MATRIX — ENDUSER-OPERABILITY-HARDENING

All rows verified by execution against the real installer artifacts.

| Stage | Before this lane | After this lane | Evidence |
|---|---|---|---|
| **Fresh install** | Works, per-user, no admin, no Python/Node/npm/Git | unchanged | v9.0.14 setup.exe silent install → exit 0 |
| **First launch** | No browser, no tray, no window; console only | `nexus dashboard` + Start Menu "Dashboard" entry + live `dashboard.url.txt` pointer | `webbrowser` grep = 0 pre-lane; new command + journey tests |
| **Upgrade (install over)** | Preserved by idempotent `[Files]` | unchanged | `[Files]` never deletes user data |
| **Repair (reinstall)** | OK | unchanged | idempotent re-install path |
| **Restart** | OK | unchanged | pidfile re-anchor |
| **Uninstall — keep data (default)** | **DESTROYED** `{app}` wholesale: artifacts/, data/, logs/, archive/ | **PRESERVED** — Inno's own log removes installer-written files only | uninstall probe v9.0.14 → USER DATA LOST; probe on rebuilt installer → PRESERVED (both exit 0) |
| **Uninstall — delete data (explicit tick)** | removes both locations | unchanged, explicit only | `[Code] CurUninstallStepChanged` |
| **Reinstall after uninstall** | the shipped DBs came back (EU-01) | clean, NOT_INITIALIZED | EU-01 hygiene gate + test suite |
| **Broken state recovery** | DB_MIGRATION_PENDING false alarm on installer-delivered DBs | gone — DBs are no longer shipped | v9.0.14 `sqlite3 artifacts/audit.db` → 52 tables, `user_version 0` |
| **Update payload cleanliness** | CI machine state staged verbatim (52 tables, 23 signals, 405 news rows, paper_state.json, logs/, archive/) | hygiene gate cleans + hard-fails (exit 4) on any survivor, before staging | `clean_payload.ps1` behaviour tests; `release.yml` gate step |

## Files that are user-owned (never removed by repair/update/uninstall)

| Category | Location | Regenerable | Survives update | Survives uninstall (default) |
|---|---|---|---|---|
| user config | `<LocalAppData>\NexusScalpEngine\config\` | no | yes | yes |
| user logs (legacy root) | `<LocalAppData>\NexusScalpEngine\logs\` | no | yes | yes |
| models | `<LocalAppData>\NexusScalpEngine\models\` | no | yes | yes |
| runtime DBs (frozen) | `<{app}>\artifacts\audit.db` etc. | no | yes (idempotent `[Files]`) | **yes now** (EU-02) |
| paper state (frozen) | `<{app}>\data\paper_state.json` | no | yes | **yes now** (EU-02) |
| runtime logs (frozen) | `<{app}>\logs\` | yes | yes | **yes now** (EU-02) |
| diagnostics bundle | `<data root>\diagnostics\` | yes | yes | yes |
| application binaries | `<{app}>\` | **yes (reinstall)** | yes | removed (correct) |

## Never shipped (EU-01 gate, hard-fail)

`auth-key.txt`, `auth_key.pub`, `app_secrets.enc`, `.first_run`,
`release.lock`, `update.lock`, `settings.json.bak`, `*.db`, `*.db-wal`,
`*.db-shm`, `*.db-journal`, `paper_state.json`, `nexus.pid`, `*.log`.

Preserved as intentional payload: `artifacts/models/**`, `configs/**`,
`Web/**`, `docs/**`, `_internal/**`, `NexusScalpEngine.exe`.
