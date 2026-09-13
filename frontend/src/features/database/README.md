# database — bounded context

Persistence console (legacy tab-database): provider + domain health, schema/
migration state, hygiene worker, PG config form (shared validation; password
pair cross-check mirrors PASSWORD_MISMATCH — values go to the OS SecretStore,
never round-trip), test-connection, dry-run preview, backup + migrate guarded
by TYPED confirm (confirm:true), live progress polling while a job runs,
last report/validation, SSMS-style explorer + read-only SQL console + named
API keys. EDD: diagnostics_state_routes.py:1102-1450, db_console.py,
debug_research_routes.py:1746 (db/status).
