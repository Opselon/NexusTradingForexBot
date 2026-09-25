# src/nexus_scalp/cli/incident_commands.py

- **PURPOSE:** The `nexus incident ...` command group — incident-response
  CLI (list/show/report/telegram/archive) delegating to the incidents
  subsystem (store + telegram + reports).
- **ARCHITECTURE LAYER:** CLI (entrypoint extension; see
  modules/cli/db_commands.md for the shared conventions).
- **RESPONSIBILITY:** read-only incident surfacing + explicit archive
  actions; reuses the main CLI's emit/JSON/exit-code conventions.
- **DEPENDENCIES:** typer, incidents store/telegram/reports.
- **CONNECTS TO:** shell, incidents runtime, /api/diagnostics/*.
- **KEY CONCEPTS:** incident ids/correlation ids (EXEC-...) are the join
  keys between CLI, API, and Telegram report surfaces.
- **EDGE CASES & PITFALLS:** archive/telegram commands must confirm before
  side effects (destructive discipline); JSON mode must stay
  machine-parseable.