# tests/unit/test_settings_api_bug072.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Web API integration tests for the BUG-072 settings/telegram architecture (§38/§39 matrices).
- Guards: GET /api/settings returns MASKED token status, NEVER plaintext; GET /api/settings/telegram/status truthful when missing; provenance shows source and version; every mutation audited.
- Notifier rebuild: saving telegram settings REBUILDS the notifier; clear-token DISABLES it.
- Config-save persistence: `_isolate_live_yaml` — token persists to the SECRET STORE, not live YAML; empty token does NOT wipe the store (`test_config_save_empty_token_does_not_wipe_store`); save rebuilds the notifier.
- Fixtures: `_make_engine`/_FakeEngine for API wiring; real AuditRepository-backed settings_service.
- 18 defs / 214 lines. Companion: test_settings_subsystem_bug072.py (isolated subsystem level).