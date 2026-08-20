# Web/api_client.js

- **PURPOSE:** The central API client & error contract (the "app.js part
  1" head file): `window.NX.api` — every HTTP call attaches a correlation
  request_id (X-Request-ID), parses the SAFE server error envelope
  {error:{code,message,request_id}}, logs a [UI_ERROR] line, presents a
  user-friendly message preserving request_id, dedupes in-flight requests
  per key, and NEVER fabricates data.
- **ARCHITECTURE LAYER:** Web UI (client plumbing).
- **RESPONSIBILITY:** (a) request id generation (`req_<ts><seq>`);
  (b) safe error envelope parsing + UI_ERROR diagnostics; (c) in-flight
  dedup (no duplicate polling storms); (d) no-fabrication discipline
  (no fake PnL/random fallbacks).
- **DEPENDENCIES:** none beyond the browser.
- **CONNECTS TO:** app.js (all calls), server error middleware.
- **KEY CONCEPTS:** the correlation discipline end-to-end: server X-
  Request-ID ↔ client request_id ↔ [UI_ERROR] lines ↔ log alignment.
- **EDGE CASES & PITFALLS:** network errors (no HTTP status) must still
  produce a structured UI_ERROR; the envelope parser must tolerate
  non-envelope bodies (legacy endpoints) without crashing the UI.