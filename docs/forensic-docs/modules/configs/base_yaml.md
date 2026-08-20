# configs/base.yaml + configs/live.yaml(.example)

- **PURPOSE:** Default bootstrap configuration (`base.yaml` — the safe
  defaults: PAPER mode, EURUSD, conservative risk 0.5%/1 position, candle
  intel + news enabled, DB hygiene on with dry_run/apply_deletes=false)
  and the live runtime example (`live.yaml.example`; `live.yaml` is the
  operator's runtime file — NEVER the authoritative settings source at
  runtime, per runtime_config.md).
- **ARCHITECTURE LAYER:** Configuration (bootstrap/import/export layer).
- **RESPONSIBILITY:** (a) default values the engine boots with;
  (b) operator overrides;
  (c) the config file is a PROJECTION — after startup the engine consumes
  the RuntimeConfigStore snapshot; live.yaml = export for diagnostics/
  legacy tooling.
- **CONNECTS TO:** AppConfig.load_from_yaml, CLI, runtime config store.
- **KEY CONCEPTS:** DB-hygiene SAFE_CLEAN only applies when
  `apply_deletes=true` AND execution.mode != LIVE; forensic_report is
  opt-in (enabled=false); telegram credentials NEVER live here at runtime
  (INV-010 — migrated to the secure store, blanked from YAML).
- **EDGE CASES & PITFALLS:** live.yaml.example vs the packaged config
  must stay in sync (a package built with a stale example ships stale
  defaults); the packaged EXE reads its config from the user data dir
  (%LOCALAPPDATA%\NexusScalpEngine), not the repo configs/.