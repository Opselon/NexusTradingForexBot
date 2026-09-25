# Liquidity (bounded context)

Liquidity Intelligence governor (/api/liquidity/*) + MSLIE market-structure
perception (/api/mslie/*): state cards, ten-value feature table, pools,
sweep/liquidity-map visualization, read-only model feature vector.

- Invariants honoured in presentation: calculation_status and source_status
  stay orthogonal (BUG-110) — never collapsed into one health boolean;
  values the backend did not record render "—", never 0.
- Toggle is the only mutation: confirm-guarded, backend-authoritative report
  re-read after the POST (SettingsService HOT_RESTRICTED, engine untouched).
- Evidence: news_liquidity_mslie_routes.py, features/liquidity_runtime.py,
  mslie/engine.py; behavior reference Web/app.js tab-liquidity.
