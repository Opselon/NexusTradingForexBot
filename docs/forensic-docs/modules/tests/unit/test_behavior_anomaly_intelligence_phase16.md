# tests/unit/test_behavior_anomaly_intelligence_phase16.py

- GUARDS: Behavioral + Anomaly Intelligence — TASK-2 regression suite (TEST-BHV-01..20): trade-behavior flaws surfaced with evidence (overhold loser, profit giveback, missed breakeven, premature breakeven, model/regime/liquidity reversal ignored, risk deviation, exit anomaly, duplicate outcome, strategy-context loss).
- KEY ASSERTIONS:
  - NO_DATA state is explicit; every anomaly fires ONLY with evidence (evidence required, none fired without); Telegram + API shapes; backfill idempotent (twice → no duplicates); versions persisted; analysis bounded (56 asserts).
- PITFALLS IT ENCODES: anomaly detectors must produce evidence payloads (never bare flags); truth states are 3-valued (NO_DATA/CLEAR/flag) and must be reported honestly.
- NOTES: 20 requirement-mapped classes over a 926-line file; fake account/position/adapter harness.
