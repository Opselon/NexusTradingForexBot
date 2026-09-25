// Package routes — registration of the generated (proxied) surface.
package routes

import (
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/handlers"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/router"
)

// registerGenerated mounts every generated route through the generic proxy.
//
// The table is generated from Python's own resolved route dump and preserves
// its declaration order, which the regex router uses as match priority — the
// same priority FastAPI applies. Duplicate method+path pairs are skipped.
func registerGenerated(r *router.Mux, p *handlers.Proxy) {
	seen := make(map[string]struct{}, len(generatedRoutes))
	for _, e := range generatedRoutes {
		key := e.Method + " " + e.Path
		if _, hit := seen[key]; hit {
			continue
		}
		seen[key] = struct{}{}
		r.Handle(e.Method, e.Path, p.Handler(e.Method, e.Path))
	}
}
