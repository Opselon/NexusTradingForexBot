# tests/unit/test_anomaly_verify01_duplicates.py + test_anomaly_verify01_mfe.py

# test_anomaly_verify01_duplicates.py
- **GUARDS:** Verify-01 anomaly forensics — duplicate detection in the
  ledgers (idempotency_key / dedup keys hold under real captured data).
- **KEY ASSERTIONS:** canonical identity dedup (idempotency_key,
  article_hash+verified duplicate_of, trade_id, article_id+run_id);
  split-fill families are PROTECTED (never duplicates); no duplicate
  financial records on replay of the captured real data.

# test_anomaly_verify01_mfe.py
- **GUARDS:** Verify-01 MFE/giveback forensics — MFE capture sanity on
  the captured cohort.
- **KEY ASSERTIONS:** MFE/MAE tracking (order_manager _mfe_tracker) is
  monotonic; MFE<=0 → None (no synthetic 0.0); retention analytics
  (mfe_capture_ratio/giveback) math matches the reference computation on
  fixture data.