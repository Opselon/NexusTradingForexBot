# Alt Console — Auth/Token Transport Review (SEC-1/3)

Scope: the Alternative React console under `frontend/` (TASK-ALT-UI /
ALT-UI-PRO) and the WEB-AUTH-P0 surfaces it talks to
(`src/nexus_scalp/web/auth.py`, `src/nexus_scalp/observability/logging.py`,
`src/nexus_scalp/cli/engine_boot.py`, `src/nexus_scalp/web/server.py`).
Written record of the security-token-hygiene lane; the ENFORCED version of
every claim is `tests/unit/test_alt_ui_token_hygiene.py` (24 checks, fully
offline, repo venv). Lane constraint honored: read-only outside
`tests/unit/test_alt_ui_token_hygiene.py` and this doc — nothing else was edited.

- Date: 2026-09-13 (worktree `nse-ui-pro-ux`, branch `feat/alt-ui-pro-ux`,
  HEAD `414fa559` at review time; siblings are editing `frontend/src`
  concurrently — line cites are as-of-HEAD, the gate matches STRUCTURE and
  reports live `file:line` at failure time).
- Auditor: Hermes SEC-1 lane (token/auth transport + regression gate).

Policy ids (P1–P7) are used by both the doc and the gate's test names.

---

## 1. Transport table

| Channel | Credential transport | Where the token lives client-side | Server enforcement point | Notes |
|---|---|---|---|---|
| REST (all `fetch` via `src/api/client.ts`) | `Authorization: Bearer <t>` + `X-NSE-Token: <t>` headers (`authHeaders()`, client.ts:91–95) | `sessionStorage['nse.altui.token']` only (P1) | `WebAuthMiddleware` / `_TokenAuthMiddleware` (auth.py) — header order Bearer → X-NSE-Token → cookie → `?token=` | Headers win over cookie; constant-time `hmac.compare_digest` |
| SSE stream (`GET /api/ticks/stream`, `EventSource`) | `?token=<urlencoded>` query on the **same-origin relative** path (`sseUrl()`, realtimeSocket.ts:84–94) | same sessionStorage key, read at connect time | query-param branch of the same middleware | EventSource cannot set headers — query is the ONLY option short of fetch-streaming (see §3) |
| Cookie bootstrap (legacy `Web/` dashboard only) | `nse_web_auth` cookie, `HttpOnly`, `SameSite=strict`, `Path=/`, `max_age=12h`, set on public `/app.js` + `/api_client.js` (auth.py:336–360) | browser cookie jar, per-origin; **JS never reads/writes it** | accepted AFTER headers (auth.py:402–407); opt-out `NSE_WEB_AUTH_COOKIE_DISABLE` | The alt console does NOT rely on it and never touches `document.cookie` (gated) |
| Boot injection seam | `window.__NSE_WEB_TOKEN__` (client.ts:53–56) — in-memory, first priority | memory only; never written to any storage by this path | n/a | Ship-shape for a serving template to inject without URL exposure |
| Dev habitat (`vite` :5173) | proxy forwards `/api/*` to `NSE_API_ORIGIN` (default 127.0.0.1:8080), same-origin to the browser | sessionStorage of the :5173 origin | backend behind the proxy | Vite proxy target is env-driven, never baked into `dist` |

## 2. Token lifecycle and the URL-scrub proof (P1/P2)

`?token=` deep link → `resolveToken()` (frontend/src/api/client.ts):

1. capture: `const qp = new URLSearchParams(window.location.search).get("token")` (client.ts:58);
2. persist: `sessionStorage.setItem(TOKEN_STORAGE_KEY, qp)` (client.ts:61) —
   sessionStorage is per-tab AND per-origin: closing the tab clears the
   credential, and it never crosses to another port/host;
3. scrub: `url.searchParams.delete("token")` (client.ts:64) then
   `window.history.replaceState(null, "", url.toString())` (client.ts:65) —
   replaceState replaces the CURRENT history entry, so the address bar, a
   copy/paste, or a bookmark after load can never carry the token;
4. return value used for headers/SSE; refresh re-reads sessionStorage (client.ts:72–76).

Gate proof (structural, order-checked):
- `test_capture_then_scrub_flow_present` fails if any of the four steps is
  removed AND if the store→delete→replaceState ORDER breaks (store must precede
  scrub, else a failed store would drop the credential entirely);
- `test_no_token_assignment_to_location_before_scrub` bans re-materializing the
  token via `location.href/assign/replace` or `pushState`;
- `test_no_window_name_or_cookie_sinks` bans `window.name` (survives
  cross-navigation) and any `document.cookie =` write;
- `test_token_never_touches_localstorage` — localStorage is for VISUAL prefs
  only. Allowlist resolved THROUGH same-file consts (`LANG_KEY` etc.):
  `nexus.ui.lang` (shared with the legacy dashboard, verified identical in
  `Web/ux_i18n.js:21` — the legacy comparison this lane was asked to check:
  same key, same dict-membership rule, `saved in DICTS` mirrors
  ux_i18n's lang validation), `nse.altui.sidebar`, `nse.altui.dense`
  (uiStore readPref/writePref, callsite keys re-checked), and the
  SettingsPage clear-prefs action (guard re-verified: `PREF_PREFIX =
  "nse.altui."` + `startsWith(PREF_PREFIX) || k === LANG_KEY`).
- `test_sessionstorage_keys_are_allowlisted` — the ONLY sessionStorage key the
  UI may touch is `nse.altui.token` (client.ts and realtimeSocket.ts agree;
  `test_token_storage_key_constant_is_pinned` pins the literal).

## 3. SSE `?token=` exposure assessment (P4/P7)

EventSource offers no header hook, so the token rides the query string —
accepted at the server's query-param branch. Exposure surfaces, verified:

| Surface | Verdict | Evidence |
|---|---|---|
| Browser history / address bar | clean | same-origin `EventSource(url)` does not alter history; the entry point was already scrubbed (§2) |
| Referer leakage to third parties | none in practice | the app issues ONLY root-relative same-origin requests; no `<a target>`, no remote subresources, no CDN (SEC-3 gate pins this); same-origin requests DO carry the query in `Referer`, so hardening is still proposed (§3.1) |
| Server access log (uvicorn) | quiet in production | `engine_boot.py:598` builds `uvicorn.Config(log_level="warning")`; `configure_logging` applies `--log-level` to `uvicorn.access`, so at WARNING the per-request access line (which WOULD contain the full request target incl. `?token=`) is never emitted. PROVEN by gate `test_uvicorn_production_config_keeps_access_logs_quiet` — flipping to `info`/`debug` (or `uvicorn --reload`/CLI habit during ops) fails the check. NOTE: this is the load-bearing fact; the structlog redactor does NOT sit in front of uvicorn.access (it writes stdout via its own handler). |
| Structured (structlog) logs | scrubbed | `_SECRET_ASSIGN_RE` masks `token=<val>` assignments inside every string value and `_redact_sensitive_fields` masks any KEY containing `token`/`auth`/`bearer`…; behaviorally proven by `test_value_scrubber_redacts_token_query` + `test_key_based_redaction_masks_token_fields`. Web-route error logging passes `request.url.path` only (server/api_v1 error handlers) — never the query string, verified by reading the call sites. |
| Generated-token log line | safe by design | auth.py never logs the generated value; it relies on the same redaction pipeline (auth.py:155–172 comment) and retrieves via the secret store |
| Browser devtools / net panel | visible | same-origin operator habitat; acceptable and identical for every cookie-authed app |
| 401 bodies | no echo | static `"missing or invalid web auth token"` + `WWW-Authenticate: Bearer` (auth.py:243–256, 377–390) — gate executes the real ASGI middleware offline and asserts neither the expected nor the supplied token appears in the body (`test_webauth_middleware_401_body_carries_no_token`) |

Client-side hygiene around the stream: `test_token_param_confined_to_sse_builder`
fails if `?token=` is ever constructed outside `sseUrl()` or if the
EventSource is built from anything else; `test_token_never_stored_in_realtime_client_state`
bans a token field on the client class; the builder must keep the value
URL-encoded (`encodeURIComponent`) on the relative `/api/ticks/stream` path.

**§3.1 PROPOSAL (not applied — index.html is outside this lane):** add
`<meta name="referrer" content="no-referrer">` to `frontend/index.html`. The
page today leaks nothing (no cross-origin requests exist), but the meta makes
that a browser-enforced invariant instead of a code-review invariant, and it
protects any future deep link opened while `?token=` is still in the URL
(pre-scrub window). Alternative/extra: serve `/alt/index.html` with a
`Referrer-Policy: no-referrer` response header (server.py currently sets none).
A stronger transport fix — replacing `EventSource` with a `fetch`-based reader
that CAN send headers — is the only way to remove query exposure entirely;
it is a realtime-lane decision (reconnect/backoff/watchdog parity), recorded
here as the durable mitigation, not done unilaterally.

## 4. Standalone (`:8088`) cookie-bleed analysis

Scenario: the built bundle served OUTSIDE the engine origin — e.g. `vite
preview`/any static server on `http://127.0.0.1:8088` proxying `/api` to the
engine at `:8080`.

- The legacy `nse_web_auth` cookie is **HttpOnly, SameSite=strict, host-scoped
  and port-split at origin level**: a cookie minted by `:8080` never rides to
  `:8088`. Same for the alt console's `sessionStorage` — different origin
  (`scheme://host:port`), so the 8080 tab's token is invisible on 8088.
  **No cross-origin bleed exists in either direction.**
- Consequence: the standalone copy is UNAUTHENTICATED until the operator
  bootstraps it. That is fail-closed behavior, not a defect.
- The standalone proxy therefore MUST forward credentials explicitly:
  `Authorization` and `X-NSE-Token` request headers pass through (the browser
  sends them to the proxy origin because the UI's `fetch` is same-origin
  relative). The proxy must NOT depend on cookies and must NOT log query
  strings (the SSE `?token=` leg passes through the proxy too — an
  access-logging static server would record the token in ITS logs; keep the
  proxy quiet or strip-and-forward).
- Operator flow (documented, since the repo wires no 8088):
  1. `cd frontend && npm run build`; serve `dist/` on 8088 with `/api` (and
     `/api/ticks/stream` unbuffered) proxied to 8080, headers forwarded,
     access log for `/api` off or redacted;
  2. open `http://127.0.0.1:8088/alt/?token=<NSE_WEB_AUTH_TOKEN>` once —
     capture → 8088-origin sessionStorage → URL scrub;
  3. refresh works (sessionStorage); closing the tab drops the credential;
  4. the 8080 legacy dashboard and the 8088 console hold INDEPENDENT token
     copies; revoking means touching both (or rotating the server token).
- Default production habitat avoids all of this: `web/server.py` mounts the
  bundle at `/alt` on the SAME origin (8080) and Node stays build/dev-only
  (DEC-0002) — one origin, one sessionStorage copy, no proxy hop.

## 5. Threat list + mitigations (current state)

| # | Threat | Status / mitigation |
|---|---|---|
| T1 | Token survives in address bar / shared history | MITIGATED: capture→store→scrub order pinned by gate (§2) |
| T2 | Token persists past tab close | MITIGATED: sessionStorage-only allowlist; localStorage banned for token material; no `document.cookie`/`window.name` sinks |
| T3 | Token printed to console / toast / banner / UI text | MITIGATED: zero `console.*` in src; toasts carry backend messages + request_ids only (useMutationFeedback, queryBridge — auth errors route to the STATIC banner, not toasts); `?token=<NSE_WEB_AUTH_TOKEN>` strings in AppShell/RiskPage banners are literal operator instructions, never interpolated values (gate bans `token=`+`${` in any UI string) |
| T4 | Token embedded in error surfaces | MITIGATED: `normalizeErrorEnvelope` composes messages only from backend envelope fields, static strings and correlation ids; gate scans every `new ApiError(` site for token/auth-header interpolation (`test_apierror_messages_never_embed_credentials`) |
| T5 | Query token in server logs | MITIGATED two ways: uvicorn access logging quiet at warning (structural pin) + structlog key/value redaction (behavioral proof). Residual: operator-run `uvicorn --log-level info` bypasses the app config — documented ops hazard |
| T6 | Referrer leakage of `?token=` | Effectively none today (same-origin-only traffic); `no-referrer` meta/header PROPOSED (§3.1), not applied (out of lane) |
| T7 | Cross-site request forgery on token auth | LOW: SameSite=strict cookie + header/query transports; no state-changing route trusts the cookie alone for mutations |
| T8 | XSS steals token (sessionStorage is JS-readable) | Shared fate with any SPA token scheme: no `dangerouslySetInnerHTML`/`innerHTML` in the console (SEC-3/XSS gates in sibling lanes), CSP proposal belongs to the serving lane; SSE `?token=` additionally readable by any same-document script — accepted as equivalent to header theft under XSS |
| T9 | Token committed to repo/dist | MITIGATED: shape-only secret scan in this gate (`test_no_token_literal_hardcoded_in_frontend`) + SEC-3 dist scans; findings print lengths, never values |
| T10 | Replay of a captured `?token=` on the LAN | Accepted residual risk (localhost-bound default bind, plaintext http by design for a local control plane); token rotation lives in the secret store; LIVE mode keeps auth mandatory (require_always path) |

## 6. What stays fail-closed

- No token → 401 on everything outside the minimal static allowlist
  (`is_public_path`, traversal-refused), static body, no echo; `?token=`,
  Bearer and `X-NSE-Token` all constant-time compared.
- Token unresolvable at boot → 500 `AUTH_CONFIG_ERROR` on every protected
  route (never an empty-token allow); generation persists to the DPAPI
  secret store, value never logged.
- `/alt` static mount is NOT public: verified live via TestClient at
  `NSE_WEB_AUTH_TOKEN` set — `/alt/`, `/alt/trading`, and
  `/alt/assets/index-*.js` all 401 without a credential; only the
  `dist` file set itself is served once authorized (deep-link SPA fallback
  included). The console therefore REQUIRES the `?token=` first-load or a
  pre-injected `window.__NSE_WEB_TOKEN__`.
- UI never fabricates an authed state: no token → `authConfigured()` false,
  401 → static auth banner (`ApiError.isAuthError` = 401/403/
  AUTH_CONFIG_ERROR), `clearAuthToken()` keeps its purge seam.
- `NSE_WEB_AUTH_DISABLE=1` remains the documented PAPER/local-only escape
  hatch (server.py:2902–2911 logs a warning) — never combine with LIVE or a
  routable host.

## 7. Findings and proposals

1. **KNOWN GAP (logged, not shipped-fixed): `authorization` defeats its own
   redaction.** In `_redact_sensitive_fields` the exemption scan runs FIRST
   and `"author" in "authorization"` hits `_NON_SECRET_KEY_FRAGMENTS`, so the
   key `continue`s — value-based scrubbing is SKIPPED for it:
   `{'authorization': 'Bearer <t>'}` passes through VERBATIM (proven by the
   gate's `xfail(strict=False)`
   `test_authorization_field_redaction_known_gap`). No web code logs request
   headers today, so it is latent, not live. Patch suggestion
   (`src/nexus_scalp/observability/logging.py`):
   ```python
   _EXACT_CREDENTIAL_KEYS = frozenset({"authorization", "auth", "bearer"})
   ...
   for key in list(event_dict.keys()):
       lower = str(key).lower()
       if lower not in _EXACT_CREDENTIAL_KEYS and any(f in lower for f in _NON_SECRET_KEY_FRAGMENTS):
           continue
       ...
   ```
   Then flip the xfail to strict. (Out of this lane's write scope — file is
   READ-ONLY here; SEC-2/ops lane should own it.)
2. **PROPOSAL**: `<meta name="referrer" content="no-referrer">` in
   `frontend/index.html` and/or a `Referrer-Policy: no-referrer` header on
   the `/alt` mount (§3.1). Not applied (read-only).
3. **PROPOSAL**: fetch-based SSE reader to move the stream token into
   headers; keep `EventSource` as fallback only if the server ever grows a
   cookie path for `/api/ticks/stream` (it does not today — the bootstrap
   cookie is set for the legacy page, and SameSite=strict same-origin SSE
   WOULD ride it; the alt console intentionally does not depend on that).
4. **OPS note**: any custom static/preview server in front of `dist/` must
   not log query strings (§4); production `/alt` mount makes this moot.
5. **Mid-flight scan note**: `frontend/src` moved under this audit (siblings
   active). The gate deliberately anchors on STRUCTURE (function blocks,
   call-site regexes, same-file const resolution) and reports `file:line`
   live at failure time — no brittle absolute line numbers were baked.

## 8. Gate inventory

`tests/unit/test_alt_ui_token_hygiene.py` — 24 checks (23 pass + 1 documented
xfail), offline (no network, no browser, no running backend; one in-process
ASGI call against the real middleware class). Policies P1–P7 map to
`TestTokenStoragePolicy`, `TestUrlScrubContract`, `TestNoTokenInTextSinks`,
`TestSseTransport`, `TestErrorSurfaceHygiene`, `TestServerAuthTransportFacts`,
`TestStructlogRedaction`. Failure messages always name
`file:line`-of-offense and never reproduce token material.
The gate was mutation-tested: deleting the scrub, moving the token to
localStorage, adding `console.log(token)`/toast(token)/UI(`token=${token}`),
building the SSE URL without `encodeURIComponent`, inventing a new
sessionStorage key, embedding `authHeaders().Authorization` in an ApiError,
interpolating the token into the server 401 body, and flipping uvicorn
`log_level` to `info` each make the suite RED (9/9 mutations detected;
clean tree green).

### Unenforceable by this source-level pytest (recorded honestly)

- Real browser runtime behavior (actual address-bar after load, DevTools,
  Referer on the wire) — source structure is necessary, not sufficient.
- Anything a human types at runtime: `uvicorn --log-level info` on the CLI,
  a chatty third-party proxy on 8088, pasting the deep link into chat
  BEFORE first load (the scrub only protects post-capture state).
- XSS actually stealing sessionStorage (T8) — that is a CSP/serving-lane
  invariant.
- Live end-to-end 401→banner flow (covered by the live-backend contract
  suite when `ALT_UI_LIVE_BACKEND=1`, not here).
