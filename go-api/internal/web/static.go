// Package web serves the built React Control Center bundle from the Go API
// origin so Go can be the SINGLE origin for both the API and the UI
// (END-USER-RUNTIME-UI-INTEGRATION, frozen decisions #4/#6/#9).
//
// This is a faithful PORT of the Python seam src/nexus_scalp/web/
// frontend_assets.py (resolution order) and web/server.py (_AltSpaStaticFiles
// root mount semantics). There is deliberately ONE resolver here — the Go
// sibling of the frozen Python seam — so the two servers can never drift into
// serving two different bundles.
//
// MOUNT CONTRACT (mirrors Starlette first-match in server.py create_app):
//
//	the SPA handler is the LAST thing registered on the router, after every
//	/api route and the /api/v1 fallback. The router asks the API first; only
//	a path the whole API surface rejected reaches the static layer. This is
//	the Go equivalent of registering app.mount("/", root_spa) as the very
//	last route.
//
// DENY LIST (mirrors ROOT_SPA_DENY_PREFIXES + the Python public-path set):
//
//	/api, /ws, /web, /health, /healthz, /app.js, /api_client.js are owned by
//	the Python surface (proxied through the Go router) and are NEVER answered
//	with the SPA shell — an unmatched child of those prefixes is an honest
//	404, never index.html (contract #6, §60: a typo'd API call must never
//	receive HTML the client would parse as data).
package web

import (
	"errors"
	"io"
	"io/fs"
	"log/slog"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
)

// DistEnvVar is the frozen env override slot (contract #9, search slot 1).
// Mirrors NEXUS_ALT_UI_DIR in frontend_assets.py: AUTHORITATIVE — set but
// invalid (no index.html) means NO dist, never a silent fallthrough.
const DistEnvVar = "NEXUS_ALT_UI_DIR"

// denyPrefixes mirrors ROOT_SPA_DENY_PREFIXES in server.py (frozen #6, §60):
// path classes the root SPA mount answers with an honest 404, never the
// index document. /api, /ws and /web are Python-owned route trees.
var denyPrefixes = []string{"/api", "/ws", "/web"}

// denyPaths is the exact-path deny list: Python-owned documents and the
// cookie-bootstrap assets (BUG-267). The Go router registers all of these
// (they are in the generated route table or the handwritten surface), so in
// practice the API always wins by registration order; this list is the
// defense in depth that keeps the SPA layer from masking them if the route
// table ever lags the Python surface.
var denyPaths = map[string]bool{
	"/health":        true,
	"/healthz":       true,
	"/app.js":        true,
	"/api_client.js": true,
}

// maxCachedResolutions bounds the dist lookup cache: the resolver probes the
// filesystem and runs on the request path (like the Python seam, which is
// re-read per request), so a small memo is a strict win.
const maxCachedResolutions = 8

// resolutionCache memoizes ResolveFrontendDist. A negative lookup is cached
// too, so a bundle-less origin answers unknown paths without re-probing the
// disk on every request. The cache is keyed by the process-wide resolution
// (one dist per process per env state); test helpers can reset it.
type resolutionCache struct {
	mu      sync.Mutex
	entries map[string]string
	value   string
	set     bool
}

var distCache resolutionCache

func (c *resolutionCache) get() (string, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.value, c.set
}

func (c *resolutionCache) store(v string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.value = v
	c.set = true
	if c.entries == nil {
		c.entries = make(map[string]string, maxCachedResolutions)
	}
}

// resetCache clears the memo (test helper: the live process resolves once).
func resetCache() {
	distCache.mu.Lock()
	defer distCache.mu.Unlock()
	distCache.value = ""
	distCache.set = false
}

// hasIndex reports whether dir is a directory holding a servable index.html.
// Mirrors frontend_assets._has_index: OSError-guarded, never raises.
func hasIndex(dir string) bool {
	info, err := os.Stat(filepath.Join(dir, "index.html"))
	return err == nil && !info.IsDir()
}

// executableDir derives the running binary's directory at runtime, mirroring
// sys.executable.parent in frontend_assets.py. Never panics; "" when the OS
// will not say (the packaged candidates are then simply skipped).
func executableDir() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	if resolved, err := filepath.EvalSymlinks(exe); err == nil {
		exe = resolved
	}
	return filepath.Dir(exe)
}

// repoRoot derives the repository root from this package's own source path
// (go-api/internal/web/static.go -> the go-api parent is the repo root),
// mirroring _repo_root() in frontend_assets.py (parents[3] of the src layout).
// EvalSymlinks keeps the DEV path working under a git worktree, where the
// source path resolves through a linked tree.
func repoRoot() string {
	_, file, _, ok := runtime.Caller(0)
	if !ok || file == "" {
		return ""
	}
	if resolved, err := filepath.EvalSymlinks(file); err == nil {
		file = resolved
	}
	dir := filepath.Dir(file) // .../go-api/internal/web
	for i := 0; i < 3; i++ {
		parent := filepath.Dir(dir)
		if parent == dir { // reached the volume root
			return ""
		}
		dir = parent
	}
	return dir // .../go-api  (repo root in the standard layout)
}

// resolveFrontendDist resolves the built React dist directory (contract #9
// frozen order). It is a line-for-line port of
// frontend_assets.resolve_frontend_dist, with the two packaged candidates
// expressed against the Go BINARY's directory instead of sys.executable
// (the Python seam's onedir layout is <exe>/_internal/frontend/dist; a Go
// build ships the same data tree next to the nexus-api binary).
//
// Search order (FROZEN — never reorder, never add slots):
//  1. env NEXUS_ALT_UI_DIR          — authoritative; invalid -> no dist
//  2. <exe_dir>/_internal/Web       — packaged release (PyInstaller onedir)
//  3. <exe_dir>/Web                 — packaged portable release
//  4. <repo>/frontend/dist          — dev checkout (vite build output)
//  5. <repo>/Web                    — dev checkout legacy bundle
//
// Returns "" when no candidate is servable; callers treat that as "do not
// mount the SPA". Never raises: every probe is error-guarded (this runs on
// the request path, like the Python seam).
func resolveFrontendDist() string {
	override := strings.TrimSpace(os.Getenv(DistEnvVar))
	if override != "" {
		if hasIndex(override) {
			return override
		}
		// Contract #9: set-but-invalid is terminal — NEVER fall through.
		return ""
	}

	if exeDir := executableDir(); exeDir != "" {
		for _, cand := range []string{
			filepath.Join(exeDir, "_internal", "Web"),
			filepath.Join(exeDir, "Web"),
		} {
			if hasIndex(cand) {
				return cand
			}
		}
	}

	if root := repoRoot(); root != "" {
		for _, cand := range []string{
			filepath.Join(root, "frontend", "dist"),
			filepath.Join(root, "Web"),
		} {
			if hasIndex(cand) {
				return cand
			}
		}
	}
	return ""
}

// ResolveFrontendDist is the exported seam: the dist directory that actually
// contains index.html, or "" when no candidate is servable. The result is
// memoized per process so the hot request path never re-probes the disk.
// Mirrors the frozen seam other lanes import by that name.
func ResolveFrontendDist() string {
	if v, ok := distCache.get(); ok {
		return v
	}
	dir := resolveFrontendDist()
	if dir != "" {
		slog.Info("[spa] frontend dist resolved", "dir", dir)
	} else {
		slog.Info("[spa] no built frontend dist found — SPA fallback not " +
			"mounted (unknown paths keep the honest-404 behavior)")
	}
	distCache.store(dir)
	return dir
}

// SPAStaticFiles serves the resolved frontend dist as an SPA: real files are
// served verbatim, unknown DOTLESS paths fall back to index.html (client-side
// routing), and the deny list / traversal-shaped requests get an honest 404.
//
// It mirrors _AltSpaStaticFiles mounted at "/" with deny_prefixes set
// (ROOT_SPA_DENY_PREFIXES) — NOT the /alt instance, whose deny list is empty
// for byte-compat. The Go server serves the ROOT mount only; /alt stays a
// Python-owned route in the generated table.
type SPAStaticFiles struct {
	root    string
	fileSys http.FileSystem
	server  http.Handler
}

// NewSPAStaticFiles builds the SPA handler over the resolved dist directory.
// dist must contain index.html (ResolveFrontendDist guarantees this); an
// empty dist yields a handler that passes everything through to next.
func NewSPAStaticFiles(dist string) *SPAStaticFiles {
	if dist == "" || !hasIndex(dist) {
		return &SPAStaticFiles{root: ""}
	}
	return &SPAStaticFiles{
		root:    dist,
		fileSys: http.Dir(dist),
		server:  http.FileServer(http.Dir(dist)),
	}
}

// ServeSPA wraps next with the SPA static layer. next is the FULL API router
// (every /api route already registered): it is consulted FIRST, so the API
// always wins by dispatch order — the Go form of Starlette first-match plus
// the server.py deny list. When no dist resolves the layer is a no-op and
// next answers everything (zero-regression behavior, contract #4).
func ServeSPA(next http.Handler) http.Handler {
	spa := NewSPAStaticFiles(ResolveFrontendDist())
	if spa.root == "" {
		return next
	}
	return spa.Middleware(next)
}

// Middleware installs the SPA layer under next. Exported for tests that need
// to build the layer directly against a fixture directory. A nil next is
// treated as a 404-only terminator: the layer serves static files and the
// SPA fallback, and answers anything it does not own with a plain 404 (used
// when the router installs the layer as its not-found handler).
func (s *SPAStaticFiles) Middleware(next http.Handler) http.Handler {
	if s.root == "" {
		return next
	}
	terminal := next
	if terminal == nil {
		terminal = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusNotFound)
		})
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.serve(w, r, terminal)
	})
}

// Register mounts the SPA catch-all on mux as the LOWEST-priority route: it
// must be called AFTER every /api route is registered so the API always wins
// (mirrors app.mount("/", root_spa) being the last route in create_app).
func (s *SPAStaticFiles) Register(mux *http.ServeMux, next http.Handler) {
	mux.Handle("/", s.Middleware(next))
}

// denied reports the path class the SPA must never answer (contract #6/§60).
func denied(p string) bool {
	if denyPaths[p] {
		return true
	}
	for _, prefix := range denyPrefixes {
		if strings.HasPrefix(p, prefix) {
			return true
		}
	}
	return false
}

// escapeAttempt reports traversal/escape shapes in the ORIGINAL request path,
// mirroring _AltSpaStaticFiles._scope_escape_attempt: raw "..", backslashes,
// collapsed "//", and Windows drive/device paths are refused BEFORE any
// lookup (defense in depth alongside CodeQL #62/#63/#67).
func escapeAttempt(p string) bool {
	if p == "" {
		return false
	}
	if strings.Contains(p, "..") || strings.Contains(p, `\`) || strings.Contains(p, "//") {
		return true
	}
	head := strings.TrimLeft(p, "/")
	first := head
	if i := strings.Index(head, "/"); i >= 0 {
		first = head[:i]
	}
	return len(first) >= 2 && first[1] == ':' && isASCIILetter(first[0])
}

func isASCIILetter(b byte) bool {
	return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z')
}

// immutableAssetRe mirrors _HASHED_ASSET_RE (contract #8): a vite content
// hash in the filename is `-<hash>.<ext>` with hash length >= 8. Such assets
// are content-immutable under the same name, so the root mount caches them
// for a year.
var immutableAssetRe = regexp.MustCompile(`-[A-Za-z0-9_]{8,}\.[A-Za-z0-9]+$`)

// immutableCacheControl mirrors _IMMUTABLE_CACHE_CONTROL (contract #8).
const immutableCacheControl = "public, max-age=31536000, immutable"

// contentTypeByExtension mirrors Starlette's FileResponse: mimetypes.guess_type,
// with a charset appended for text/* types. Go's http.FileServer SNIFFS content
// instead (it would answer .js with application/javascript), so the extension
// type is set on the header first — serveContent keeps a pre-set Content-Type.
func contentTypeByExtension(p string) string {
	ext := strings.ToLower(filepath.Ext(p))
	switch ext {
	case ".html", ".htm":
		return "text/html; charset=utf-8"
	case ".js", ".mjs":
		return "text/javascript; charset=utf-8"
	case ".css":
		return "text/css; charset=utf-8"
	case ".json":
		return "application/json"
	case ".svg":
		return "image/svg+xml"
	case ".png":
		return "image/png"
	case ".ico":
		return "image/x-icon"
	case ".map":
		return "application/json"
	case ".txt":
		return "text/plain; charset=utf-8"
	case ".woff":
		return "font/woff"
	case ".woff2":
		return "font/woff2"
	default:
		return ""
	}
}

// isIndexDoc reports the CONTRACT #5 alias pair: /index.html is an ALIAS of /,
// never a separate document.
func isIndexDoc(p string) bool {
	return p == "/" || p == "/index.html"
}

// serveIndex writes the SPA index document with contract #8 headers (index
// documents are never cached — a stale shell pins outdated asset URLs after
// every rebuild). When index.html is somehow missing the request falls
// through to next (zero-regression behavior, contract #4).
func (s *SPAStaticFiles) serveIndex(w http.ResponseWriter, r *http.Request, next http.Handler) {
	index, err := s.fileSys.Open("/index.html")
	if err != nil {
		next.ServeHTTP(w, r)
		return
	}
	defer index.Close()
	stat, serr := index.Stat()
	if serr != nil || stat.IsDir() {
		next.ServeHTTP(w, r)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if _, err := io.Copy(w, index); err != nil {
		// The client went away; nothing to fix, but never panic on the
		// request path (the Python seam's invariants).
		var pathErr *fs.PathError
		if !errors.As(err, &pathErr) {
			slog.Debug("[spa] index document copy incomplete", "error", err)
		}
	}
}

// serve implements the _AltSpaStaticFiles.get_response contract for the root
// mount: deny first, escape check, real file, else SPA fallback.
func (s *SPAStaticFiles) serve(w http.ResponseWriter, r *http.Request, next http.Handler) {
	p := r.URL.Path

	// The deny list is Python-owned surface: pass it through untouched so the
	// API router's own 404/405 envelope applies (an unmatched /api child is
	// an honest 404, never a shell document — §60).
	if denied(p) {
		next.ServeHTTP(w, r)
		return
	}
	if escapeAttempt(p) {
		next.ServeHTTP(w, r)
		return
	}

	// The index document: served directly because http.FileServer answers
	// /index.html with a 301 to ./ (the Python contract serves the file).
	if isIndexDoc(p) {
		s.serveIndex(w, r, next)
		return
	}

	// Real file? Serve it verbatim with the extension content type set first
	// (Go's serveContent keeps a pre-set Content-Type header).
	if f, err := s.fileSys.Open(path.Clean("/" + p)); err == nil {
		stat, serr := f.Stat()
		_ = f.Close()
		if serr == nil && !stat.IsDir() {
			if ct := contentTypeByExtension(p); ct != "" {
				w.Header().Set("Content-Type", ct)
			}
			if immutableAssetRe.MatchString(p) {
				w.Header().Set("Cache-Control", immutableCacheControl)
			}
			s.server.ServeHTTP(w, r)
			return
		}
	}

	// SPA fallback: unknown dotless paths get the index document. A path with
	// an extension that is NOT .html is a missing ASSET — honest 404, never
	// a fake index (a typo'd asset must never be masked).
	name := p
	if i := strings.LastIndex(p, "/"); i >= 0 {
		name = p[i+1:]
	}
	if strings.Contains(name, ".") && !strings.EqualFold(filepath.Ext(name), ".html") {
		next.ServeHTTP(w, r)
		return
	}
	s.serveIndex(w, r, next)
}
