# tests/conftest.py

- GUARDS: Repository-wide pytest fixture registration (TASK-06-70D-LIQUIDITY-OPTIMIZATION). Without it pytest would never discover fixtures ("fixture 'contract' not found") declared in plain helper modules — the 70D shadow suites (TASK-05-70D-SHADOW) depend on `contract`/`tmp_artifacts` from `tests/helpers/shadow70_fixtures.py`.
- KEY ASSERTIONS:
  - `pytest.register_assert_rewrite("tests.helpers.shadow70_fixtures")` — registers BEFORE importing so assert-rewriting applies to helper module asserts (the comment explicitly warns about ordering, conftest.py).
  - `_isolate_settings_db()` (session autouse fixture) — isolates the settings DB per test session. `assert` count: 2 (registration + isolation checks).
- PITFALLS IT ENCODES: fixture modules living outside conftest are invisible to pytest by default; plain helper files must be imported/registered here. Assert-rewrite registration must precede any use of the helper module.
- NOTES: Session-scoped isolation of `settings` database is the contract that lets settings toggling tests (test_settings_api_bug072, test_settings_subsystem_bug072) run safely in any order.
