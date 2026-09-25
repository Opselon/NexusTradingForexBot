# tests/helpers/shadow70_fixtures.py

- GUARDS: Shared fixtures for the 70D shadow test suites (TASK-05-70D-SHADOW): build a fake 70D contract the shadow engine can attach to, plus a temp artifacts dir.
- KEY ASSERTIONS: none (pure fixture providers).
- PITFALLS IT ENCODES: registered from conftest (pytest.register_assert_rewrite) — plain helper modules' fixtures only work because conftest.py imports the module and pre-registers assert rewriting; the shadow suites consume `contract` and `tmp_artifacts` exactly as the live shadow wiring does.
- NOTES: `make_contract(...)` builder + `vector70`, `tmp_artifacts`, `contract` fixtures. Backs test_shadow70_* and test_shadow_phase11.
