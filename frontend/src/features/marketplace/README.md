# features/marketplace — Strategy Marketplace bounded context

Replaces legacy `tab-marketplace` (Web/marketplace.js). Talks ONLY to the v1
platform `/api/v1/marketplace/*` (envelope unwrapped in api.ts via `getV1`).

- **types.ts** — DTOs per the frozen CHG-0056 contract (api_v1/marketplace.py).
- **model.ts** — install-count validation mirrors the server rule (1..500);
  `NOT_AVAILABLE` for unscored rows; enablement PENDING/DENIED are distinct.
- **useCases.ts / hooks.ts** — reads unwrapped; commands invalidate
  `["marketplace", …]` because installs/repairs mutate several tables at once.
- **ui/** — packs, seeds (+drawer with 14-factor history), rankings per
  dimension, repairs, runtime snapshot.

Invariants: gates are server-side; the console never grants enablement
silently and never writes the runtime snapshot directly.
