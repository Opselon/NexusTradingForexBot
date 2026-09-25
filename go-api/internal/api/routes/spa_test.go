// Package routes tests the API-wins-over-SPA contract: the static frontend
// layer is the lowest-priority catch-all, so every registered API route and
// the deny list keep winning, and an unmatched non-API path gets the shell.
package routes

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// distResolver is the memoized frontend-dist seam (internal/web). Tests reset
// it because t.Setenv changes the NEXUS_ALT_UI_DIR slot between cases and the
// memo would otherwise pin the first test's resolution.
type distResolver interface {
	resetDistCache()
}

// apiMarker proves a request reached the API surface rather than being
// swallowed by the static layer.
const apiMarker = "RESOURCE_NOT_FOUND"

// withDistFixture builds the full handler chain (routes.Build) against a tiny
// dist fixture and returns it plus the fixture directory.
func withDistFixture(t *testing.T) (http.Handler, string) {
	t.Helper()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "index.html"),
		[]byte("<!doctype html><html>"+spaMarker+"</html>"), 0o644); err != nil {
		t.Fatalf("write fixture index.html: %v", err)
	}
	t.Setenv("NEXUS_ALT_UI_DIR", dir)

	h := Build(nil) // no Python origin: API handlers report unavailable, which
	// is enough to prove dispatch priority — the static layer must still lose.
	return h, dir
}

// TestRegisteredAPIRouteWinsOverSPA proves registration order: a registered
// /api/v1 route is answered by the API (DEPENDENCY_UNAVAILABLE with no
// engine attached), NEVER by the SPA shell.
func TestRegisteredAPIRouteWinsOverSPA(t *testing.T) {
	h, _ := withDistFixture(t)

	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet,
		"/api/v1/system/health", nil))

	if !strings.Contains(rec.Body.String(), apiMarker) &&
		!strings.Contains(rec.Body.String(), "DEPENDENCY_UNAVAILABLE") {
		t.Errorf("/api/v1/system/health: body = %q — the API surface did not "+
			"win (it must never be shadowed by the SPA layer)", rec.Body.String())
	}
	if strings.Contains(rec.Body.String(), spaMarker) {
		t.Errorf("/api/v1/system/health: the SPA shell was served for a " +
			"registered API route (the API must always win)")
	}
}

// TestUnknownAPIPathIsNotShell proves the deny list: an unmatched /api path
// is an honest 404 envelope, never the index document (§60 — a typo'd API
// call must never receive HTML the client parses as data).
func TestUnknownAPIPathIsNotShell(t *testing.T) {
	h, _ := withDistFixture(t)

	for _, target := range []string{
		"/api/v1/anything-at-all",
		"/api/typo/op",
		"/health",
		"/app.js",
		"/api_client.js",
	} {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, target, nil))
		if strings.Contains(rec.Body.String(), spaMarker) {
			t.Errorf("GET %s: the deny-list path was served the SPA shell — "+
				"it must get an honest 404", target)
		}
		if !strings.Contains(rec.Body.String(), apiMarker) {
			t.Errorf("GET %s: body = %q, want the canonical 404 envelope",
				target, rec.Body.String())
		}
	}
}

// TestUnmatchedNonAPIPathServesShell proves the SPA fallback fires through
// the full route table: a client-side route receives the index document.
func TestUnmatchedNonAPIPathServesShell(t *testing.T) {
	h, _ := withDistFixture(t)

	for _, target := range []string{"/trading", "/positions/open", "/deep/spa/route"} {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, target, nil))
		if rec.Code != http.StatusOK {
			t.Errorf("GET %s: status = %d, want 200 (the SPA shell)", target, rec.Code)
			continue
		}
		if !strings.Contains(rec.Body.String(), spaMarker) {
			t.Errorf("GET %s: body = %q, want the index document", target,
				rec.Body.String())
		}
	}
}

// TestNoDistKeepsHonest404 proves the zero-regression contract (#4): with no
// resolvable dist, unknown paths keep the pre-wave honest-404 behavior.
func TestNoDistKeepsHonest404(t *testing.T) {
	t.Setenv("NEXUS_ALT_UI_DIR", "/nonexistent/no-such-dist")
	h := Build(nil)

	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/trading", nil))
	if rec.Code != http.StatusNotFound {
		t.Errorf("/trading with no dist: status = %d, want 404 (zero "+
			"regression — no shell may be invented)", rec.Code)
	}
}
