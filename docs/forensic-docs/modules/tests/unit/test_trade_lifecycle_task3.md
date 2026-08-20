# tests/unit/test_trade_lifecycle_task3.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- TASK-3 regression guards — canonical trade lifecycle / exit intelligence / learning-lineage integrity (TEST-TL-01..24).
- DEAL_REASON mapping (BUG-083): reason=4 → SL (never TP), status in ("CLOSED", "CLOSED_SL") — `assert row.get("status") in ("CLOSED", "CLOSED_SL")  # reason=4 -> CLOSED_SL (BUG-083)`.
- Lifecycle: one decision → one canonical trade; one parent order + N fills → ONE economic trade (split siblings inherit FULL context: entry order id, confidence 0.62/0.71, regime, reason PURE_AI, expected price, setup snapshot); strategy context + model metadata survive close; initial risk IMMUTABLE; R uses initial risk.
- Exit intelligence: BE exit counts; exit reasons recorded truthfully; learning lineage: RecordingEngine receives real trade outcomes.
- 52 defs / 1029 lines; MockMT5Port fixture with counters; AuditRepository for ledger asserts (queued-writer flush).