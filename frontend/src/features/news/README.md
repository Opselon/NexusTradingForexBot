# features/news — News Intelligence bounded context

Replaces legacy `tab-news` (Web/app.js news block + Web/news_intelligence.js).

- **api.ts** — typed calls over the stable `@/api/client` exports (Lane 1 owns
  `src/api/newsApi.ts`; this feature keeps its own surface to avoid import races).
- **types.ts** — DTOs mirroring `news_liquidity_mslie_routes.py` /
  `news_intelligence_routes.py` (raw JSON, `available:false` when off).
- **model.ts** — guards + view models: `available:false` is an UNAVAILABLE state,
  never an empty list; unanalyzed => `PENDING` (never defaulted to NEUTRAL).
- **useCases.ts / hooks.ts** — reads through guards, commands invalidate
  `["news", …]`; the backend response is the only verdict source.
- **ui/** — state panel (engine + auto-analysis toggles, fetch, confirm-guarded
  self-heal), feed + article drawer, impact timeline, sources/health, keywords,
  trade linkage.

Invariants: news is bounded (informs, never forces a trade); refresh has a
60s server bandwidth guard; self-heal only rebuilds DERIVED tables.
