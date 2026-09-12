/**
 * pro_api_client.test.mjs — offline transport tests for the alt console API
 * layer (frontend/src/api/client.ts).
 *
 * Covers:
 *   1. normalizeErrorEnvelope (pure): v1 envelope, legacy safe envelope,
 *      bare-string legacy error, HTML/non-JSON 500 body, 401 variants
 *      (middleware shape, empty body, AUTH_CONFIG_ERROR), request-id
 *      precedence, retryable fidelity — no fabricated structure.
 *   2. The central fetch wrapper under a STUBBED global.fetch: auth headers
 *      (Bearer + X-NSE-Token + X-Request-ID), v1 unwrap, 204/empty body
 *      (no JSON parse crash), INVALID_RESPONSE contract violation, per-request
 *      timeout -> ApiError TIMEOUT with typed requestId, caller abort -> raw
 *      AbortError preserved, stream paths pinned OUT of any timeout budget.
 *   3. Static invariants: no EventSource/localStorage inside client.ts,
 *      exactly one fetch dispatch (never auto-retries a mutation), the pinned
 *      ?token= capture -> sessionStorage -> URL-scrub flow intact, and the
 *      snake_case query-key convention in the api modules.
 *
 * Run from the repo root (no network, no backend, no npm install):
 *   node --test tests/js/pro_api_client.test.mjs
 * (Node >= 24 strips the TS types from the imported .ts modules at load time;
 * an alias-resolution hook maps "@/..." to frontend/src/... the same way
 * tsconfig paths + vite do.)
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { registerHooks } from "node:module";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..", "..");
const SRC = path.join(root, "frontend", "src");
const CLIENT_TS = path.join(SRC, "api", "client.ts");

// --- "@/" alias + extension resolution for type-stripped TS imports --------
registerHooks({
  resolve(spec, ctx, next) {
    if (spec.startsWith("@/")) {
      const p = path.join(SRC, spec.slice(2));
      const withExt = readCandidate(p);
      if (withExt) return { url: pathToFileUrl(withExt).href, shortCircuit: true };
    }
    if (spec.startsWith(".") && ctx.parentURL?.endsWith(".ts")) {
      const abs = path.resolve(path.dirname(fileURLToPath(ctx.parentURL)), spec);
      const withExt = readCandidate(abs);
      if (withExt) return { url: pathToFileUrl(withExt).href, shortCircuit: true };
    }
    return next(spec, ctx);
  },
});
function readCandidate(p) {
  for (const cand of [p, `${p}.ts`, path.join(p, "index.ts")]) {
    try {
      if (readFileSync(cand)) return cand;
    } catch {
      /* not a file, try next */
    }
  }
  return null;
}
function pathToFileUrl(p) {
  return new URL("file:///" + String(p).replaceAll("\\", "/").replace(/^\//, ""));
}

console.log(`node ${process.version}`);

// --- minimal browser globals the client touches (NO network anywhere) -------
/** Minimal window/sessionStorage stub; `store` may be shared across calls to
 *  simulate a page refresh (same tab, same sessionStorage, scrubbed URL). */
function installBrowserStub({ search = "", store = new Map() } = {}) {
  const scrubbed = [];
  globalThis.window = {
    location: {
      search,
      href: `http://alt.console.local/alt/${search}`,
    },
    history: {
      replaceState(_s, _t, url) {
        scrubbed.push(String(url));
      },
    },
    __NSE_WEB_TOKEN__: undefined,
  };
  globalThis.sessionStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => void store.set(k, String(v)),
    removeItem: (k) => void store.delete(k),
  };
  return { store, scrubbed };
}

const client = await import(pathToFileUrl(CLIENT_TS));
const {
  normalizeErrorEnvelope,
  getV1,
  getLegacy,
  send,
  request,
  getAuthToken,
  isStreamPath,
  DEFAULT_READ_TIMEOUT_MS,
  DEFAULT_MUTATION_TIMEOUT_MS,
} = client;
const { ApiError } = await import(pathToFileUrl(path.join(SRC, "types", "api.ts")));

// ---------------------------------------------------------------------------
// 1. normalizeErrorEnvelope — pure response-shape fidelity
// ---------------------------------------------------------------------------

test("v1 envelope: code/message/request_id/retryable pass through exactly", () => {
  const e = normalizeErrorEnvelope(
    503,
    {
      error: {
        code: "ENGINE_UNAVAILABLE",
        message: "The trading engine is not attached to the API server.",
        details: { hint: "attach engine" },
        request_id: "req_aaaaaaaaaa",
        retryable: true,
      },
    },
    "altui_client_rid",
  );
  assert.ok(e instanceof ApiError);
  assert.equal(e.status, 503);
  assert.equal(e.code, "ENGINE_UNAVAILABLE");
  assert.equal(e.message, "The trading engine is not attached to the API server.");
  assert.equal(e.requestId, "req_aaaaaaaaaa", "envelope request_id wins over the client id");
  assert.equal(e.retryable, true);
});

test("v1 envelope with retryable:false keeps false (backend verdict wins over 503 heuristic)", () => {
  const e = normalizeErrorEnvelope(503, { error: { code: "RESOURCE_UNAVAILABLE", message: "m", request_id: "r1", retryable: false } });
  assert.equal(e.retryable, false);
});

test("legacy safe envelope {available,success,error:{code,message,request_id}}", () => {
  const e = normalizeErrorEnvelope(
    500,
    { available: false, success: false, error: { code: "OPERATION_FAILED", message: "The operation could not be completed.", request_id: "req_b1" } },
    "fallback",
  );
  assert.equal(e.code, "OPERATION_FAILED");
  assert.equal(e.message, "The operation could not be completed.");
  assert.equal(e.requestId, "req_b1");
  assert.equal(e.retryable, false);
});

test("bare-string legacy error {error:'text'} keeps the text, code falls back by status", () => {
  const e = normalizeErrorEnvelope(500, { error: "boom from before hardening" });
  assert.equal(e.message, "boom from before hardening");
  assert.equal(e.code, "INTERNAL_ERROR");
  assert.equal(e.retryable, false);
});

test("HTML error page (status 500) does not throw and yields an honest generic error", () => {
  const html = "<!DOCTYPE html><html><head><title>500 Internal Server Error</title></head><body>nginx</body></html>";
  const e = normalizeErrorEnvelope(500, html, "altui_x1");
  assert.ok(e instanceof ApiError);
  assert.equal(e.status, 500);
  assert.equal(e.code, "INTERNAL_ERROR");
  assert.equal(e.message, "Request failed (HTTP 500).");
  assert.equal(e.requestId, "altui_x1", "requestId passthrough on unparseable bodies");
  assert.ok(!e.message.includes("nginx"), "no fabricated body content");
});

test("null body (empty response) still normalizes", () => {
  const e = normalizeErrorEnvelope(502, null);
  assert.equal(e.code, "INTERNAL_ERROR");
  assert.equal(e.message, "Backend unreachable.");
  assert.equal(e.requestId, null, "no id from anywhere stays null — never invented");
});

test("401 variant: auth middleware body {ok:false,error:{code:'UNAUTHORIZED',message}} has no request_id -> passthrough used", () => {
  const e = normalizeErrorEnvelope(401, { ok: false, error: { code: "UNAUTHORIZED", message: "missing or invalid web auth token" } }, "altui_401");
  assert.equal(e.code, "UNAUTHORIZED");
  assert.equal(e.isAuthError, true);
  assert.equal(e.requestId, "altui_401");
});

test("401 variant: empty body -> default auth message, isAuthError", () => {
  const e = normalizeErrorEnvelope(401, null);
  assert.equal(e.code, "UNAUTHORIZED");
  assert.equal(e.message, "Missing or invalid web auth token.");
  assert.equal(e.isAuthError, true);
});

test("AUTH_CONFIG_ERROR (500, fail-closed token resolution) is an auth error signal", () => {
  const e = normalizeErrorEnvelope(500, { ok: false, error: { code: "AUTH_CONFIG_ERROR", message: "web auth token unresolvable (fail-closed)" } });
  assert.equal(e.isAuthError, true);
  assert.equal(e.code, "AUTH_CONFIG_ERROR");
});

test("status 0 (network) defaults to NETWORK_ERROR-style generic message", () => {
  const e = normalizeErrorEnvelope(0, null, "altui_net");
  assert.equal(e.code, "NETWORK_ERROR");
  assert.equal(e.message, "Backend unreachable.");
  assert.equal(e.retryable, false, "retry policy belongs to react-query, envelope heuristic only marks 503/504");
  assert.equal(e.requestId, "altui_net");
});

test("FastAPI detail-shape body is surfaced, arrays never fabricated into objects", () => {
  const e = normalizeErrorEnvelope(422, { detail: [{ loc: ["query", "page"], msg: "value is not a valid integer" }] });
  assert.ok(e.message.includes("value is not a valid integer"));
  const arr = normalizeErrorEnvelope(500, ["unexpected", "array"]);
  assert.equal(arr.code, "INTERNAL_ERROR");
  assert.equal(arr.message, "Request failed (HTTP 500).");
});

// ---------------------------------------------------------------------------
// 2. the central fetch wrapper (stubbed global.fetch — offline)
// ---------------------------------------------------------------------------

/** Stub fetch: records calls, replays canned behavior. */
function stubFetch(behavior) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return behavior(url, init, calls.length);
  };
  return calls;
}

const json = (obj, init = {}) =>
  new Response(JSON.stringify(obj), { status: 200, headers: { "content-type": "application/json" }, ...init });

test("getV1: unwraps {data,meta}, sends Bearer + X-NSE-Token + X-Request-ID correlation headers", async () => {
  installBrowserStub();
  globalThis.sessionStorage.setItem("nse.altui.token", "tok_secret_1");
  const calls = stubFetch(() => json({ data: { ok: 1 }, meta: { request_id: "req_srv", generated_at: "x" } }));
  const out = await getV1("/api/v1/risk/summary");
  assert.deepEqual(out, { ok: 1 }, "payload unwrapped, meta dropped");
  const h = calls[0].init.headers;
  assert.equal(h["Authorization"], `Bearer ${"tok_secret_1"}`);
  assert.equal(h["X-NSE-Token"], "tok_secret_1");
  assert.match(String(h["X-Request-ID"]), /^altui_[a-z0-9]+_[a-z0-9]+$/, "client correlation id sent on every request");
  assert.equal(calls.length, 1, "exactly one dispatch — the transport never retries by itself");
});

test("401 through the real wrapper surfaces ApiError.isAuthError with the echoed header id", async () => {
  installBrowserStub();
  stubFetch(() =>
    new Response(JSON.stringify({ ok: false, error: { code: "UNAUTHORIZED", message: "missing or invalid web auth token" } }), {
      status: 401,
      headers: { "content-type": "application/json", "X-Request-ID": "req_echo_1" },
    }),
  );
  await assert.rejects(getLegacy("/api/status"), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.isAuthError, true);
    assert.equal(e.code, "UNAUTHORIZED");
    assert.equal(e.requestId, "req_echo_1", "response X-Request-ID beats the client guess");
    return true;
  });
});

test("204 empty body resolves to null WITHOUT a JSON parse crash", async () => {
  installBrowserStub();
  stubFetch(() => new Response(null, { status: 204 }));
  assert.equal(await getLegacy("/api/positions/close"), null);
});

test("legacy 200 + success:false refusal passes through verbatim (never faked)", async () => {
  installBrowserStub();
  stubFetch(() => json({ ok: true, success: false, message: "mode switch blocked in LIVE" }));
  const r = await send("/api/engine/mode", { mode: "LIVE" });
  assert.equal(r.success, false);
  assert.equal(r.message, "mode switch blocked in LIVE");
});

test("send(): JSON content-type + serialized body + mutation never re-dispatched on 500", async () => {
  installBrowserStub();
  const calls = stubFetch(() =>
    json({ available: false, success: false, error: { code: "OPERATION_FAILED", message: "The operation could not be completed.", request_id: "req_mut" } }, { status: 500 }),
  );
  await assert.rejects(send("/api/engine/toggle", { active: true }), (e) => {
    assert.equal(e.code, "OPERATION_FAILED");
    assert.equal(e.requestId, "req_mut");
    return true;
  });
  assert.equal(calls.length, 1, "mutations must NEVER be auto-retried by the transport");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
  assert.equal(calls[0].init.body, JSON.stringify({ active: true }));
});

test("v1 endpoint returning a non-envelope success body -> typed INVALID_RESPONSE, not undefined leak", async () => {
  installBrowserStub();
  stubFetch(() => json({ available: false }));
  await assert.rejects(getV1("/api/v1/system/status"), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.code, "INVALID_RESPONSE");
    assert.equal(e.retryable, false);
    return true;
  });
});

test("v1 envelope with data:null returns null (backend verdict kept, not replaced with a guess)", async () => {
  installBrowserStub();
  stubFetch(() => json({ data: null, meta: { request_id: "r", generated_at: "g" } }));
  assert.equal(await getV1("/api/v1/runtime/mode"), null);
});

test("per-request timeout: local budget aborts a hung fetch -> ApiError TIMEOUT with typed requestId", async () => {
  installBrowserStub();
  stubFetch(
    (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => {
          reject(new DOMException("This operation was aborted", "AbortError"));
        });
      }),
  );
  await assert.rejects(request("/api/status", { timeoutMs: 30 }), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.code, "TIMEOUT");
    assert.equal(e.status, 0);
    assert.equal(e.retryable, true);
    assert.match(String(e.requestId), /^altui_/, "network failure carries the client correlation id");
    return true;
  });
});

test("plain network rejection (no HTTP response) -> typed NETWORK_ERROR carrying the client requestId", async () => {
  installBrowserStub();
  stubFetch(() => {
    throw new TypeError("fetch failed");
  });
  await assert.rejects(getLegacy("/api/status"), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.status, 0);
    assert.equal(e.code, "NETWORK_ERROR");
    assert.equal(e.retryable, true);
    assert.match(String(e.requestId), /^altui_/, "requestId passthrough even when the backend never answered");
    return true;
  });
});

test("fallback path (no AbortSignal.timeout in the runtime): manual controller+timer still bounds the request", async () => {
  installBrowserStub();
  const original = AbortSignal.timeout;
  assert.equal(typeof original, "function", "precondition: Node has AbortSignal.timeout");
  delete AbortSignal.timeout; // simulate an older runtime (feature check must degrade, not crash)
  try {
    stubFetch(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
    );
    await assert.rejects(request("/api/status", { timeoutMs: 25 }), (e) => {
      assert.ok(e instanceof ApiError);
      assert.equal(e.code, "TIMEOUT");
      assert.match(String(e.requestId), /^altui_/);
      return true;
    });
  } finally {
    AbortSignal.timeout = original;
  }
});

test("caller-supplied abort (react-query cancellation) propagates as the raw AbortError", async () => {
  installBrowserStub();
  stubFetch(
    (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      }),
  );
  const ac = new AbortController();
  const p = getLegacy("/api/status", ac.signal);
  ac.abort();
  await assert.rejects(p, (e) => {
    assert.ok(!(e instanceof ApiError), "cancellation must not be dressed up as a backend error");
    assert.equal(e.name, "AbortError");
    return true;
  });
});

test("SSE/stream path is PINNED out of the timeout budget (a deadline would kill the live feed)", async () => {
  installBrowserStub();
  let sawSignal = null;
  stubFetch(
    (_url, init) =>
      new Promise((resolve) => {
        sawSignal = init.signal;
        setTimeout(() => resolve(json({ data: 1, meta: {} })), 60);
      }),
  );
  assert.equal(isStreamPath("/api/ticks/stream?token=x"), true);
  assert.equal(isStreamPath("/api/status"), false);
  const out = await request("/api/ticks/stream?token=abc", { timeoutMs: 10 });
  assert.equal(sawSignal.aborted, false, "no local abort fired against the stream connection");
  assert.deepEqual(out, { data: 1, meta: {} });
});

test("defaults: reads 15s, mutations 30s; garbage timeout values fall back, never unbounded", async () => {
  assert.equal(DEFAULT_READ_TIMEOUT_MS, 15_000);
  assert.equal(DEFAULT_MUTATION_TIMEOUT_MS, 30_000);
  installBrowserStub();
  const calls = stubFetch(() => json({ ok: true }));
  await request("/api/status", { timeoutMs: Number.NaN }); // ignored -> default
  await request("/api/status", { timeoutMs: -5 }); // ignored -> default
  await request("/api/status", { timeoutMs: 10 }); // honored
  assert.equal(calls.length, 3);
});

// ---------------------------------------------------------------------------
// 3. pinned invariants (static scans of shipped source)
// ---------------------------------------------------------------------------

const clientSrc = readFileSync(CLIENT_TS, "utf8");

test("PINNED: ?token= capture -> sessionStorage -> URL scrub is still the shipped flow", () => {
  assert.ok(clientSrc.includes('sessionStorage.setItem(TOKEN_STORAGE_KEY, qp)'));
  assert.ok(clientSrc.includes('url.searchParams.delete("token")'));
  assert.ok(clientSrc.includes("window.history.replaceState"));
  assert.ok(clientSrc.includes('new URLSearchParams(window.location.search).get("token")'));
});

test("token NEVER lands in the durable web store (sessionStorage only)", () => {
  // client.ts mentions localStorage in its contract comment; what must be
  // true is that no code path calls into it.
  assert.ok(!/localStorage\s*\./.test(clientSrc), "client.ts must not call localStorage APIs");
  assert.ok(!/\bgetLocalStorage\b|localStorage\s*[=)]/.test(clientSrc));
});

test("client.ts never touches the EventSource/SSE transport (owned by realtimeSocket)", () => {
  assert.ok(!/EventSource/.test(clientSrc));
  assert.ok(!/text\/event-stream/.test(clientSrc));
});

test("transport dispatches fetch exactly once per request (no retry loop of its own)", () => {
  const dispatches = (clientSrc.match(/await fetch\(/g) ?? []).length;
  assert.equal(dispatches, 1);
});

test("PINNED at runtime: ?token= capture lands in sessionStorage and is scrubbed from the URL", async () => {
  const shared = new Map();
  const { scrubbed } = installBrowserStub({ search: "?token=deep_link_secret", store: shared });
  assert.equal(getAuthToken(), "deep_link_secret");
  assert.equal(shared.get("nse.altui.token"), "deep_link_secret");
  assert.equal(scrubbed.length, 1);
  assert.ok(!scrubbed[0].includes("deep_link_secret"), "replaceState URL must not carry the token");
  // Simulated page refresh: URL is clean now, sessionStorage is the source of truth.
  installBrowserStub({ store: shared });
  assert.equal(getAuthToken(), "deep_link_secret");
  shared.delete("nse.altui.token");
  assert.equal(getAuthToken(), null);
});

test("query-key convention: api modules send snake_case wire keys via URLSearchParams", () => {
  const apiDir = path.join(SRC, "api");
  const files = ["auditApi.ts", "positionsApi.ts", "intelligenceApi.ts"];
  for (const f of files) {
    const src = readFileSync(path.join(apiDir, f), "utf8");
    const setKeys = [...src.matchAll(/q\.set\(\s*"([^"]+)"/g)].map((m) => m[1]);
    for (const k of setKeys) {
      assert.match(k, /^[a-z][a-z0-9_]*$/, `${f}: wire query key "${k}" must be snake_case (backend contract)`);
    }
  }
  const audit = readFileSync(path.join(apiDir, "auditApi.ts"), "utf8");
  for (const k of ["page_size", "event_type", "severity", "status", "page"]) {
    assert.ok(audit.includes(`"${k}"`), `auditApi must use the backend key ${k}`);
  }
});

test("endpoint strings are unchanged (NO route changes allowed in this lane)", () => {
  // Pinned contract surface verified against src/nexus_scalp/web/ (api_v1 +
  // legacy routes). Any change here is a backend-contract break. Route
  // strings are checked WITHOUT quotes so both `"..."` and `` `...${qs}` ``
  // forms are covered.
  const pins = [
    ["engineApi.ts", "/api/status"],
    ["engineApi.ts", "/api/mt5/status"],
    ["engineApi.ts", "/api/engine/toggle"],
    ["engineApi.ts", "/api/engine/mode"],
    ["engineApi.ts", "/api/v1/system/status"],
    ["tradingApi.ts", "/api/positions/close"],
    ["tradingApi.ts", "/api/positions/modify"],
    ["riskApi.ts", "/api/v1/risk/status"],
    ["riskApi.ts", "/api/v1/risk/summary"],
    ["riskApi.ts", "/api/debug/state"],
    ["mlApi.ts", "/api/models/integrity"],
    ["mlApi.ts", "/api/models/shadow70/summary"],
    ["mlApi.ts", "/api/v1/model/status"],
    ["mlApi.ts", "/api/v1/model/identity"],
    ["mlApi.ts", "/api/v1/features/status"],
    ["auditApi.ts", "/api/v1/audit/events"],
    ["auditApi.ts", "/api/v1/observability/events"],
    ["auditApi.ts", "/api/v1/incidents"],
    ["auditApi.ts", "/api/v1/database/status"],
    ["auditApi.ts", "/api/v1/database/integrity"],
    ["intelligenceApi.ts", "/api/news/state"],
    ["intelligenceApi.ts", "/api/news/health"],
    ["intelligenceApi.ts", "/api/news/latest"],
    ["intelligenceApi.ts", "/api/intelligence/summary"],
    ["intelligenceApi.ts", "/api/intelligence/autopsies"],
    ["positionsApi.ts", "/api/v1/positions"],
    ["positionsApi.ts", "/api/account/trades"],
    ["positionsApi.ts", "/api/v1/execution/history"],
    ["marketApi.ts", "/api/v1/market/quote"],
    ["marketApi.ts", "/api/v1/runtime/mode"],
    ["marketApi.ts", "/api/v1/runtime/freshness"],
  ];
  for (const [file, pin] of pins) {
    const src = readFileSync(path.join(SRC, "api", file), "utf8");
    assert.ok(src.includes(pin), `${file} lost endpoint ${pin} — route strings are the backend contract`);
  }
});

test("realtime SSE url keeps the ?token= query transport (WEB-AUTH-P0 headerless client)", () => {
  const src = readFileSync(path.join(SRC, "websocket", "realtimeSocket.ts"), "utf8");
  assert.ok(src.includes("/api/ticks/stream"));
  assert.ok(/token=/.test(src), "SSE authenticates via ?token= because EventSource cannot set headers");
});
