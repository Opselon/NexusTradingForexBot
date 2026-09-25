# tests/unit/test_forensic_incident_center_task.py

- GUARDS: Forensic Incident Center repair regression tests (2026-08-20; spec 33/66, reduced critical-test philosophy): one strong test per failure class: broker epoch normalization, clock-skew semantics, split-fill sentinel, occurrence impact, outcome/broker-evidence recovery, one-click trace, evidence lifecycle, export masking, timebase chains.
- KEY ASSERTIONS:
  - `test_server_local_epoch_subtracts_offset` / `test_garbage_epoch_returns_none`; `test_sentinel_zero_is_not_a_family`; `test_zero_outcome_with_broker_pnl_is_recoverable`; `test_verified_refused_without_fix_evidence`; `test_incident_json_masks_secrets`; `test_chain_reports_pre_fix_marker` (46 asserts).
- PITFALLS IT ENCODES: verification REQUIRES fix evidence (no status flip without it); zero-outcome + broker PnL is recoverable truth, not a loss; secret masking is mandatory in every export path.
- NOTES: One test per failure class by design (no test explosion) — the anti-regression philosophy for the incident center.
