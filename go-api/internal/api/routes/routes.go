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
	"strings"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/handlers"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/observability"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/security/auth"
)

// Build assembles the full middleware chain + route table and returns the
// root handler ready to be served.
func Build(py *python.Client) http.Handler {
	mux := http.NewServeMux()
	sys := handlers.NewSystem(py)

	// ---- Phase A: 10 operations. Registered with METHOD PATTERNS so Go's
	// 1.22+ ServeMux distinguishes "path unknown" (404) from "method wrong"
	// (405) exactly as FastAPI's automatic routing does. ----
	mux.HandleFunc("GET /api/v1/system/health", sys.Health)
	mux.HandleFunc("GET /api/v1/system/status", sys.Status)
	mux.HandleFunc("GET /api/v1/system/readiness", sys.Readiness)
	mux.HandleFunc("GET /api/v1/system/version", sys.Version)
	mux.HandleFunc("GET /api/v1/system/runtime", sys.Runtime)
	mux.HandleFunc("GET /api/v1/system/capabilities", sys.Capabilities)
	mux.HandleFunc("GET /api/v1/system/workers", sys.Workers)
	mux.HandleFunc("GET /api/v1/system/diagnostics", sys.Diagnostics)
	mux.HandleFunc("POST /api/v1/system/diagnostics/run", sys.DiagnosticsRun)
	mux.HandleFunc("POST /api/v1/system/refresh", sys.Refresh)

	// ---- Phase B: read-only domains ----
	rec := handlers.NewResearch(py)
	mux.HandleFunc("GET /api/v1/research/status", rec.Status)
	mux.HandleFunc("GET /api/v1/research/strategies", rec.Strategies)
	mux.HandleFunc("GET /api/v1/research/strategies/{strategy_id}", rec.StrategyDetail)
	mux.HandleFunc("GET /api/v1/research/runs", rec.Runs)
	mux.HandleFunc("GET /api/v1/research/datasets", rec.Datasets)

	risk := handlers.NewRisk(py)
	mux.HandleFunc("GET /api/v1/risk/status", risk.Status)
	mux.HandleFunc("GET /api/v1/risk/summary", risk.Summary)

	rt := handlers.NewRuntime(py)
	mux.HandleFunc("GET /api/v1/runtime/mode", rt.Mode)
	mux.HandleFunc("GET /api/v1/runtime/freshness", rt.Freshness)
	mux.HandleFunc("GET /api/v1/runtime/shutdown", rt.Shutdown)

	// ---- Method-not-allowed for every Phase A + B path: a known path with
	// the wrong verb must answer 405, not 404 (FastAPI parity). ----
	knownGet := []string{
		"/api/v1/system/health", "/api/v1/system/status", "/api/v1/system/readiness",
		"/api/v1/system/version", "/api/v1/system/runtime", "/api/v1/system/capabilities",
		"/api/v1/system/workers", "/api/v1/system/diagnostics",
		"/api/v1/research/status", "/api/v1/research/strategies",
		"/api/v1/research/strategies/{strategy_id}", "/api/v1/research/runs",
		"/api/v1/research/datasets",
		"/api/v1/risk/status", "/api/v1/risk/summary",
		"/api/v1/runtime/mode", "/api/v1/runtime/freshness", "/api/v1/runtime/shutdown",
	}
	for _, path := range knownGet {
		mux.HandleFunc("POST "+path, handlers.MethodNotAllowedHandler)
		mux.HandleFunc("PUT "+path, handlers.MethodNotAllowedHandler)
		mux.HandleFunc("DELETE "+path, handlers.MethodNotAllowedHandler)
		mux.HandleFunc("PATCH "+path, handlers.MethodNotAllowedHandler)
	}
	// POST-only endpoints from Phase A: register the read verbs only.
	for _, path := range []string{"/api/v1/system/diagnostics/run", "/api/v1/system/refresh"} {
		mux.HandleFunc("GET "+path, handlers.MethodNotAllowedHandler)
		mux.HandleFunc("PUT "+path, handlers.MethodNotAllowedHandler)
		mux.HandleFunc("DELETE "+path, handlers.MethodNotAllowedHandler)
		mux.HandleFunc("PATCH "+path, handlers.MethodNotAllowedHandler)
	}

	// ---- fallback: unknown /api/v1 path -> canonical v1 404 envelope ----
	mux.HandleFunc("/api/v1/", v1Fallback)

	// Chain: correlation outermost (id available to auth + handlers),
	// then auth (fail-closed), then dispatch.
	var h http.Handler = mux
	if auth.Disabled() {
		// Install-time rollback knob (mirrors _install_web_auth_if_enabled).
		// Never combine with LIVE execution or a routable host binding.
		slog.Warn("[WEB-AUTH] DISABLED via NSE_WEB_AUTH_DISABLE=1 — NEVER " +
			"combine with LIVE execution mode or a routable host binding.")
	} else {
		h = auth.Middleware(h)
	}
	h = observability.RequestIDMiddleware(h)
	return h
}

// v1Fallback distinguishes "path unknown" (404) from "method wrong" (405)
// for /api/v1 paths, so the error body matches FastAPI's automatic handling.
func v1Fallback(w http.ResponseWriter, r *http.Request) {
	handlers.NotFoundHandler(w, r)
}

// IsV1Path mirrors common._is_v1_path: the v1 envelope applies only under
// /api/v1. Exported for the parity harness.
func IsV1Path(p string) bool { return strings.HasPrefix(p, "/api/v1") }
