# src/nexus_scalp/cli/main.py

- **PURPOSE:** The primary Typer CLI (`nexus` / `nse`) — the operator
  surface: start/stop/restart/run (engine lifecycle), doctor/health/status/
  test/logs/config/settings/repair/audit-purge/diagnostics/export/
  verify-release, the full update engine (`update`/`release` with
  check/status/history/rollback/doctor + --channel/--dry-run/--force/
  --json + exit codes), setup wizard (install/setup/uninstall), model
  factory commands (model-dataset-build/experiment-create/train/inspect/
  validate), db commands (delegated to cli/db_commands.py), incident
  commands (cli/incident_commands.py).
- **ARCHITECTURE LAYER:** CLI (entrypoint).
- **RESPONSIBILITY:** (a) parse + validate CLI args (Typer), (b) load
  AppConfig (YAML+env), (c) build the engine graph and run it
  (`_run_engine` → `_start_web_and_engine` — web server as background
  task, engine loop main), (d) daemon spawn via pidfile + `_spawn_daemon`
  (background process management), (e) human vs JSON output
  (`_emit(data, as_json, plain)` + verdict styling), (f) update
  orchestration (`_update_orchestrator`, `_update_exit_code` — exit 5 =
  update not applicable/failed, additive), (g) doctor health entries
  (`_health_entries`) — the pre-flight checks.
- **DEPENDENCIES:** typer, every subsystem (engine, config, settings,
  update engine, model factory, db, incidents, release health), pydantic.
- **CONNECTS TO:** shell, LiveEngine, web server, update engine, tests
  (test_cli_db_phase18, test_release_update_phase17, test_train_model_cli).
- **KEY CONCEPTS:**
  - Safe defaults: `nexus start` → PAPER mode (never silently LIVE);
    `start --mode live` requires interactive confirmation; SHADOW = live
    feed, zero orders.
  - Mode precedence: config file mode respected; the packaged EXE starts
    --mode from the config file (NO-OP etc. — release metadata).
  - Setup wizard (`_wizard_flow`) — first-run compatibility report →
    mode → symbol → health check.
- **EDGE CASES & PITFALLS:** daemon spawn must detach cleanly (pidfile,
    no zombie); update commands must translate engine reports into the
    unified exit-code contract; JSON mode must ALWAYS include exit_code;
    the CLI is the operator's failure-reporting surface — never swallow
    engine errors into a fake green (the "NO FAKE GREEN" rule).