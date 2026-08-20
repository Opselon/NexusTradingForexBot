# tests/unit/test_runtime_config_hot_reload.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Authoritative runtime-config hot-reload tests (MASTER ACCEPTANCE §65-§72): config change takes effect WITHOUT process restart (same PID / same engine instance).
- Guards: UI-style save persists + increments config version + changes deterministic behavior (`test_save_changes_deterministic_behavior_without_restart`); invalid config rejected keeping LAST-KNOWN-GOOD; cross-field rejection is ATOMIC; unknown key rejected.
- `test_live_yaml_is_not_authoritative` (TestLiveYamlIsNotAuthoritative): editing the YAML file ALONE does NOT change runtime — the config store/API is authoritative; file edits alone are ignored (documents the hot-reload architecture).
- Restart persistence: a NEW store restores persisted values.
- Config domains: snapshot is IMMUTABLE; snapshot→flat roundtrip; frozen SL / min-RR fixtures (`_frozen_algo_sl`, `_frozen_min_rr`).
- 17 defs / 255 lines.