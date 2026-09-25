// Package router tests cover the Starlette-compatibility contract: first
// match in declaration order wins, and a matching path with an unregistered
// method yields 405 rather than 404.
package router

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// recordingHandler returns the pattern that matched, so tests can assert
// WHICH route won — not just that some route matched.
func recordingHandler(name string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(name))
	}
}

// TestRootAndTrailingSlash guards the path-compilation edge: "/" is the root,
// and a template ending in "/" must still require its slash.
func TestRootAndTrailingSlash(t *testing.T) {
	m := New()
	m.HandleFunc("GET", "/", recordingHandler("root"))
	m.HandleFunc("GET", "/api/v1/", recordingHandler("v1fallback"))

	for path, want := range map[string]string{
		"/":         "root",
		"/api/v1/":  "v1fallback",
		"/api/v1/x": "",
	} {
		rec := httptest.NewRecorder()
		m.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))
		if want == "" {
			if rec.Code != http.StatusNotFound {
				t.Errorf("path %q: status = %d, want 404", path, rec.Code)
			}
			continue
		}
		if got := rec.Body.String(); got != want {
			t.Errorf("path %q: winner = %q, want %q", path, got, want)
		}
	}
}

func TestLiteralMatches(t *testing.T) {
	m := New()
	m.HandleFunc("GET", "/api/v1/system/health", recordingHandler("health"))

	rec := httptest.NewRecorder()
	m.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/system/health", nil))

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if got := rec.Body.String(); got != "health" {
		t.Fatalf("body = %q, want %q", got, "health")
	}
}

// TestParamSegments asserts {name} matches exactly one segment.
func TestParamSegments(t *testing.T) {
	m := New()
	m.HandleFunc("GET", "/api/news/{article_id}", recordingHandler("article"))

	for path, want := range map[string]int{
		"/api/news/abc":     http.StatusOK,
		"/api/news/abc/def": http.StatusNotFound, // two segments, no match
		"/api/news":         http.StatusNotFound, // zero segments
	} {
		rec := httptest.NewRecorder()
		m.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))
		if rec.Code != want {
			t.Errorf("path %q: status = %d, want %d", path, rec.Code, want)
		}
	}
}

// TestPathParam is FastAPI's {name:path} — multiple segments.
func TestPathParam(t *testing.T) {
	m := New()
	m.HandleFunc("GET", "/api/dependency/node/{node_id:path}", recordingHandler("node"))

	for _, path := range []string{
		"/api/dependency/node/a",
		"/api/dependency/node/a/b",
		"/api/dependency/node/a/b/c",
	} {
		rec := httptest.NewRecorder()
		m.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))
		if rec.Code != http.StatusOK {
			t.Errorf("path %q: status = %d, want 200", path, rec.Code)
		}
	}
}

// TestDeclarationOrderPriority is the reason this router exists. Starlette
// picks the FIRST matching pattern; Go's ServeMux cannot express that for
// cross-matching pairs and panics instead.
func TestDeclarationOrderPriority(t *testing.T) {
	m := New()
	// These two cross-match: /api/news/analyze/restore matches BOTH.
	m.HandleFunc("POST", "/api/news/{article_id}/restore", recordingHandler("restore"))
	m.HandleFunc("POST", "/api/news/analyze/{article_id}", recordingHandler("analyze"))

	rec := httptest.NewRecorder()
	m.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/news/analyze/restore", nil))
	if got := rec.Body.String(); got != "restore" {
		t.Fatalf("cross-matching path: winner = %q, want %q (declaration order)",
			got, "restore")
	}

	// The second pattern still wins its own unique paths.
	rec = httptest.NewRecorder()
	m.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/news/analyze/42", nil))
	if got := rec.Body.String(); got != "analyze" {
		t.Fatalf("unique path: winner = %q, want %q", got, "analyze")
	}
}

// TestMethodNotAllowed is FastAPI's automatic 405: the path is known, the
// verb is not.
func TestMethodNotAllowed(t *testing.T) {
	m := New()
	m.HandleFunc("GET", "/api/v1/system/health", recordingHandler("health"))

	rec := httptest.NewRecorder()
	m.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/v1/system/health", nil))
	if rec.Code != http.StatusMethodNotAllowed {
		t.Fatalf("wrong verb on known path: status = %d, want 405", rec.Code)
	}
}

// TestUnknownPathIsNotFound guards the other half of the 404/405 split.
func TestUnknownPathIsNotFound(t *testing.T) {
	m := New()
	m.HandleFunc("GET", "/api/v1/system/health", recordingHandler("health"))

	rec := httptest.NewRecorder()
	m.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/system/nope", nil))
	if rec.Code != http.StatusNotFound {
		t.Fatalf("unknown path: status = %d, want 404", rec.Code)
	}
}

// TestQuerystringIgnored: matching is on r.URL.Path only.
func TestQuerystringIgnored(t *testing.T) {
	m := New()
	m.HandleFunc("GET", "/api/v1/research/runs", recordingHandler("runs"))

	rec := httptest.NewRecorder()
	m.ServeHTTP(rec, httptest.NewRequest(http.MethodGet,
		"/api/v1/research/runs?page=2&page_size=50", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
}

// TestRegexEscaping: literal segments must not be interpreted as regex.
func TestRegexEscaping(t *testing.T) {
	m := New()
	m.HandleFunc("GET", "/api/v1/foo.bar+baz", recordingHandler("literal"))

	rec := httptest.NewRecorder()
	m.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/foo.bar+baz", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("literal-with-metachars: status = %d, want 200", rec.Code)
	}
}
