# Agent 7 — forensic finding note (scratch evidence)

## Finding TDF-F1: test_live_engine_regime_state_freshness.py has an environment-dependent assertion

- SURFACE: tests/unit/test_live_engine_regime_state_freshness.py::test_regime_state_max_age_sec_default_and_yaml_keys
- SYMPTOM (reproduced locally): KeyError 'algo' — the test reads
  `configs/live.yaml` and asserts `data["algo"]["regime_state_max_age_sec"] == 300.0`.
- ROOT CAUSE (proven): `configs/live.yaml` is a GIT-IGNORED, operator-owned
  file (`.gitignore`: configs/live.yaml; `git check-ignore` rc=0). It is the
  user's local runtime config, NOT a tracked contract artifact. The tracked
  `configs/base.yaml` DOES carry `algo.regime_state_max_age_sec: 300.0`
  (verified at HEAD). The operator's local live.yaml currently only has a
  `risk:` section, so the assertion fails HERE while passing on machines
  whose live.yaml happens to include the algo block (as it did at the
  original author's machine — commit 19f8673e landed green).
- CLASSIFICATION: test defect (environment-dependent test), NOT an
  implementation defect. The CONFIG LOADER chain is
  user-config -> live.yaml -> base.yaml (cli/engine_boot.py) and
  AppConfig.load_from_yaml MERGES onto dataclass/pydantic defaults, so a
  live.yaml without the algo section is fully valid: the default 300.0
  (config.py AlgoConfig.regime_state_max_age_sec, ge=1 le=86400) applies.
  The runtime snapshot also defaults it (runtime_config.py:102).
- WHY THIS MATTERS (data-flow mission): an env-dependent red test erodes the
  gate signal for everyone in the swarm. The CONTRACT that actually matters
  is: default == 300.0 == base.yaml value == runtime snapshot value, and
  live.yaml (when it carries the key) OVERRIDES consistently. That is what
  the test must pin.
- FIX (Agent 7): pin the real contract without depending on operator-owned
  file contents:
    1. AlgoConfig().regime_state_max_age_sec == 300.0 (unchanged)
    2. base.yaml (TRACKED) carries 300.0 (unchanged)
    3. live.yaml is checked ONLY if it exists AND carries an 'algo' section:
       then the key must equal the AlgoConfig default (override consistency)
    4. tracked live.yaml.example (when present) must not contradict the
       default (it omits the key today -> falls under default; assert only
       when present)
  This keeps the original intent (override consistency across config layers)
  and removes the false dependency on a git-ignored operator file's shape.
