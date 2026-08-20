# tests/unit/test_performance_report_intelligence.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Performance Report Intelligence suite (task §22, 17 required cases): basic daily report, zero trades, only wins, only losses, mixed, breakeven, MAE/MFE missing, regime attribution.
- Report math: trades/wins/losses/expectancy/net_pnl/win_rate/profit_factor asserted against seeded ledgers; report_id deterministic for same period; account.balance from live snapshot.
- Missing-data discipline: zero trades → `net_pnl is None`, `win_rate is None`, `profit_factor is None`; only wins → `profit_factor is None` (# no losses → undefined); `median_trade is None` (not computed in this stage); no MFE → `mfe_capture_ratio is None`. Explicit None-over-fabrication contract throughout.
- Deep report string contains the period blob (`"\n\n".join(chunks) == deep` marker captured by champion logging — capsys pattern, not caplog; BUG-118).
- Fixtures: `_FakeAccount`/`_FakePosition`/`_FakeAdapter` + `audit`/`core` with `_flush` (queued-writer join).
- 67 defs / 917 lines.