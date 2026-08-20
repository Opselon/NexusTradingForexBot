# tests/unit/test_model_governance_phase16.py

- GUARDS: Model Governance behavioral suite — TEST-LG-01..30 (TASK-6 / CHG-0003): live model-governance boundary — load gate, truthful registry, same-input alignment (50D/70D), shadow isolation, golden parity, evidence, promotion lifecycle, plus TEST-GOV-01..30 for the 70D governance path.
- KEY ASSERTIONS:
  - `test_lg01/02/03`: champion loads when gate passes, hash verified, invalid blocked with the exact gate; `test_lg08/09/15`: same timestamp/news context → deterministic prediction; shadow cannot execute orders and its exceptions/timeouts can NEVER stop the champion; bounded queue; `test_lg21..24`: promotion gate blocks invalid candidates, requires explicit approval, rollback restores previous champion; `test_gov28_no_auto_promotion`; `test_gov29_concurrent_promotion_lock`; `test_gov30_restart_safe_governance_state` (128 asserts).
- PITFALLS IT ENCODES: shadow isolation is absolute (champion continuity must survive any shadow failure); promotion is human-approved and atomic; rollback must preserve evidence.
- NOTES: 2×30 requirement classes over 1673 lines; pairs with integration test_model_lifecycle_api.py.
