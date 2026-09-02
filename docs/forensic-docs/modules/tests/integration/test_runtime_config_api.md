# tests/integration/test_runtime_config_api.py

- GUARDS: Web-API hot-reload acceptance (§19/§40/§56/§65) — proves the EXACT paths the browser uses: PUT /api/algo/config → runtime applied + version + persisted (live.yaml written as projection, NEVER authoritative); POST /api/algo/toggle, GET /api/status versions.
- KEY ASSERTIONS:
  - `TestAlgoTunerApiHotReload`, `TestConfigApiHotReload`: config round-trip applies at runtime, version bumps, persisted projection stays consistent with runtime canonical settings (32 asserts).
- PITFALLS IT ENCODES: live.yaml is a projection — runtime config (DB/settings) is authoritative; toggles must not double-apply or restart the engine.
- NOTES: Regression net for the browser-driven tuner; pairs with unit test_runtime_config_hot_reload.py.
