# src/nexus_scalp/market_data/__init__.py

- **PURPOSE:** Package exports for market data (bar aggregation, tick
  storage).
- **ARCHITECTURE LAYER:** Market data.
- **RESPONSIBILITY:** Stable import surface (BarData, BarAggregator
  symbols).
- **DEPENDENCIES:** sibling modules.
- **CONNECTS TO:** features (BarData is the bar type everywhere), ports
  (IMT5Port.get_historical_bars returns list[BarData]).
- **KEY CONCEPTS:** BarData is imported by the ports layer — the market_data
  package must stay dependency-light to avoid cycles.
- **EDGE CASES & PITFALLS:** None beyond keeping the export list additive.