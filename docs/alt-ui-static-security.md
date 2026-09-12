# ALT Console — Static-Serving & Auth Surface Security Review (SEC-2/SEC-3)

Lane: `security-static-surface`. Date: 2026-09-13. Author: SEC-2 subagent.
Regression suite: `tests/unit/test_alt_ui_static_auth.py` (run output:
`pytest_static.txt` at worktree root). Worktree: `nse-ui-pro-ux` @ `1065279a`.
Everything below describes the **current on-disk state** of
`src/nexus_scalp/web/server.py` (`_AltSpaStaticFiles` mount at `/alt`,
`auth.install_web_auth`) — server.py and `scripts/serve_alt_ui.py` were NOT
edited (siblings own them); fixes are *requested*, not applied.

## 0. Method

- Repo venv python + `fastapi.testclient.TestClient` over
  `create_app(engine_ref=None)`, mirroring `test_frontend_assets_phase14.py`.
- `NSE_WEB_AUTH_DISABLE` left UNSET (actively popped) — the real WEB-AUTH-P0
  middleware is installed; token via `NSE_WEB_AUTH_TOKEN`;
  `NSE_WEB_AUTH_COOKIE_DISABLE=1` pins header-only transport so a stray
  bootstrap cookie can't mask a header regression.
- Dist is a controlled tmp tree mounted via `NEXUS_ALT_UI_DIR` (the resolver's
  first branch) so sibling rebuilds of `frontend/dist` cannot change hashes
  under the suite; two tests additionally mirror the REAL `frontend/dist`
  (skipping with the mount's own disable-by-default reason when absent).
- Import isolation: the venv's `__editable__` .pth pins `nexus_scalp` to the
  MAIN checkout (`NexusTradingForexBot/src`). The suite force-resolves THIS
  worktree's `src/` (same BUG-231 class documented in `tests/conftest.py`).

## 1. Findings table

| ID | Sev | Status | Behavior / Finding |
|----|-----|--------|--------------------|
| SEC-2/A1 | info | pinned | The whole `/alt` mount sits **behind** the token: `/alt`, `/alt/`, `/alt/index.html`, `/alt/assets/*` are 401 tokenless, 200 with Bearer. Stricter than the legacy static allowlist (`/app.js` etc. are public); `/alt` is in no allowlist (`is_public_path("/alt/") is False`). Pin prevents an accidental future "make /alt public" edit. |
| SEC-2/A2 | low | pinned + **fix requested** | `is_public_path()` refuses traversal by a **text** check for `..`/`\` on the path string, but percent-encoded traversal (`/assets/%2e%2e/secrets.enc`) passes the check and returns `True` (would be public if a route existed under `/assets/`, `/static/`, `/vendor/`). Safe today only because uvicorn hands the middleware the **decoded** ASGI `scope["path"]`. Any fronting proxy (the 8088 plan) that forwards raw percent-encoding without decoding turns this into a public-path bypass. Fix (integrators, auth.py): test `urllib.parse.unquote(path)` for `..`/`\` too. |
| SEC-2/T1 | ok | pinned | Traversal battery (15 shapes: raw, `%2e%2e`, double-encoded, `%5c` backslash, mixed `..%2f`, mid-path `./assets/../../`, `/4/../../windows/win.ini`, and the task's `/alt/../secrets.enc`) **never serves bytes outside dist** — results are 401 (tokenless), 404, or the *in-root* SPA shell (never the 315 KB legacy `Web/index.html`, never `secrets.enc`/`win.ini` content). `StaticFiles.lookup_path` containment + `_sanitise_component` hold. The canary file provably exists (guard-the-guard test). |
| SEC-2/D1 | ok | pinned | No directory listing: `html=True` + `follow_symlink=False`; a real dir with no index (`/alt/assets/bare/`) returns the shell (200, no-store) or plain 404 — never an enumeration of sibling files. |
| SEC-2/C1 | **med** | xfail-pinned (**finding**) | The app shell served as a **real file** (`/alt/index.html`, `/alt/`) carries **no `Cache-Control`** — a browser/proxy may pin a stale shell that references an OLD hashed bundle after a redeploy (heuristic caching; `/alt/audit/123` fallback DOES set `no-store`, so policy is inconsistent between the two paths to the same bytes). Fix (sibling, server.py): set `Cache-Control: no-store` on the real-file `index.html` response too (override `get_response`/`file_response` when name is index.html), or a mount-scoped header rule. Do not fix in tests — xfail flips when it lands. |
| SEC-2/C2 | low | **finding** | Hashed assets (`/alt/assets/index-*.js|css`) also carry no `Cache-Control`; safe (content-addressed names) but re-downloaded on every console load. Optional improvement, not security: `public, max-age=31536000, immutable` on `/alt/assets/*` with hash in name. |
| SEC-2/R1 | ok | pinned | `/alt` does NOT shadow legacy routes: `/` returns **byte-identical** `WEB_DIR/index.html` (315,617 B, legacy dashboard), and unknown `/api/*` keeps its JSON 404 envelope — the SPA fallback is mount-scoped and cannot overmount the API or `/`. |
| SEC-2/R2 | ok | pinned (conditional) | SPA deep-link fallback is **present on disk** (`_AltSpaStaticFiles.get_response`): non-asset deep links → 200 shell + `no-store`; missing `*.js` → honest 404 (no fake index masking typos). The test is written conditionally so it stays valid if the sibling reworks the fallback (then 404 accepted, shell-bytes-under-a-deep-route never). |
| SEC-2/E1 | ok | pinned | SSE `/api/ticks/stream`: 401 + `application/json` tokenless (verified at raw ASGI scope level), 200 + `text/event-stream` with the same Bearer token as REST — one policy across REST/static/stream. |
| SEC-2/W1 | **high** | **finding (review-only)** | WebSocket `/ws` and `/web` are **not** covered by WEB-AUTH-P0: `install_web_auth` uses `BaseHTTPMiddleware`, which only sees `http` scope; websocket scope passes to the endpoint, which `accept()`s unconditionally and immediately pushes the full canonical snapshot (positions, account, model state). A LAN peer may open a tokenless WS read-stream. Out of this lane's write scope — hand to the auth lane: require the token in `?token=`/first-frame before `accept()`. |
| SEC-2/P1 | info | note | CORS is `allow_origins=["*"], allow_credentials=False` — consistent for the cross-origin plan only for header-token calls; but middleware order (`add_middleware` insert(0) ⇒ auth is OUTERMOST, added last) means an auth-less **OPTIONS preflight is 401'd before CORS can answer it**, so browser cross-origin calls to :8080 fail. The standalone 8088 origin must therefore proxy `/api` server-side (same-origin to the browser), which is also what closes the preflight hole. |
| SEC-2/I1 | info | note | The mounted dist is resolved at **create_app time** from `NEXUS_ALT_UI_DIR` or `<pkg-root|cwd>/frontend/dist` — same as the legacy `/alt` behavior; no `follow_symlink` risk since StaticFiles defaults to off. |

## 2. SSE resource-exhaustion posture (REVIEW-ONLY — current server.py)

Observed at `/api/ticks/stream` (`sse_telemetry_stream`):

1. **No connection cap, no rate limit.** Every authenticated LAN client may
   hold N concurrent streams; each runs its own `while True` loop at ~5 Hz.
2. **Full snapshot per loop, per connection.** Each iteration calls
   `get_system_state()` (the whole canonical projection: chart overlays,
   accounting, health, freshness) — CPU cost multiplies by streams; a
   small number of hostile/authenticated clients can saturate the event loop
   that also serves trading-path API calls. Same unbounded pattern on
   `/ws`+`/web` (`active_connections` set, no cap) — compounded because each
   SSE loop *also* fans out to every WS client per event.
3. **No server-side idle/max-lifetime cap** — termination relies on
   `request.is_disconnected()`, which needs a responsive client (or a dead
   TCP read); half-open keepalive clients linger.
4. **Observability lies under concurrency**: `app.state.sse_diag` is a single
   shared dict — the last connection to connect owns `connection`/
   `connected_at`, and `reconnect_count` mixes clients.
5. **No `X-Accel-Buffering: no`** on the response — behind nginx/Caddy (or the
   8088 proxy) the stream may be buffered until the buffer fills, silently
   freezing the console (the Vite *dev* proxy explicitly sets it + `no-cache`
   — the production surface should not rely on the proxy doing it).
6. **`?token=` is an accepted credential** for exactly this stream → the token
   appears in URLs, proxy access logs, and (if a stream URL is ever pasted
   into a chat) out-of-band. `redact_url` covers logs; fronting proxies must
   redact `token=` too.

Recommendations for the API/auth lane (not done here): per-origin concurrent
stream cap (small, e.g. 4), global stream cap with 503 `ENGINE_UNAVAILABLE`-
style envelope, snapshot coalescing (compute once per tick, broadcast — the
`stream_history` deque(200) already proves the pattern), max stream lifetime
+ retry hint, per-client connect rate limit, `X-Accel-Buffering: no` +
`Cache-Control: no-cache, no-transform` on the SSE response itself.

## 3. Standalone `scripts/serve_alt_ui.py` (port 8088) — design risks for integrators

REVIEW-ONLY: the script is being written concurrently by a sibling; these are
must-haves to hand it (and to `docker/`/docs owners), each derived from the
surfaces pinned above.

**SSRF / upstream control**
- [ ] Upstream (`--backend` / `NSE_API_ORIGIN`-style env) resolved and
      validated **once at startup**: `http`/`https` only, no userinfo, no
      redirect-following on proxied requests.
- [ ] Never accept a request-time user-controlled upstream (no `?target=`, no
      `X-Forward-To`, no per-request base URL override).
- [ ] Refuse non-loopback/cloud-metadata upstream hosts unless an explicit
      operator flag is passed (block 0.0.0.0/8, 127.0.0.0/8 *except* the
      operator's chosen loopback port is fine — the point is the *default*
      must not let a page drive the fetch).

**Bind / open-proxy**
- [ ] Default bind `127.0.0.1` — LAN/`0.0.0.0` only behind an explicit
      `--allow-remote` that logs a WARNING naming the bind, mirroring
      `NSE_WEB_AUTH_DISABLE`'s "NEVER with LIVE / routable host" language.
- [ ] Proxy **path allowlist** (`/api/*`, `/health*`, `/ws`,`/web`), not
      "everything that isn't a static file": an `/alt` static server must not
      become a general fetch-forwarder (open-proxy abuse, internal-port
      probing from a browser).
- [ ] Static side: serve ONLY the dist tree (containment like StaticFiles —
      reject decoded `..`, encoded `..`, backslashes), `index=False`-style
      no-listing, no symlink following.

**Credentials / headers**
- [ ] If the proxy forwards `Authorization`/`X-NSE-Token`, it forwards the
      operator's token to exactly one fixed upstream — and must NOT also
      mirror the token into cookies or HTML on the *foreign* :8088 origin
      (the WEB-UI-BOOTSTRAP cookie design sets `nse_web_auth` for
      same-origin :8080; replicating it on 8088 makes one DPAPI token live on
      two origins — cookie scope bug class). `Set-Cookie` and `Cookie` from
      the upstream/clients should be stripped unless deliberately,
      documentedly proxied.
- [ ] Header allowlist outbound: `accept`, `accept-encoding`,
      `authorization`/`x-nse-token` (if in scope), nothing opaque (`via`,
      `x-forwarded-*` limited to loopback-appended values).
- [ ] Redact `?token=` in the 8088's own access logs (reuse
      `observability` `redact_url`).
- [ ] Do not decode-and-re-encode paths in a way that would hand the backend a
      `%2e%2e` that SEC-2/A2's text-only allowlist check would wave through.

**SSE / streaming**
- [ ] Flush every SSE write (no user-space buffering); do not read
      content-length; set/keep `X-Accel-Buffering: no`; disable response
      compression middleware for `text/event-stream` (gzip breaks streams).
- [ ] Propagate client disconnect to the upstream stream (otherwise each dead
      browser tab leaks one upstream SSE connection — the exact exhaustion
      vector in §2).
- [ ] Cap concurrent proxied streams per peer; timeout idle upstream reads.

**Auth consistency**
- [ ] :8088 must not become the unauthenticated back door around :8080's
      WEB-AUTH-P0: if it proxies `/api`, either require the token from the
      client (pass-through) or bind loopback-only and document that premise.
- [ ] Mirror `SEC-2/T1` battery + `SEC-2/E1` as a smoke check against the
      running 8088 in the integrator's own tests (the suite here targets the
      in-process mount; the same asserts port to a live base URL).

## 4. Regression suite mapping (tests/unit/test_alt_ui_static_auth.py)

| Assertion requested | Test |
|---|---|
| unauth `/api/status` 401 (WEB-AUTH-P0 intact) | `test_api_status_unauthenticated_is_401` |
| `/alt` static reachable per observed allowlist (pinned 401/200 policy) | `test_alt_static_is_not_public_and_never_leaks_api`, `test_alt_static_reachable_with_token` |
| traversal incl. `/alt/../secrets.enc`, encodings, backslashes | `test_traversal_never_discloses_files` (×15 params, auth+noauth), `test_repo_dist_traversal_battery_stays_closed`, `test_outside_dist_target_actually_exists` |
| allowlist traversal refusal (single source of truth) + A2 pin | `test_allowlist_refuses_traversal_upstream` |
| no directory listing | `test_no_directory_listing_on_indexless_dir` |
| index cache policy (finding, not fixed here) | `test_index_shell_cache_control_prevents_stale_shell` (**non-strict xfail**, flips when sibling adds `no-store` on real index) |
| deep-link fallback (valid whether or not sibling lands/tweaks) | `test_spa_fallback_shell_cache_policy_is_pinned` (conditional), `test_hashed_asset_content_type_sanity` |
| `/alt` cannot shadow legacy `/` | `test_root_serves_legacy_dashboard_not_alt` |
| fallback cannot swallow the API | `test_api_404_is_not_swallowed_by_spa_fallback` |
| hashed bundle content-type sanity (real dist) | `test_repo_dist_index_and_bundle_serve_with_expected_types` |
| SSE auth consistency | `test_sse_stream_requires_the_same_token` |
| dist-absent behavior | module skips like the mount (`pytest.skip` with the mount's own reason) |
