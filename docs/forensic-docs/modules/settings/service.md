# src/nexus_scalp/settings/service.py + secret_store.py + paths.py + __init__.py

- **PURPOSE:** The canonical user/installation settings subsystem
  (BUG-077): Telegram credentials and runtime settings NEVER come from
  live.yaml at runtime.
- **ARCHITECTURE LAYER:** Settings (infrastructure — the authoritative
  settings source, INV-010).
- **RESPONSIBILITY:**
  - `secret_store.py` — `SecureSecretStore`: Windows DPAPI
    (CryptProtectData via ctypes DATA_BLOB), ciphertext anchored to the
    OS user; no plaintext, no XOR/base64 key.
  - `service.py` — `SettingsDatabase` (isolated `app_settings.db` under
    %LOCALAPPDATA%\NexusScalpEngine\databases\ with tables
    application_settings/configuration_metadata/settings_audit) +
    `SettingsService` with precedence: SYSTEM DEFAULT < INSTALLATION
    SETTINGS < SAFE ENV OVERRIDES < RUNTIME HOT. Mutability classes:
    HOT_SAFE / HOT_RESTRICTED / RESTART_REQUIRED / INSTALLATION_ONLY /
    SECRET. Explicit degraded states (SETTINGS_DB_CORRUPT,
    SECRET_UNAVAILABLE, MIGRATION_REQUIRED) — never fake READY.
    Token masking (`_mask_token`), correlation ids, type inference for
    stored values. Legacy migration: live.yaml telegram.bot_token →
    secure store, verified write-back → blanked from YAML (idempotent,
    restart-safe).
  - `paths.py` — settings DB location resolution (settings_db_path/
    settings_db_url).
- **DEPENDENCIES:** ctypes (DPAPI), sqlite3, pydantic, pathlib.
- **CONNECTS TO:** LiveEngine (`self.settings_service`; `[TELEGRAM_CONFIG]`
  startup log with enabled/configured/token_present/source), web
  (/api/settings mask+persist+hot-rebuild, /api/settings/telegram/status,
  /api/settings/validate), CLI settings, doctor (TELEGRAM check), tests
  (test_settings_subsystem_bug072, test_settings_api_bug072,
  test_telegram_forensics_bug072).
- **KEY CONCEPTS:** THE settings single-authority: UI telegram save MUST
  route via settings_service.set_telegram() — never live.yaml (INV-010/
  BUG-080 discipline); env override (NEXUS_TELEGRAM_*) remains the
  diagnosis escape hatch; every write is audited (settings_audit table).
- **EDGE CASES & PITFALLS:** DPAPI unavailable (non-Windows/CI) →
  SECRET_UNAVAILABLE degraded state (tested); migration must be
  failure-safe (a crash mid-migration leaves the legacy token intact);
  settings DB corruption must degrade explicitly, never fake READY.