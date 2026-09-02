# src/nexus_scalp/shadow/shadow70/liq_provider.py + news_provider.py

# liq_provider.py
- **PURPOSE:** The 70D Liquidity producer bridge (TASK-05-70D-SHADOW):
  CONTRACT-FIRST (INV-70D-003) — indices 60..69 are the Liquidity family.
  Resolves the canonical liquidity producer WITHOUT importing the whole
  engine: it binds to features/liquidity_engine at runtime but keeps the
  import surface minimal (avoids heavy/circular imports in the shadow
  worker's cold path).
- **RESPONSIBILITY:** (a) resolve the producer module/function;
  (b) compute the 10D liquidity vector for a given (bars, decision_at);
  (c) provide the canonical family names in order.
- **KEY CONCEPTS:** the ordering contract is schema_contract's
  LIQUIDITY_10D_NAMES — never reordered here; provider resolution failing
  → explicit UNAVAILABLE (shadow70 records the family as unavailable,
  never fabricated).
- **EDGE CASES:** liquidity producer version drift → the 70D validation
  (schema hash) catches it at attach/validate time.

# news_provider.py
- **PURPOSE:** Canonical News-family 10D mapping for the 70D vector
  (TASK-10): indices 50..59 = NEWS family (INV-70D-002). Maps the
  canonical live news context (news_context_v1 12 fields) to the
  10-field 70D block (fields 0..8 + news_state idx 10 — NOT a blind
  first-10 slice), mirroring schema_contract's selection exactly.
- **RESPONSIBILITY:** context → 10D mapping with the news-honesty rule
  (no postdating events; NO_OVERLAP → zero vector, never fabricated).
- **KEY CONCEPTS:** the selection must stay byte-identical to
  schema_contract.NEWS_10D_NAMES (asserted in tests); provider failure →
  family UNAVAILABLE (shadow records honestly).
- **EDGE CASES:** a news context with fewer than 10 usable fields → the
  missing fields take neutral per the contract (not zero, not padded).