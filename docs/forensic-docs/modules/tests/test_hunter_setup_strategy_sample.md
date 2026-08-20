# tests/unit/test_hunter_setup_strategy_sample.py + test_git_surveillance_task13.py + test_cli_db_phase18.py + test_logging.py

# test_hunter_setup_strategy_sample.py
- **GUARDS:** model_generation setup_detector + sample_maker +
  strategy_factory (the Hunter family).
- **KEY ASSERTIONS:** setup detection determinism (same bars → same
  detections); quality tiering math; best_strategy_for routing; sample
  id content-address determinism; tail-bounded label windows.

# test_git_surveillance_task13.py
- **GUARDS:** The TASK-13 git-surveillance lessons (parallel-swarm
  discipline) as regression guards.
- **KEY ASSERTIONS:** absorption detection (git show stat/log checks);
  registry row preservation rules; the rev-list left-right count
  staleness lesson (fetch before counting); CRLF-safe registry edits.

# test_cli_db_phase18.py
- **GUARDS:** cli/db_commands (`nexus db ...`) — status/migrations/
  hygiene command surface.
- **KEY ASSERTIONS:** command exit codes + JSON output shape; hygiene
  AUDIT_ONLY defaults (destructive ops need explicit flags); migration
  command idempotency; emitter conventions shared with main CLI.

# test_logging.py
- **GUARDS:** observability/logging — structlog setup + redaction.
- **KEY ASSERTIONS:** idempotent configure_logging; severity dirs;
  entropy redaction hides high-entropy strings; timestamp injection;
  capture-handler interactions.
- **PITFALLS IT ENCODES:** the structlog default PrintLoggerFactory trap
  (install capture handlers AFTER configure_logging; raiseExceptions
  False; restore in finally).