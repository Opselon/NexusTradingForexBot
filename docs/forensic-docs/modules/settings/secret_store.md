# src/nexus_scalp/settings/secret_store.py + paths.py

- **PURPOSE:** Secure credential storage + path resolution for the
  settings subsystem: `SecureSecretStore` (Windows DPAPI via ctypes —
  CryptProtectData/CryptUnprotectData with DATA_BLOB structs; ciphertext
  anchored to the OS user; no plaintext, no XOR/base64 keys) and
  `settings_db_path`/`settings_db_url` (the isolated app_settings.db
  under %LOCALAPPDATA%\NexusScalpEngine\databases\).
- **ARCHITECTURE LAYER:** Settings (infrastructure security boundary).
- **RESPONSIBILITY:** (a) encrypt/decrypt secrets with OS-keyed DPAPI;
  (b) provide the settings-DB location the SettingsDatabase uses.
- **DEPENDENCIES:** ctypes (bcrypt.dll CryptProtectData), pathlib.
- **CONNECTS TO:** SettingsService (token load/save), the migration path
  (live.yaml → secure store with verified write-back → YAML blanked),
  tests (test_settings_subsystem_bug072, test_telegram_forensics_bug072).
- **KEY CONCEPTS:** `DATA_BLOB` marshaling + `_local_free` (LocalFree)
  handle the Win32 memory contract; DPAPI binds ciphertext to the user
  profile (a token encrypted on machine A cannot decrypt on machine B —
  by design); failure raises SecretStoreError (→ SECRET_UNAVAILABLE
  degraded state upstream).
- **EDGE CASES & PITFALLS:** non-Windows/CI environments have no DPAPI —
  callers must catch SecretStoreError and degrade explicitly (never fake
  READY); the store must never log ciphertext or plaintext.