package web

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
)

// stubCaller implements PythonCaller for testing without a network.
type stubCaller struct {
	mu       sync.Mutex
	calls    []string
	response []byte
	err      error
}

func (s *stubCaller) DoRaw(_ context.Context, method, path string, _ io.Reader) ([]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls = append(s.calls, method+" "+path)
	return s.response, s.err
}

func (s *stubCaller) getCalls() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	copied := make([]string, len(s.calls))
	copy(copied, s.calls)
	return copied
}

func (s *stubCaller) callCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.calls)
}

// TestDocsReturnsHTML200 proves that /docs returns 200 HTML embedding /openapi.json.
func TestDocsReturnsHTML200(t *testing.T) {
	ps := NewPlatformStatic(nil)

	for _, target := range []string{"/docs", "/docs/"} {
		req := httptest.NewRequest(http.MethodGet, target, nil)
		rec := httptest.NewRecorder()
		ps.ServeHTTP(rec, req)

		if rec.Code != http.StatusOK {
			t.Fatalf("GET %s status = %d, want 200", target, rec.Code)
		}
		ct := rec.Header().Get("Content-Type")
		if !strings.Contains(ct, "text/html") {
			t.Errorf("GET %s Content-Type = %q, want text/html", target, ct)
		}
		body := rec.Body.String()
		if !strings.Contains(body, "SwaggerUIBundle") {
			t.Errorf("GET %s body missing SwaggerUIBundle", target)
		}
		if !strings.Contains(body, "/openapi.json") {
			t.Errorf("GET %s body missing /openapi.json reference", target)
		}
	}
}

// TestOAuth2RedirectReturnsHTML200 proves that /docs/oauth2-redirect returns 200 HTML.
func TestOAuth2RedirectReturnsHTML200(t *testing.T) {
	ps := NewPlatformStatic(nil)

	req := httptest.NewRequest(http.MethodGet, "/docs/oauth2-redirect", nil)
	rec := httptest.NewRecorder()
	ps.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /docs/oauth2-redirect status = %d, want 200", rec.Code)
	}
	ct := rec.Header().Get("Content-Type")
	if !strings.Contains(ct, "text/html") {
		t.Errorf("GET /docs/oauth2-redirect Content-Type = %q, want text/html", ct)
	}
	body := rec.Body.String()
	if !strings.Contains(body, "OAuth2 Redirect") {
		t.Errorf("GET /docs/oauth2-redirect body missing OAuth2 Redirect")
	}
}

// TestReDocReturnsHTML200 proves that /redoc returns 200 HTML embedding /openapi.json.
func TestReDocReturnsHTML200(t *testing.T) {
	ps := NewPlatformStatic(nil)

	for _, target := range []string{"/redoc", "/redoc/"} {
		req := httptest.NewRequest(http.MethodGet, target, nil)
		rec := httptest.NewRecorder()
		ps.ServeHTTP(rec, req)

		if rec.Code != http.StatusOK {
			t.Fatalf("GET %s status = %d, want 200", target, rec.Code)
		}
		ct := rec.Header().Get("Content-Type")
		if !strings.Contains(ct, "text/html") {
			t.Errorf("GET %s Content-Type = %q, want text/html", target, ct)
		}
		body := rec.Body.String()
		if !strings.Contains(body, "spec-url=\"/openapi.json\"") {
			t.Errorf("GET %s body missing spec-url=\"/openapi.json\"", target)
		}
		if !strings.Contains(body, "<redoc") {
			t.Errorf("GET %s body missing <redoc tag", target)
		}
	}
}

// TestStaticCSSJSServingWithProperMIMEType proves direct serving of CSS, JS, HTML,
// and hashed immutable assets with proper MIME types and Cache-Control headers.
func TestStaticCSSJSServingWithProperMIMEType(t *testing.T) {
	tempDir := t.TempDir()

	cssContent := "body { background: #0b0f19; color: #fff; }\n"
	jsContent := "console.log('nexus platform static');\n"
	htmlContent := "<!doctype html><html><body><h1>Platform</h1></body></html>\n"
	immutableContent := "export const version = '1.0.0';\n"

	files := map[string]string{
		"theme.css":            cssContent,
		"platform.js":          jsContent,
		"view.html":            htmlContent,
		"vendor/theme.css":     cssContent,
		"bundle-a1b2c3d4e5.js": immutableContent,
	}

	for rel, content := range files {
		full := filepath.Join(tempDir, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			t.Fatalf("mkdir failed for %s: %v", rel, err)
		}
		if err := os.WriteFile(full, []byte(content), 0o644); err != nil {
			t.Fatalf("write failed for %s: %v", rel, err)
		}
	}

	ps := NewPlatformStatic(nil, WithStaticDir(tempDir))

	cases := []struct {
		target       string
		wantStatus   int
		wantType     string
		wantCache    string
		wantBodyPart string
	}{
		{
			target:       "/static/theme.css",
			wantStatus:   http.StatusOK,
			wantType:     "text/css; charset=utf-8",
			wantCache:    "public, max-age=86400",
			wantBodyPart: "background: #0b0f19",
		},
		{
			target:       "/static/vendor/theme.css",
			wantStatus:   http.StatusOK,
			wantType:     "text/css; charset=utf-8",
			wantCache:    "public, max-age=86400",
			wantBodyPart: "background: #0b0f19",
		},
		{
			target:       "/static/platform.js",
			wantStatus:   http.StatusOK,
			wantType:     "application/javascript",
			wantCache:    "public, max-age=86400",
			wantBodyPart: "nexus platform static",
		},
		{
			target:       "/static/view.html",
			wantStatus:   http.StatusOK,
			wantType:     "text/html; charset=utf-8",
			wantCache:    "no-cache",
			wantBodyPart: "<h1>Platform</h1>",
		},
		{
			target:       "/static/bundle-a1b2c3d4e5.js",
			wantStatus:   http.StatusOK,
			wantType:     "application/javascript",
			wantCache:    "public, max-age=31536000, immutable",
			wantBodyPart: "export const version",
		},
	}

	for _, c := range cases {
		req := httptest.NewRequest(http.MethodGet, c.target, nil)
		rec := httptest.NewRecorder()
		ps.ServeHTTP(rec, req)

		if rec.Code != c.wantStatus {
			t.Errorf("GET %s status = %d, want %d", c.target, rec.Code, c.wantStatus)
		}
		if got := rec.Header().Get("Content-Type"); got != c.wantType {
			t.Errorf("GET %s Content-Type = %q, want %q", c.target, got, c.wantType)
		}
		if got := rec.Header().Get("Cache-Control"); got != c.wantCache {
			t.Errorf("GET %s Cache-Control = %q, want %q", c.target, got, c.wantCache)
		}
		if !strings.Contains(rec.Body.String(), c.wantBodyPart) {
			t.Errorf("GET %s body = %q, want substring %q", c.target, rec.Body.String(), c.wantBodyPart)
		}
	}
}

// TestFallbackBehaviorWhenFileNotFound proves that when a static file does not
// exist on disk, PlatformStatic seamlessly falls back to proxying to Python py.DoRaw.
func TestFallbackBehaviorWhenFileNotFound(t *testing.T) {
	proxiedCSS := "/* proxied css from python */\n.python-rule { display: block; }"
	stub := &stubCaller{
		response: []byte(proxiedCSS),
		err:      nil,
	}

	emptyDir := t.TempDir()
	ps := NewPlatformStatic(stub, WithStaticDir(emptyDir))

	req := httptest.NewRequest(http.MethodGet, "/static/dynamic-generated.css", nil)
	rec := httptest.NewRecorder()
	ps.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /static/dynamic-generated.css status = %d, want 200", rec.Code)
	}
	if got := rec.Body.String(); got != proxiedCSS {
		t.Errorf("GET /static/dynamic-generated.css body = %q, want %q", got, proxiedCSS)
	}
	if got := rec.Header().Get("Content-Type"); got != "text/css; charset=utf-8" {
		t.Errorf("GET /static/dynamic-generated.css Content-Type = %q, want text/css; charset=utf-8", got)
	}

	calls := stub.getCalls()
	if len(calls) != 1 || calls[0] != "GET /static/dynamic-generated.css" {
		t.Errorf("expected 1 call to python for GET /static/dynamic-generated.css, got: %v", calls)
	}
}

// TestFallbackBehaviorWhenPythonReturnsNotFound proves that when Python returns
// a 404 LegacyResponse, it is replayed verbatim to the client.
func TestFallbackBehaviorWhenPythonReturnsNotFound(t *testing.T) {
	legacy404 := []byte(`{"detail":"Not Found"}`)
	stub := &stubCaller{
		err: &python.LegacyResponse{
			Status: http.StatusNotFound,
			Body:   legacy404,
		},
	}

	emptyDir := t.TempDir()
	ps := NewPlatformStatic(stub, WithStaticDir(emptyDir))

	req := httptest.NewRequest(http.MethodGet, "/static/nonexistent.js", nil)
	rec := httptest.NewRecorder()
	ps.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("GET /static/nonexistent.js status = %d, want 404", rec.Code)
	}
	if got := rec.Body.String(); got != string(legacy404) {
		t.Errorf("GET /static/nonexistent.js body = %q, want %q", got, string(legacy404))
	}
}

// TestOpenAPIServingFromFile proves /openapi.json is served directly from disk
// when docs/api/openapi.json exists.
func TestOpenAPIServingFromFile(t *testing.T) {
	tempDir := t.TempDir()
	openAPIFile := filepath.Join(tempDir, "openapi.json")
	schema := `{"openapi":"3.0.0","info":{"title":"NSE","version":"1.0.0"}}`
	if err := os.WriteFile(openAPIFile, []byte(schema), 0o644); err != nil {
		t.Fatalf("write openapi.json: %v", err)
	}

	stub := &stubCaller{}
	ps := NewPlatformStatic(stub, WithOpenAPIPath(openAPIFile))

	req := httptest.NewRequest(http.MethodGet, "/openapi.json", nil)
	rec := httptest.NewRecorder()
	ps.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /openapi.json status = %d, want 200", rec.Code)
	}
	if got := rec.Header().Get("Content-Type"); got != "application/json" {
		t.Errorf("GET /openapi.json Content-Type = %q, want application/json", got)
	}
	if got := rec.Body.String(); got != schema {
		t.Errorf("GET /openapi.json body = %q, want %q", got, schema)
	}
	if stub.callCount() != 0 {
		t.Errorf("python was called %d times; file should have been served without upstream call", stub.callCount())
	}
}

// TestOpenAPIServingFromCache proves /openapi.json is served from the in-memory cache
// when no file is present on disk.
func TestOpenAPIServingFromCache(t *testing.T) {
	cachedSchema := `{"openapi":"3.0.0","info":{"title":"Cached NSE"}}`
	stub := &stubCaller{}

	ps := NewPlatformStatic(stub,
		WithOpenAPIPath(filepath.Join(t.TempDir(), "nonexistent.json")),
		WithCachedOpenAPI([]byte(cachedSchema)),
	)

	req := httptest.NewRequest(http.MethodGet, "/openapi.json", nil)
	rec := httptest.NewRecorder()
	ps.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /openapi.json status = %d, want 200", rec.Code)
	}
	if got := rec.Header().Get("Content-Type"); got != "application/json" {
		t.Errorf("GET /openapi.json Content-Type = %q, want application/json", got)
	}
	if got := rec.Body.String(); got != cachedSchema {
		t.Errorf("GET /openapi.json body = %q, want %q", got, cachedSchema)
	}
	if stub.callCount() != 0 {
		t.Errorf("python was called %d times; cache should have answered without upstream call", stub.callCount())
	}
}

// TestOpenAPIFallbackToPython proves /openapi.json falls back to Python when neither
// file nor cache exists, and subsequent requests are cached.
func TestOpenAPIFallbackToPython(t *testing.T) {
	upstreamSchema := `{"openapi":"3.0.0","info":{"title":"From Upstream"}}`
	stub := &stubCaller{
		response: []byte(upstreamSchema),
	}

	ps := NewPlatformStatic(stub,
		WithOpenAPIPath(filepath.Join(t.TempDir(), "nonexistent.json")),
	)

	// First call: falls back to Python and populates cache
	req := httptest.NewRequest(http.MethodGet, "/openapi.json", nil)
	rec := httptest.NewRecorder()
	ps.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("GET /openapi.json status = %d, want 200", rec.Code)
	}
	if got := rec.Body.String(); got != upstreamSchema {
		t.Errorf("GET /openapi.json body = %q, want %q", got, upstreamSchema)
	}
	if stub.callCount() != 1 {
		t.Fatalf("expected 1 call to python, got %d", stub.callCount())
	}

	// Second call: served from memory cache, no new call to Python
	rec2 := httptest.NewRecorder()
	ps.ServeHTTP(rec2, req)
	if rec2.Code != http.StatusOK {
		t.Fatalf("GET /openapi.json second call status = %d, want 200", rec2.Code)
	}
	if stub.callCount() != 1 {
		t.Errorf("expected still 1 call to python (cached), got %d", stub.callCount())
	}

	// Third call with ?refresh=true: bypasses cache and queries Python again
	reqRefresh := httptest.NewRequest(http.MethodGet, "/openapi.json?refresh=true", nil)
	rec3 := httptest.NewRecorder()
	ps.ServeHTTP(rec3, reqRefresh)
	if rec3.Code != http.StatusOK {
		t.Fatalf("GET /openapi.json?refresh=true status = %d, want 200", rec3.Code)
	}
	if stub.callCount() != 2 {
		t.Errorf("expected 2 calls to python after refresh, got %d", stub.callCount())
	}
}

// TestTraversalRefusedFallback verifies directory traversal attempts are refused
// and safely routed to fallback.
func TestTraversalRefusedFallback(t *testing.T) {
	tempDir := t.TempDir()
	stub := &stubCaller{
		err: &python.LegacyResponse{Status: 404, Body: []byte(`{"detail":"Not Found"}`)},
	}
	ps := NewPlatformStatic(stub, WithStaticDir(tempDir))

	for _, target := range []string{
		"/static/../../etc/passwd",
		`/static/..\..\windows\win.ini`,
		"/static//etc/passwd",
	} {
		req := httptest.NewRequest(http.MethodGet, target, nil)
		rec := httptest.NewRecorder()
		ps.ServeHTTP(rec, req)

		if strings.Contains(rec.Body.String(), "root:") {
			t.Errorf("GET %s leaked file content", target)
		}
	}
}

// TestOwnsAndMiddleware verifies Owns detection and Middleware passthrough.
func TestOwnsAndMiddleware(t *testing.T) {
	ps := NewPlatformStatic(nil)

	if !ps.Owns("/docs") || !ps.Owns("/redoc") || !ps.Owns("/openapi.json") || !ps.Owns("/static/app.js") {
		t.Error("PlatformStatic should own /docs, /redoc, /openapi.json, and /static/*")
	}

	nextCalled := false
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		nextCalled = true
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("next-handler"))
	})

	handler := ps.Middleware(next)

	// Call an unowned route
	req := httptest.NewRequest(http.MethodGet, "/some/other/api", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if !nextCalled {
		t.Error("Middleware should have called next handler for unowned route")
	}
	if rec.Body.String() != "next-handler" {
		t.Errorf("body = %q, want 'next-handler'", rec.Body.String())
	}
}
