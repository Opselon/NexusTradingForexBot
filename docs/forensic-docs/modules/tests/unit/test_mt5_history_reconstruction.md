# tests/unit/test_mt5_history_reconstruction.py

- GUARDS: History reconstruction (RED phase): canonical trade-lifecycle reconstruction contract from REAL MT5 fixtures — deal+order streams → normalized broker orders/deals → logical trades (position_id lifecycle).
- KEY ASSERTIONS:
  - `TestReconstructTradesFromRealFixture`: logical trade count; net PnL equals real broker sum; wins/losses/breakeven counts; best/worst trade; partial close aggregates to ONE trade; order/deal never confused with position; trade identity is position_id, NOT uuid; `TestNormalizeAndDeduplicate`: deal/order identity key is ticket (16 asserts).
- PITFALLS IT ENCODES: identity rules are explicit — a trade IS a position_id lifecycle; dedup keys are tickets.
- NOTES: Proves the reconstruction target modules' contract before they were built (RED phase).
