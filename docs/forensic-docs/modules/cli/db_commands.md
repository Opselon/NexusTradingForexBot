# src/nexus_scalp/cli/db_commands.py + cli/incident_commands.py + cli/__init__.py

- **PURPOSE:** The `nexus db ...` (database commands: health/status,
  migrations, hygiene status/plan/run/pause/resume/history, purge) and
  `nexus incident ...` (incident response: list/show/telegram/archive)
  command groups, delegating to their subsystem engines. `cli/__init__.py`
  is the package surface.
- **ARCHITECTURE LAYER:** CLI (entrypoint extensions).
- **RESPONSIBILITY:** Keep main.py from bloating further — DB and incident
  command groups live in their own modules with the same emit/JSON/exit
  conventions as main.
- **DEPENDENCIES:** typer, database migrations engine, hygiene worker
  interfaces, incident store/telegram.
- **CONNECTS TO:** shell, database/hygiene/incidents subsystems, tests
  (test_cli_db_phase18, test_database_migrations_phase18).
- **KEY CONCEPTS:** The hygiene CLI honors AUDIT_ONLY defaults — a
  destructive run requires explicit `--mode SAFE_CLEAN --apply`; incident
  commands read-only unless archiving.
- **EDGE CASES & PITFALLS:** command groups must reuse the shared emitters
  (main `_emit` semantics) so JSON output stays machine-parseable across
  the whole CLI.