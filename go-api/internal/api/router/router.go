// Package router is a first-match-in-declaration-order regex router.
//
// The NSE surface contains route PAIRS that are genuinely ambiguous for Go's
// ServeMux — POST /api/news/{article_id}/restore and POST /api/news/analyze/{article_id}
// both match /api/news/analyze/restore and neither is more specific, so
// ServeMux panics rather than picking. Starlette resolves this by DECLARATION
// ORDER: the pattern registered first wins. That is the contract the React
// client depends on, so this router reproduces it instead of approximating it.
//
// The table is generated from Python's own resolved route dump, so the order
// here IS the order FastAPI serves, and 404-vs-405 semantics follow for free:
// a path matching a registered template with an unregistered method yields
// METHOD_NOT_ALLOWED, exactly as Starlette's automatic routing does.
package router

import (
	"net/http"
	"regexp"
	"strings"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/respond"
)

// Mux is a declaration-ordered regex router.
type Mux struct {
	entries []entry
}

type entry struct {
	method  string
	pattern *regexp.Regexp
	handler http.Handler
}

// New returns an empty Mux.
func New() *Mux { return &Mux{} }

// compile converts a FastAPI path template to a regex anchored to the whole
// path. {name} matches one segment; {name:path} matches one or more segments,
// so the trailing remainder is optional only in that case.
func compile(path string) *regexp.Regexp {
	// strings.Split("/a/b", "/") yields ["", "a", "b"]: the leading empty
	// element stands for the root and contributes NO slash of its own. Writing
	// one per segment makes "/" compile to "^//$" (matching "//" only) and
	// every absolute path double its leading slash — nothing matches.
	segs := strings.Split(path, "/")
	var b strings.Builder
	b.WriteString("^")
	for i, seg := range segs {
		if i == 0 {
			continue
		}
		if seg == "" {
			// Root ("/") or a trailing slash. A template ending in "/"
			// requires that slash; the root IS the slash alone.
			if i == len(segs)-1 && len(segs) > 1 {
				b.WriteString("/")
			}
			continue
		}
		b.WriteString("/")
		switch {
		case strings.HasSuffix(seg, ":path}"):
			// multi-segment: FastAPI allows zero extra segments too
			b.WriteString("(.*)")
		case strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}"):
			b.WriteString("([^/]+)")
		default:
			b.WriteString(regexp.QuoteMeta(seg))
		}
	}
	b.WriteString("$")
	return regexp.MustCompile(b.String())
}

// Handle registers a method+path pattern. Registration order is authoritative
// for matching priority, mirroring Starlette.
func (m *Mux) Handle(method, path string, h http.Handler) {
	m.entries = append(m.entries, entry{
		method:  method,
		pattern: compile(path),
		handler: h,
	})
}

// HandleFunc is a convenience wrapper around Handle.
func (m *Mux) HandleFunc(method, path string, f http.HandlerFunc) {
	m.Handle(method, path, f)
}

// notFound and methodNotAllowed markers retained for the legacy-surface
// fallback wiring (routes.go decides which envelope applies).
type fallbacks struct{}

// NotFound emits the canonical v1 404 envelope.
func NotFound(w http.ResponseWriter, r *http.Request) {
	respond.NotFound(w, r)
}

// MethodNotAllowed emits the canonical v1 405 envelope.
func MethodNotAllowed(w http.ResponseWriter, r *http.Request) {
	respond.MethodNotAllowed(w, r)
}

// ServeHTTP dispatches to the first pattern matching r.URL.Path.
//
// Starlette semantics: if ANY pattern for the path matches but the method is
// not registered there, the answer is 405, not 404 — and the candidate set is
// the set of patterns sharing that path, in declaration order.
func (m *Mux) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path
	pathMatches := false
	for _, e := range m.entries {
		if !e.pattern.MatchString(path) {
			continue
		}
		pathMatches = true
		if e.method == r.Method {
			e.handler.ServeHTTP(w, r)
			return
		}
	}
	if pathMatches {
		MethodNotAllowed(w, r)
		return
	}
	NotFound(w, r)
}
