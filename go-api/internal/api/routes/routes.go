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

	// ---- Method-not-allowed: Go's ServeMux redirects a path registered with
	// a method pattern; registering the remaining verbs explicitly gives the
	// FastAPI-correct 405 (rather than 404) on a known path. ----
	registerMethodNotAllowed := func(path string) {
		mux.HandleFunc("POST "+path, handlers.MethodNotAllowedHandler)
		mux.HandleFunc("PUT "+path, handlers.MethodNotAllowedHandler)
		mux.HandleFunc("DELETE "+path, handlers.MethodNotAllowedHandler)
		mux.HandleFunc("PATCH "+path, handlers.MethodNotAllowedHandler)
		mux.HandleFunc("HEAD "+path, handlers.MethodNotAllowedHandler)
		mux.HandleFunc("OPTIONS "+path, handlers.MethodNotAllowedHandler)
	}
	for _, path := range []string{
		"/api/v1/system/health", "/api/v1/system/status",
		"/api/v1/system/readiness", "/api/v1/system/version", "/api/v1/system/runtime",
		"/api/v1/system/capabilities", "/api/v1/system/workers",
		"/api/v1/system/diagnostics",
	} {
		registerMethodNotAllowed(path)
	}
	// These two are POST endpoints: register the read verbs only.
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
