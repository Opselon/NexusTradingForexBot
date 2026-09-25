// Package routes builds the NSE Go API route table.
//
// Order matters for parity with FastAPI:
//  1. correlation middleware  (X-Request-ID on EVERY response)
//  2. auth middleware          (fail-closed on every non-public path)
//  3. route dispatch
//
// FastAPI emits an automatic 405 when a path exists but the method does not,
// and a 404 otherwise. Go's ServeMux (1.22+) supports method patterns
// ("GET /path"), which gives the same split, so the two bodies stay distinct
// and byte-compatible.
package routes

import (
	"log/slog"
	"net/http"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/handlers"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/respond"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/router"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/observability"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/security/auth"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/web"
)

// Build assembles the full middleware chain + route table and returns the
// root handler ready to be served.
//
// All routes — hand-written Phase A/B and the generated surface alike — are
// registered on ONE declaration-ordered router, because matching priority is
// global in FastAPI: the first matching pattern wins regardless of which
// module declared it. Splitting them across two routers would let the wrong
// one win for the ambiguous pairs.
func Build(py *python.Client) http.Handler {
	sys := handlers.NewSystem(py)
	r := router.New()

	// ---- Phase A: 10 operations ----
	r.HandleFunc("GET", "/api/v1/system/health", sys.Health)
	r.HandleFunc("GET", "/api/v1/system/status", sys.Status)
	r.HandleFunc("GET", "/api/v1/system/readiness", sys.Readiness)
	r.HandleFunc("GET", "/api/v1/system/version", sys.Version)
	r.HandleFunc("GET", "/api/v1/system/runtime", sys.Runtime)
	r.HandleFunc("GET", "/api/v1/system/capabilities", sys.Capabilities)
	r.HandleFunc("GET", "/api/v1/system/workers", sys.Workers)
	r.HandleFunc("GET", "/api/v1/system/diagnostics", sys.Diagnostics)
	r.HandleFunc("POST", "/api/v1/system/diagnostics/run", sys.DiagnosticsRun)
	r.HandleFunc("POST", "/api/v1/system/refresh", sys.Refresh)

	// ---- Phase B: read-only domains ----
	rec := handlers.NewResearch(py)
	r.HandleFunc("GET", "/api/v1/research/status", rec.Status)
	r.HandleFunc("GET", "/api/v1/research/strategies", rec.Strategies)
	r.HandleFunc("GET", "/api/v1/research/strategies/{strategy_id}", rec.StrategyDetail)
	r.HandleFunc("GET", "/api/v1/research/runs", rec.Runs)
	r.HandleFunc("GET", "/api/v1/research/datasets", rec.Datasets)

	risk := handlers.NewRisk(py)
	r.HandleFunc("GET", "/api/v1/risk/status", risk.Status)
	r.HandleFunc("GET", "/api/v1/risk/summary", risk.Summary)

	rt := handlers.NewRuntime(py)
	r.HandleFunc("GET", "/api/v1/runtime/mode", rt.Mode)
	r.HandleFunc("GET", "/api/v1/runtime/freshness", rt.Freshness)
	r.HandleFunc("GET", "/api/v1/runtime/shutdown", rt.Shutdown)

	// ---- 404-vs-405 for the Phase A + B paths is DERIVED by the regex
	// router: when the path matches a registered template but the method does
	// not, the answer is 405, exactly as Starlette's automatic routing does.
	// No explicit wrong-verb registrations are needed. ----

	// Chain: correlation outermost (id available to auth + handlers),
	// then auth (fail-closed), then dispatch.
	chain := func(h http.Handler) http.Handler {
		if !auth.Disabled() {
			h = auth.Middleware(h)
		} else {
			// Install-time rollback knob (mirrors _install_web_auth_if_enabled).
			// Never combine with LIVE execution or a routable host binding.
			slog.Warn("[WEB-AUTH] DISABLED via NSE_WEB_AUTH_DISABLE=1 — NEVER " +
				"combine with LIVE execution mode or a routable host binding.")
		}
		return observability.RequestIDMiddleware(h)
	}

	// ---- Phase C: the full remaining surface. The route table is generated
	// from Python's own resolved route dump, so the Go router registers
	// exactly the operations Python serves — no silent drift. Every entry is
	// a byte-for-byte pass-through; Python remains the fact authority and the
	// sole place validation happens. The regex router preserves FastAPI's
	// declaration-order priority, which Go's ServeMux cannot express for the
	// ambiguous {id}+literal pairs the surface contains. ----
	proxy := handlers.NewProxy(py)
	registerGenerated(r, proxy)

	// ---- fallback: unknown /api/v1 path -> canonical v1 404 envelope ----
	r.HandleFunc("GET", "/api/v1/", v1Fallback)

	// ---- STATIC FRONTEND (END-USER-RUNTIME-UI-INTEGRATION, frozen #4/#6):
	// the built React Control Center bundle, served from THIS origin so Go is
	// the single origin for BOTH the API and the UI in production.
	//
	// REGISTRATION ORDER IS THE CONTRACT: this is registered LAST, after every
	// /api route and the /api/v1 fallback, so the API ALWAYS WINS. The regex
	// router asks the API surface first; only a path the whole API rejected
	// reaches the static layer. This mirrors Starlette's first-match
	// semantics — app.mount("/", root_spa) is the VERY LAST route in
	// server.py's create_app for exactly this reason. Additionally, the SPA
	// layer's own deny list (web.denyPrefixes/denyPaths, mirroring
	// ROOT_SPA_DENY_PREFIXES + the Python public-path set) passes /api, /ws,
	// /web, /health, /healthz, /app.js and /api_client.js through untouched,
	// so an unmatched API child is an honest 404 — never the index document
	// (§60: a typo'd API call must never receive HTML the client parses as
	// data). With no resolvable dist the layer is a no-op and unknown paths
	// keep the pre-wave honest-404 behavior (contract #4, zero regression). ----
	return chain(web.ServeSPA(r))
}

// v1Fallback distinguishes "path unknown" (404) from "method wrong" (405)
// for /api/v1 paths, so the error body matches FastAPI's automatic handling.
func v1Fallback(w http.ResponseWriter, r *http.Request) {
	handlers.NotFoundHandler(w, r)
}

// IsV1Path is kept exported for the parity harness; the canonical helper lives
// in respond (it mirrors common._is_v1_path, which every handler needs).
func IsV1Path(p string) bool { return respond.IsV1Path(p) }
