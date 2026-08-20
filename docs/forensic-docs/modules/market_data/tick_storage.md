# src/nexus_scalp/market_data/tick_storage.py

- **PURPOSE:** Optional tick persistence layer — stores raw ticks (bounded)
  for replay/backtest/forensics, feeding the REPLAY execution mode and
  post-hoc reconstruction.
- **ARCHITECTURE LAYER:** Market data (storage).
- **RESPONSIBILITY:** Bounded tick capture (in-memory ring or file-backed
  window), query by symbol/time range, replay iterator.
- **DEPENDENCIES:** domain TickData, storage abstractions.
- **CONNECTS TO:** LiveEngine (capture), replay mode, forensics tools,
  tests.
- **KEY CONCEPTS:** Persistence is write-queued (never on the tick path
  synchronously); the stored stream is the source of truth for REPLAY
  determinism (replay must consume the SAME ticks the live path saw).
- **EDGE CASES & PITFALLS:** Bounds must be enforced (unbounded tick
  storage on a 24/7 stream = disk/memory leak); replay determinism breaks
  if capture and replay timestamps diverge (UTC normalization mandatory).