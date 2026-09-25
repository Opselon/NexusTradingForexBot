// Package routes — handwritten Phase A/B handlers kept out of the generated
// table. These endpoints have Go-local behaviour that a pure forwarder cannot
// express (pagination validation, capability proxying, bootstrap cookies).
package routes

// handwrittenRoutes are served by dedicated handlers, not by the generic
// proxy. Their entries are excluded from table_gen.go.
var handwrittenRoutes = []routeEntry{
	{Method: "GET", Path: "/api/v1/system/health", IsV1: true},
	{Method: "GET", Path: "/api/v1/system/status", IsV1: true},
	{Method: "GET", Path: "/api/v1/system/readiness", IsV1: true},
	{Method: "GET", Path: "/api/v1/system/version", IsV1: true},
	{Method: "GET", Path: "/api/v1/system/runtime", IsV1: true},
	{Method: "GET", Path: "/api/v1/system/capabilities", IsV1: true},
	{Method: "GET", Path: "/api/v1/system/workers", IsV1: true},
	{Method: "GET", Path: "/api/v1/system/diagnostics", IsV1: true},
	{Method: "POST", Path: "/api/v1/system/diagnostics/run", IsV1: true},
	{Method: "POST", Path: "/api/v1/system/refresh", IsV1: true},
	{Method: "GET", Path: "/api/v1/research/status", IsV1: true},
	{Method: "GET", Path: "/api/v1/research/strategies", IsV1: true},
	{Method: "GET", Path: "/api/v1/research/strategies/{strategy_id}", IsV1: true},
	{Method: "GET", Path: "/api/v1/research/runs", IsV1: true},
	{Method: "GET", Path: "/api/v1/research/datasets", IsV1: true},
	{Method: "GET", Path: "/api/v1/risk/status", IsV1: true},
	{Method: "GET", Path: "/api/v1/risk/summary", IsV1: true},
	{Method: "GET", Path: "/api/v1/runtime/mode", IsV1: true},
	{Method: "GET", Path: "/api/v1/runtime/freshness", IsV1: true},
	{Method: "GET", Path: "/api/v1/runtime/shutdown", IsV1: true},
}
