// Package web tests the SPA static layer against the contract frozen in
// server.py / frontend_assets.py: the API always wins, the deny list is never
// served a shell document, deep links fall back to index.html, and a missing
// asset stays an honest 404.
package web

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// marker is the canary written into the fixture index document; a test that
// sees it in a response body knows the SPA fallback fired.
const marker = "<title>NSE-SPA-FALLBACK-MARKER</title>"

// apiMarker is what the fake next handler writes, so a test can prove the
// request reached the API layer and was not swallowed by the static layer.
const apiMarker = `{"error":{"code":"NOT_FOUND"}}`

// stubAPI answers every request with the canonical v1 404 envelope — the
// shape the real router emits for an unmatched /api child. Tests assert on
// its presence to prove the static layer passed the request through.
func stubAPI() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(apiMarker))
	})
}

// writeFixture creates a tiny dist tree (index.html + app.js + an asset with
// a vite-style content hash) and returns its directory.
func writeFixture(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	files := map[string]string{
		"index.html": "<!doctype html><html>" + marker + "</html>",
		"app.js":     "console.log('app');\n",
		filepath.Join("assets", "index-abc12345.js"):  "export default 1;\n",
		filepath.Join("assets", "style-deadbeef.css"): "body{color:#000}\n",
	}
	for name, body := range files {
		full := filepath.Join(dir, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			t.Fatalf("mkdir %s: %v", filepath.Dir(full), err)
		}
		if err := os.WriteFile(full, []byte(body), 0o644); err != nil {
			t.Fatalf("write %s: %v", full, err)
		}
	}
	return dir
}

// layered builds the SPA handler over the stub API for one fixture dir.
func layered(t *testing.T, dist string) http.Handler {
	t.Helper()
	spa := NewSPAStaticFiles(dist)
	if spa.root == "" {
		t.Fatalf("NewSPAStaticFiles(%q) produced an inactive handler", dist)
	}
	return spa.Middleware(stubAPI())
}

func get(t *testing.T, h http.Handler, target string) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, target, nil))
	return rec
}

// TestSPAFallbackForDeepLink proves contract #6: an unknown NON-API path
// (a client-side route like /trading) receives the index document, not a 404.
func TestSPAFallbackForDeepLink(t *testing.T) {
	h := layered(t, writeFixture(t))

	for _, target := range []string{"/", "/trading", "/positions/open/abc", "/some/spa/route"} {
		rec := get(t, h, target)
		if rec.Code != http.StatusOK {
			t.Errorf("GET %s: status = %d, want 200", target, rec.Code)
			continue
		}
		if !strings.Contains(rec.Body.String(), marker) {
			t.Errorf("GET %s: body has no index marker (SPA fallback did not fire), got %q",
				target, rec.Body.String())
		}
		if cc := rec.Header().Get("Cache-Control"); cc != "no-store" {
			t.Errorf("GET %s: Cache-Control = %q, want no-store (contract #8)",
				target, cc)
		}
	}
}

// TestAPIPathNotIntercepted proves the deny list: /api/* and the Python-owned
// documents reach the API layer untouched. The static layer must never answer
// them with the SPA shell (§60: a typo'd API call can never receive HTML the
// client would parse as data).
func TestAPIPathNotIntercepted(t *testing.T) {
	h := layered(t, writeFixture(t))

	for _, target := range []string{
		"/api/v1/anything",
		"/api/v1/system/health",
		"/api/typos/typo",
		"/health",
		"/healthz",
		"/app.js",
		"/api_client.js",
	} {
		rec := get(t, h, target)
		if rec.Code != http.StatusNotFound {
			t.Errorf("GET %s: status = %d, want 404 (the API layer owns it)",
				target, rec.Code)
			continue
		}
		if got := rec.Body.String(); got != apiMarker {
			t.Errorf("GET %s: body = %q, want the API 404 envelope %q — the "+
				"static layer swallowed it", target, got, apiMarker)
		}
	}
}

// TestRealBundleFilesServed proves a real bundled file is served verbatim
// with the right Content-Type, and that a MISSING asset stays an honest 404
// (never a fake index document — a typo'd asset must never be masked).
func TestRealBundleFilesServed(t *testing.T) {
	h := layered(t, writeFixture(t))

	cases := []struct {
		target     string
		wantType   string
		wantBody   string
		wantStatus int
		wantCache  string
	}{
		{"/assets/index-abc12345.js", "text/javascript; charset=utf-8", "export default 1;\n", 200,
			"public, max-age=31536000, immutable"},
		{"/assets/style-deadbeef.css", "text/css; charset=utf-8", "body{color:#000}\n", 200,
			"public, max-age=31536000, immutable"},
		// CONTRACT #5: /index.html is an ALIAS of /, never a separate
		// document. The SPA layer serves the index document directly for
		// both, so both get the shell (not a FileServer 301).
		{"/index.html", "text/html; charset=utf-8", "<!doctype html>", 200, "no-store"},
		// /app.js is on the DENY list (contract #6): it is Python's
		// cookie-bootstrap asset (BUG-267), so the SPA layer passes it
		// through to the API stub — never serves it from the bundle.
		{"/app.js", "", apiMarker, 404, ""},
		// Missing assets: honest 404 from the API stub, never index.html.
		{"/assets/missing-zzz.js", "", apiMarker, 404, ""},
		{"/typo.js", "", apiMarker, 404, ""},
	}
	for _, c := range cases {
		rec := get(t, h, c.target)
		if rec.Code != c.wantStatus {
			t.Errorf("GET %s: status = %d, want %d", c.target, rec.Code, c.wantStatus)
			continue
		}
		if c.wantType != "" {
			if got := rec.Header().Get("Content-Type"); got != c.wantType {
				t.Errorf("GET %s: Content-Type = %q, want %q", c.target, got, c.wantType)
			}
		}
		if c.wantCache != "" {
			if got := rec.Header().Get("Cache-Control"); got != c.wantCache {
				t.Errorf("GET %s: Cache-Control = %q, want %q (contract #8)",
					c.target, got, c.wantCache)
			}
		}
		if !strings.Contains(rec.Body.String(), c.wantBody) {
			t.Errorf("GET %s: body = %q, want it to contain %q",
				c.target, rec.Body.String(), c.wantBody)
		}
	}
}

// TestDenyListPassesThrough is the explicit deny-list gate: every denied path
// reaches next, and none of them is answered with the index document.
func TestDenyListPassesThrough(t *testing.T) {
	dist := writeFixture(t)
	h := layered(t, dist)

	for _, target := range []string{"/api", "/api/v1/x", "/ws/ticks", "/web/foo", "/health"} {
		rec := get(t, h, target)
		if strings.Contains(rec.Body.String(), marker) {
			t.Errorf("GET %s: deny-list path was served the index document "+
				"(it must reach the API layer)", target)
		}
		if got := rec.Body.String(); got != apiMarker {
			t.Errorf("GET %s: body = %q, want %q", target, got, apiMarker)
		}
	}
}

// TestTraversalRefused proves escape-shaped requests are refused BEFORE any
// lookup (defense in depth alongside CodeQL #62/#63/#67, mirroring
// _AltSpaStaticFiles._scope_escape_attempt).
func TestTraversalRefused(t *testing.T) {
	h := layered(t, writeFixture(t))

	for _, target := range []string{
		"/../../etc/passwd",
		`/..\..\etc\passwd`,
		"/static//etc/passwd",
		"/C:/windows/win.ini",
	} {
		rec := get(t, h, target)
		if strings.Contains(rec.Body.String(), marker) {
			t.Errorf("GET %s: traversal-shaped request was served the index "+
				"document", target)
		}
	}
}

// TestResolutionOrder proves the frozen search order (contract #9) is a
// faithful port of frontend_assets.resolve_frontend_dist: the env override is
// authoritative, a set-but-INVALID override is terminal (never falls through
// to another candidate), and a valid override wins over the packaged layout.
func TestResolutionOrder(t *testing.T) {
	t.Setenv(DistEnvVar, "")
	resetCache()
	defer resetCache()

	// An empty env + a repo with no dist should resolve to "" here (the test
	// binary's own exe dir and source tree have no bundled Web/), OR to a real
	// dist if the worktree happens to ship one; assert the invariant either way.
	got := resolveFrontendDist()
	if got != "" && !hasIndex(got) {
		t.Fatalf("resolveFrontendDist() = %q, which has no index.html "+
			"(the seam may only return a servable dist)", got)
	}

	// A valid override is authoritative and wins.
	valid := writeFixture(t)
	t.Setenv(DistEnvVar, valid)
	resetCache()
	if got := ResolveFrontendDist(); got != valid {
		t.Errorf("override: ResolveFrontendDist() = %q, want %q", got, valid)
	}

	// A set-but-invalid override is TERMINAL — never a fallthrough.
	broken := t.TempDir()
	t.Setenv(DistEnvVar, broken)
	resetCache()
	if got := ResolveFrontendDist(); got != "" {
		t.Errorf("invalid override: ResolveFrontendDist() = %q, want \"\" "+
			"(set-but-invalid must mean NO dist, never a fallthrough)", got)
	}
}

// TestNoDistIsNoOp proves zero-regression behavior (contract #4): with no
// resolvable dist, the SPA layer passes every request through to next
// unchanged instead of inventing a shell.
func TestNoDistIsNoOp(t *testing.T) {
	t.Setenv(DistEnvVar, "/nonexistent/no-such-dist")
	resetCache()
	defer resetCache()

	spa := NewSPAStaticFiles(ResolveFrontendDist())
	if spa.root != "" {
		t.Fatalf("no dist resolved, but the SPA handler is active (root = %q)", spa.root)
	}
	h := spa.Middleware(stubAPI())

	for _, target := range []string{"/", "/trading", "/api/v1/x"} {
		rec := get(t, h, target)
		if rec.Code != http.StatusNotFound || rec.Body.String() != apiMarker {
			t.Errorf("GET %s with no dist: status = %d body = %q, want the "+
				"next handler's response unchanged", target, rec.Code, rec.Body.String())
		}
	}
}

// TestServeSPAWrapsNext proves the wiring helper composes the layers in the
// API-wins order: next is consulted first, the static layer only answers what
// the API surface rejected.
func TestServeSPAWrapsNext(t *testing.T) {
	t.Setenv(DistEnvVar, writeFixture(t))
	resetCache()
	defer resetCache()

	h := ServeSPA(stubAPI())

	// SPA route -> index document.
	rec := get(t, h, "/deep/spa/route")
	if !strings.Contains(rec.Body.String(), marker) {
		t.Errorf("ServeSPA: /deep/spa/route body = %q, want the index document",
			rec.Body.String())
	}
	// API route -> next handler, untouched.
	rec = get(t, h, "/api/v1/anything")
	if rec.Body.String() != apiMarker {
		t.Errorf("ServeSPA: /api/v1/anything body = %q, want %q",
			rec.Body.String(), apiMarker)
	}
}

// emptyRepo is the pinned repo slot for the resolver tests: this worktree
// ships a REAL frontend/dist, so the tests pass an empty directory as the
// repo root and only ever assert on fixtures they stage themselves.
func emptyRepo(t *testing.T) string {
	t.Helper()
	return t.TempDir()
}

// resolveSlots pins every filesystem slot to a fixture tree: exe dir, repo
// root and cwd. Only what the test stages exists, so the assertion measures
// exactly the candidate under test (never the real worktree dist).
func resolveSlots(exeDir, repo, cwd string) string {
	return resolveFrontendDistWith(exeDir, repo, cwd)
}

// makeDist writes a minimal dist tree (an index.html carrying BODY) inside
// dir, returning dir. A test can tell WHICH candidate the resolver picked by
// the title in the index document.
func makeDist(t *testing.T, dir, body string) string {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("mkdir %s: %v", dir, err)
	}
	full := filepath.Join(dir, "index.html")
	if err := os.WriteFile(full, []byte("<title>"+body+"</title>"), 0o644); err != nil {
		t.Fatalf("write %s: %v", full, err)
	}
	return dir
}

// TestResolveFrontendDistPackagedReactWinsLegacy is the PARITY BUG gate: a
// packaged tree shipping BOTH _internal/Web/index.html (the legacy HTML
// bundle) and _internal/frontend/dist/index.html (the React Control Center)
// must resolve to the REACT dist — exactly what
// frontend_assets.resolve_frontend_dist does (contract #9). Before the fix,
// Go checked _internal/Web and Web/ FIRST and served the legacy bundle while
// Python served the React console.
func TestResolveFrontendDistPackagedReactWinsLegacy(t *testing.T) {
	tree := t.TempDir()
	legacy := makeDist(t, filepath.Join(tree, "_internal", "Web"),
		"LEGACY-WEB-BUNDLE")
	react := makeDist(t, filepath.Join(tree, "_internal", "frontend", "dist"),
		"REACT-CONTROL-CENTER")
	if legacy == react {
		t.Fatalf("fixture collision: legacy == react")
	}

	t.Setenv(DistEnvVar, "")
	resetCache()
	defer resetCache()

	if got := resolveSlots(tree, emptyRepo(t), t.TempDir()); got != react {
		t.Errorf("packaged tree with both bundles: resolved %q, want the React "+
			"dist %q (parity with frontend_assets.resolve_frontend_dist; the "+
			"legacy Web/ bundle must never preempt it)", got, react)
	}
}

// TestResolveFrontendDistPackagedReactOnly covers the modern release: only
// the React console is staged. The resolver must find it.
func TestResolveFrontendDistPackagedReactOnly(t *testing.T) {
	tree := t.TempDir()
	react := makeDist(t, filepath.Join(tree, "_internal", "frontend", "dist"),
		"REACT-CONTROL-CENTER")

	t.Setenv(DistEnvVar, "")
	resetCache()
	defer resetCache()

	if got := resolveSlots(tree, emptyRepo(t), t.TempDir()); got != react {
		t.Errorf("react-only packaged tree: resolved %q, want %q", got, react)
	}
}

// TestResolveFrontendDistLegacyOnly covers an OLD release that shipped only
// the legacy HTML bundle: it still resolves (zero regression — nothing
// flips a release that never had a React dist).
func TestResolveFrontendDistLegacyOnly(t *testing.T) {
	tree := t.TempDir()
	legacy := makeDist(t, filepath.Join(tree, "_internal", "Web"),
		"LEGACY-WEB-BUNDLE")

	t.Setenv(DistEnvVar, "")
	resetCache()
	defer resetCache()

	if got := resolveSlots(tree, emptyRepo(t), t.TempDir()); got != legacy {
		t.Errorf("legacy-only packaged tree: resolved %q, want the legacy "+
			"bundle %q (zero regression for old releases)", got, legacy)
	}
}

// TestResolveFrontendDistPortableLegacy covers the non-_internal portable
// layout (<exe>/Web) and the repo-Web form: both resolve when they are all
// the tree has.
func TestResolveFrontendDistPortableLegacy(t *testing.T) {
	t.Setenv(DistEnvVar, "")
	resetCache()
	defer resetCache()

	tree := t.TempDir()
	portable := makeDist(t, filepath.Join(tree, "Web"), "LEGACY-PORTABLE")
	if got := resolveSlots(tree, emptyRepo(t), t.TempDir()); got != portable {
		t.Errorf("portable legacy tree: resolved %q, want %q", got, portable)
	}
}

// TestResolveFrontendDistExactFrozenOrder asserts the whole order in one
// sweep: every higher-ranked candidate wins over every lower-ranked one.
func TestResolveFrontendDistExactFrozenOrder(t *testing.T) {
	t.Setenv(DistEnvVar, "")
	resetCache()
	defer resetCache()

	exeTree := t.TempDir()
	reactPackaged := makeDist(t, filepath.Join(exeTree, "_internal", "frontend", "dist"),
		"REACT-PACKAGED")
	legacyInternal := makeDist(t, filepath.Join(exeTree, "_internal", "Web"),
		"LEGACY-INTERNAL")
	legacyPortable := makeDist(t, filepath.Join(exeTree, "Web"),
		"LEGACY-PORTABLE")
	cwdTree := t.TempDir()
	reactCwd := makeDist(t, filepath.Join(cwdTree, "frontend", "dist"),
		"REACT-CWD")

	// rank 1: env override beats every filesystem candidate.
	valid := writeFixture(t)
	t.Setenv(DistEnvVar, valid)
	resetCache()
	if got := resolveSlots(exeTree, emptyRepo(t), cwdTree); got != valid {
		t.Errorf("order: env override lost to %q (want %q)", got, valid)
	}

	// rank 2: packaged React dist beats the cwd dev dist AND both legacy
	// bundles (all four present at once).
	t.Setenv(DistEnvVar, "")
	resetCache()
	if got := resolveSlots(exeTree, emptyRepo(t), cwdTree); got != reactPackaged {
		t.Errorf("order: packaged react lost to %q (want %q)", got, reactPackaged)
	}

	// rank 3: with the packaged react gone, the cwd dev dist wins over both
	// legacy bundles (still present).
	if err := os.RemoveAll(reactPackaged); err != nil {
		t.Fatalf("remove %s: %v", reactPackaged, err)
	}
	resetCache()
	if got := resolveSlots(exeTree, emptyRepo(t), cwdTree); got != reactCwd {
		t.Errorf("order: cwd react lost to %q (want %q)", got, reactCwd)
	}

	// rank 5: with both react dists gone, the legacy _internal/Web bundle
	// wins over the portable Web/ bundle.
	if err := os.RemoveAll(reactCwd); err != nil {
		t.Fatalf("remove %s: %v", reactCwd, err)
	}
	resetCache()
	if got := resolveSlots(exeTree, emptyRepo(t), cwdTree); got != legacyInternal {
		t.Errorf("order: legacy _internal/Web lost to %q (want %q)",
			got, legacyInternal)
	}

	// rank 6: and without _internal/Web, the portable Web/ bundle resolves.
	if err := os.RemoveAll(legacyInternal); err != nil {
		t.Fatalf("remove %s: %v", legacyInternal, err)
	}
	resetCache()
	if got := resolveSlots(exeTree, emptyRepo(t), cwdTree); got != legacyPortable {
		t.Errorf("order: portable Web lost to %q (want %q)", got, legacyPortable)
	}
}

// TestResolveFrontendDistNoCandidates is the no-bundle-at-all case: the
// resolver returns "" and the SPA layer stays inactive (contract #4).
func TestResolveFrontendDistNoCandidates(t *testing.T) {
	t.Setenv(DistEnvVar, "")
	resetCache()
	defer resetCache()

	tree := t.TempDir()
	cwdTree := t.TempDir()
	if got := resolveSlots(tree, emptyRepo(t), cwdTree); got != "" {
		t.Errorf("empty tree: resolved %q, want %q (no dist -> no SPA mount)",
			got, "")
	}
}
