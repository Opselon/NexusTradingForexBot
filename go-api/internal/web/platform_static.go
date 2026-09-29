// Package web serves the built React Control Center bundle and platform static
// assets/documentation from the Go API origin.
//
// PlatformStatic provides direct Go serving for:
//   - /openapi.json (served from docs/api/openapi.json, memory cache, or fallback to Python)
//   - /docs (Swagger UI HTML embedding /openapi.json)
//   - /docs/oauth2-redirect (Swagger UI OAuth2 redirect handler)
//   - /redoc (ReDoc HTML embedding /openapi.json)
//   - Platform static assets (src/nexus_scalp/web/static/* or fallback to Python)
package web

import (
	"context"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/respond"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
)

// PythonCaller represents the subset of the Python client required to proxy
// requests to the Python runtime. *python.Client implements this interface.
type PythonCaller interface {
	DoRaw(ctx context.Context, method, path string, body io.Reader) ([]byte, error)
}

// SwaggerUIHTML is the embedded Swagger UI template pointing to /openapi.json.
const SwaggerUIHTML = `<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link type="text/css" rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
<link rel="shortcut icon" href="https://fastapi.tiangolo.com/img/favicon.png">
<title>Nexus Scalp Engine Control Center - Swagger UI</title>
</head>
<body>
<div id="swagger-ui">
</div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<!-- ` + "`" + `SwaggerUIBundle` + "`" + ` is now available on the page -->
<script>
const ui = SwaggerUIBundle({
    url: '/openapi.json',
    "dom_id": "#swagger-ui",
    "layout": "BaseLayout",
    "deepLinking": true,
    "showExtensions": true,
    "showCommonExtensions": true,
    presets: [
        SwaggerUIBundle.presets.apis,
        SwaggerUIBundle.SwaggerUIStandalonePreset
    ],
})
</script>
</body>
</html>`

// ReDocHTML is the embedded ReDoc template pointing to /openapi.json.
const ReDocHTML = `<!DOCTYPE html>
<html>
<head>
<title>Nexus Scalp Engine Control Center - ReDoc</title>
<!-- needed for adaptive design -->
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
<link rel="shortcut icon" href="https://fastapi.tiangolo.com/img/favicon.png">
<style>
  body {
    margin: 0;
    padding: 0;
  }
</style>
</head>
<body>
<noscript>
    ReDoc requires Javascript to function. Please enable it to browse the documentation.
</noscript>
<redoc spec-url="/openapi.json"></redoc>
<script src="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js"> </script>
</body>
</html>`

// OAuth2RedirectHTML is the Swagger UI OAuth2 redirect handler page.
const OAuth2RedirectHTML = `<!doctype html>
<html lang="en-US">
<head><title>Swagger UI: OAuth2 Redirect</title></head>
<body>
<script>
'use strict';
function run() {
    var oauth2 = window.opener.swaggerUIRedirectOauth2, sentState = oauth2.state, redirectUrl = oauth2.redirectUrl;
    var qp = (window.location.hash || window.location.search || '').substring(1).split('&').reduce(function(acc, part) {
        var kv = part.split('='); if (kv[0]) acc[kv[0]] = decodeURIComponent(kv[1] || ''); return acc;
    }, {});
    if (qp.state === sentState) {
        if (qp.code) { delete oauth2.state; oauth2.auth.code = qp.code; oauth2.callback({auth: oauth2.auth, redirectUrl: redirectUrl}); }
        else { oauth2.callback({auth: oauth2.auth, token: qp, isValid: true, redirectUrl: redirectUrl}); }
    }
    window.close();
}
window.addEventListener('DOMContentLoaded', run);
</script>
</body>
</html>`

// PlatformStatic handles OpenAPI documentation and platform static assets.
type PlatformStatic struct {
	py              PythonCaller
	staticDir       string
	openAPIPath     string
	cachedOpenAPIMu sync.RWMutex
	cachedOpenAPI   []byte
}

// PlatformStaticOption configures a PlatformStatic instance.
type PlatformStaticOption func(*PlatformStatic)

// WithStaticDir sets the primary directory for static platform assets.
func WithStaticDir(dir string) PlatformStaticOption {
	return func(p *PlatformStatic) { p.staticDir = dir }
}

// WithOpenAPIPath sets an explicit path to the openapi.json file on disk.
func WithOpenAPIPath(path string) PlatformStaticOption {
	return func(p *PlatformStatic) { p.openAPIPath = path }
}

// WithCachedOpenAPI pre-populates the in-memory OpenAPI cache.
func WithCachedOpenAPI(data []byte) PlatformStaticOption {
	return func(p *PlatformStatic) { p.cachedOpenAPI = data }
}

// NewPlatformStatic constructs a PlatformStatic handler.
func NewPlatformStatic(py PythonCaller, opts ...PlatformStaticOption) *PlatformStatic {
	ps := &PlatformStatic{
		py: py,
	}
	for _, opt := range opts {
		opt(ps)
	}
	return ps
}

// SetCachedOpenAPI sets the cached OpenAPI JSON data.
func (p *PlatformStatic) SetCachedOpenAPI(data []byte) {
	p.cachedOpenAPIMu.Lock()
	defer p.cachedOpenAPIMu.Unlock()
	p.cachedOpenAPI = data
}

// GetCachedOpenAPI returns the currently cached OpenAPI JSON data.
func (p *PlatformStatic) GetCachedOpenAPI() []byte {
	p.cachedOpenAPIMu.RLock()
	defer p.cachedOpenAPIMu.RUnlock()
	return p.cachedOpenAPI
}

// platformContentType resolves content types for platform static files, ensuring
// .js returns application/javascript, .css returns text/css; charset=utf-8,
// and .html returns text/html; charset=utf-8.
func platformContentType(filePath string) string {
	ext := strings.ToLower(filepath.Ext(filePath))
	switch ext {
	case ".html", ".htm":
		return "text/html; charset=utf-8"
	case ".js", ".mjs":
		return "application/javascript"
	case ".css":
		return "text/css; charset=utf-8"
	default:
		return contentTypeByExtension(filePath)
	}
}

// resolveOpenAPIFile checks candidate locations for openapi.json on disk.
func (p *PlatformStatic) resolveOpenAPIFile() string {
	if p.openAPIPath != "" {
		if info, err := os.Stat(p.openAPIPath); err == nil && !info.IsDir() {
			return p.openAPIPath
		}
	}
	if env := strings.TrimSpace(os.Getenv("NEXUS_OPENAPI_PATH")); env != "" {
		if info, err := os.Stat(env); err == nil && !info.IsDir() {
			return env
		}
	}
	var candidates []string
	if root := repoRoot(); root != "" {
		candidates = append(candidates, filepath.Join(root, "docs", "api", "openapi.json"))
	}
	candidates = append(candidates,
		filepath.Join("docs", "api", "openapi.json"),
		filepath.Join("..", "docs", "api", "openapi.json"),
		filepath.Join("..", "..", "docs", "api", "openapi.json"),
	)
	for _, cand := range candidates {
		if info, err := os.Stat(cand); err == nil && !info.IsDir() {
			return cand
		}
	}
	return ""
}

// resolveStaticDirs returns candidate directories containing static assets,
// prioritizing the configured staticDir or src/nexus_scalp/web/static.
func (p *PlatformStatic) resolveStaticDirs() []string {
	var candidates []string
	if p.staticDir != "" {
		candidates = append(candidates, p.staticDir)
	}
	if env := strings.TrimSpace(os.Getenv("NEXUS_STATIC_DIR")); env != "" {
		candidates = append(candidates, env)
	}
	if root := repoRoot(); root != "" {
		candidates = append(candidates, filepath.Join(root, "src", "nexus_scalp", "web", "static"))
	}
	candidates = append(candidates, filepath.Join("src", "nexus_scalp", "web", "static"))
	if root := repoRoot(); root != "" {
		candidates = append(candidates, filepath.Join(root, "Web"))
	}
	candidates = append(candidates, "Web")

	seen := make(map[string]bool)
	var valid []string
	for _, cand := range candidates {
		if cand == "" {
			continue
		}
		clean := filepath.Clean(cand)
		if seen[clean] {
			continue
		}
		seen[clean] = true
		if info, err := os.Stat(clean); err == nil && info.IsDir() {
			valid = append(valid, clean)
		}
	}
	return valid
}

// ServeOpenAPI serves /openapi.json from disk, cache, or fallback to Python.
func (p *PlatformStatic) ServeOpenAPI(w http.ResponseWriter, r *http.Request) {
	refresh := r.URL.Query().Get("refresh") == "true" || r.URL.Query().Get("force") == "true"

	if !refresh {
		// 1. Check disk file docs/api/openapi.json
		if filePath := p.resolveOpenAPIFile(); filePath != "" {
			if data, err := os.ReadFile(filePath); err == nil && len(data) > 0 {
				p.cachedOpenAPIMu.Lock()
				p.cachedOpenAPI = data
				p.cachedOpenAPIMu.Unlock()

				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("Cache-Control", "public, max-age=3600")
				w.WriteHeader(http.StatusOK)
				_, _ = w.Write(data)
				return
			}
		}

		// 2. Check in-memory cache
		p.cachedOpenAPIMu.RLock()
		cached := p.cachedOpenAPI
		p.cachedOpenAPIMu.RUnlock()
		if len(cached) > 0 {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Cache-Control", "public, max-age=3600")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write(cached)
			return
		}
	}

	// 3. Fallback to Python runtime
	if p.py != nil {
		raw, err := p.py.DoRaw(r.Context(), http.MethodGet, "/openapi.json", nil)
		if err == nil && len(raw) > 0 {
			p.cachedOpenAPIMu.Lock()
			p.cachedOpenAPI = raw
			p.cachedOpenAPIMu.Unlock()

			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Cache-Control", "public, max-age=3600")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write(raw)
			return
		}
		if err != nil {
			p.handlePythonError(w, r, err)
			return
		}
	}

	respond.LegacyHTTPError(w, http.StatusNotFound, "Not Found")
}

// ServeDocs serves the Swagger UI HTML page at /docs.
func (p *PlatformStatic) ServeDocs(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(SwaggerUIHTML))
}

// ServeOAuth2Redirect serves the Swagger UI OAuth2 redirect handler at /docs/oauth2-redirect.
func (p *PlatformStatic) ServeOAuth2Redirect(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(OAuth2RedirectHTML))
}

// ServeReDoc serves the ReDoc HTML page at /redoc.
func (p *PlatformStatic) ServeReDoc(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(ReDocHTML))
}

// ServeStatic serves static files directly from local storage with proper
// Content-Type and Cache-Control headers, or falls back to Python py.DoRaw.
func (p *PlatformStatic) ServeStatic(w http.ResponseWriter, r *http.Request) {
	urlPath := r.URL.Path

	// Reject directory traversal attempts immediately
	if escapeAttempt(urlPath) {
		p.fallback(w, r)
		return
	}

	// Prepare candidate relative subpaths
	var relCandidates []string
	if strings.HasPrefix(urlPath, "/static/") {
		relCandidates = append(relCandidates, strings.TrimPrefix(urlPath, "/static/"))
	}
	relCandidates = append(relCandidates, strings.TrimPrefix(urlPath, "/"))

	// Check each configured static directory
	for _, dir := range p.resolveStaticDirs() {
		for _, rel := range relCandidates {
			target := filepath.Join(dir, filepath.FromSlash(rel))
			cleanTarget := filepath.Clean(target)
			cleanDir := filepath.Clean(dir)
			if !strings.HasPrefix(cleanTarget, cleanDir) {
				continue
			}

			info, err := os.Stat(cleanTarget)
			if err == nil && !info.IsDir() {
				data, rerr := os.ReadFile(cleanTarget)
				if rerr == nil {
					ct := platformContentType(cleanTarget)
					if ct != "" {
						w.Header().Set("Content-Type", ct)
					}
					if strings.HasSuffix(cleanTarget, ".html") || strings.HasSuffix(cleanTarget, ".htm") {
						w.Header().Set("Cache-Control", "no-cache")
					} else if immutableAssetRe.MatchString(filepath.Base(cleanTarget)) {
						w.Header().Set("Cache-Control", immutableCacheControl)
					} else {
						w.Header().Set("Cache-Control", "public, max-age=86400")
					}
					w.WriteHeader(http.StatusOK)
					_, _ = w.Write(data)
					return
				}
			}
		}
	}

	// File not found or could not be read -> fallback to Python
	p.fallback(w, r)
}

// fallback proxies the request to the Python runtime via py.DoRaw.
func (p *PlatformStatic) fallback(w http.ResponseWriter, r *http.Request) {
	if p.py == nil {
		if respond.IsV1Path(r.URL.Path) {
			respond.NotFound(w, r)
		} else {
			respond.LegacyHTTPError(w, http.StatusNotFound, "Not Found")
		}
		return
	}

	full := r.URL.Path
	if r.URL.RawQuery != "" {
		full += "?" + r.URL.RawQuery
	}

	raw, err := p.py.DoRaw(r.Context(), r.Method, full, r.Body)
	if err != nil {
		p.handlePythonError(w, r, err)
		return
	}

	if len(raw) == 0 {
		w.WriteHeader(http.StatusNoContent)
		return
	}

	ct := platformContentType(r.URL.Path)
	if ct != "" {
		w.Header().Set("Content-Type", ct)
	}
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(raw)
}

// handlePythonError formats upstream errors consistently.
func (p *PlatformStatic) handlePythonError(w http.ResponseWriter, r *http.Request, err error) {
	if be, ok := python.AsBoundary(err); ok {
		respond.ReplayBoundary(w, r, be)
		return
	}
	if lr, ok := python.AsLegacy(err); ok {
		ct := platformContentType(r.URL.Path)
		if ct != "" {
			w.Header().Set("Content-Type", ct)
		} else {
			w.Header().Set("Content-Type", "application/json")
		}
		w.WriteHeader(lr.Status)
		_, _ = w.Write(lr.Body)
		return
	}
	respond.FailDependencyUnavailable(w, r, "upstream unavailable")
}

// ServeHTTP dispatches requests for OpenAPI, Swagger UI, ReDoc, and static assets.
func (p *PlatformStatic) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/openapi.json":
		p.ServeOpenAPI(w, r)
	case "/docs", "/docs/":
		p.ServeDocs(w, r)
	case "/docs/oauth2-redirect":
		p.ServeOAuth2Redirect(w, r)
	case "/redoc", "/redoc/":
		p.ServeReDoc(w, r)
	default:
		p.ServeStatic(w, r)
	}
}

// Owns reports whether the given URL path is directly owned by PlatformStatic.
func (p *PlatformStatic) Owns(urlPath string) bool {
	switch urlPath {
	case "/openapi.json", "/docs", "/docs/", "/docs/oauth2-redirect", "/redoc", "/redoc/":
		return true
	}
	if strings.HasPrefix(urlPath, "/static/") {
		return true
	}
	return p.hasLocalFile(urlPath)
}

func (p *PlatformStatic) hasLocalFile(urlPath string) bool {
	if escapeAttempt(urlPath) {
		return false
	}
	relCandidates := []string{strings.TrimPrefix(urlPath, "/")}
	for _, dir := range p.resolveStaticDirs() {
		for _, rel := range relCandidates {
			cleanTarget := filepath.Clean(filepath.Join(dir, filepath.FromSlash(rel)))
			if !strings.HasPrefix(cleanTarget, filepath.Clean(dir)) {
				continue
			}
			if info, err := os.Stat(cleanTarget); err == nil && !info.IsDir() {
				return true
			}
		}
	}
	return false
}

// Middleware installs PlatformStatic in an HTTP middleware chain. If PlatformStatic
// owns the route or serves a local asset, it handles it; otherwise it delegates to next.
func (p *PlatformStatic) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if p.Owns(r.URL.Path) {
			p.ServeHTTP(w, r)
			return
		}
		if next != nil {
			next.ServeHTTP(w, r)
			return
		}
		p.ServeHTTP(w, r)
	})
}
