# tests/unit/test_settings_subsystem_bug072.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Isolated settings subsystem tests (BUG-072): settings DB, secure secret store, telegram wiring — NO web layer.
- Settings DB: created on FIRST run; persists across restart; typed values roundtrip; version increments; CORRUPT DB reports DEGRADED state (no crash); every mutation audited; mutability classification enforced.
- Secure secret store: secret NEVER plaintext on disk; roundtrip; delete; absent → None (`store.get_secret("nope") is None`).
- SettingsService telegram: telegram token from secure store; missing token → EXPLICIT state (not a silent empty); enabled-without-token handled truthfully.
- 26 defs / 243 lines; `db_path`/`secret_root` tmp fixtures.
- NOTE: BUG-072 roots — plaintext tokens in live YAML and notifier rebuild on save are the two behaviors this suite pins.