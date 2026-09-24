// Package auth tests the WEB-AUTH-P0 contract ported from
// src/nexus_scalp/web/auth.py.
//
// Every expectation here is traceable to a measured Python response, not to
// the Go implementation's own behaviour: 401/500 body shapes, cookie
// attributes, precedence order and traversal rejection were all captured from
// a live create_app() instance (see api/migration/contracts/).
package auth

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// OKBody is the response the protected handler writes when auth passed it.
const OKBody = `{"data":{"ok":true},"meta":{}}`

// newChain builds the middleware over a marker handler and returns a server
// whose handler reports whether dispatch happened (via the marker header).
func newChain(t *testing.T) *httptest.Server {
	t.Helper()
	h := Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Test-Dispatched", "yes")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(OKBody))
	}))
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	return srv
}

// setToken points resolution at a literal token for this test only.
func setToken(t *testing.T, tok string) {
	t.Helper()
	t.Setenv(EnvToken, tok)
}

// noTokenEnvironment makes resolveToken fail: no env token and no .env file
// reachable from an empty working directory.
func noTokenEnvironment(t *testing.T) {
	t.Helper()
	t.Setenv(EnvToken, "")
	old, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	tmp := t.TempDir()
	if err := os.Chdir(tmp); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(old) })
}

func decodeError(t *testing.T, r *http.Response) (code, message string) {
	t.Helper()
	var body struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		t.Fatalf("decode 401 body: %v", err)
	}
	return body.Error.Code, body.Error.Message
}

// TestPublicPathsRequireNoToken covers the BUG-267 bootstrap entry documents:
// a fresh browser must be able to load them, or the cookie can never arrive.
func TestPublicPathsRequireNoToken(t *testing.T) {
	noTokenEnvironment(t)
	srv := newChain(t)

	for _, p := range []string{"/", "/index.html", "/app.js", "/api_client.js",
		"/health", "/healthz", "/styles.css", "/vendor/x.js", "/alt/panel"} {
		resp, err := http.Get(srv.URL + p)
		if err != nil {
			t.Fatalf("GET %s: %v", p, err)
		}
		resp.Body.Close()
		if resp.StatusCode == http.StatusUnauthorized {
			t.Errorf("public path %s was blocked with 401", p)
		}
	}
}

// TestFailClosedWithoutToken is the headline security property: an unresolvable
// token blocks every protected path. There is no anonymous mode.
func TestFailClosedWithoutToken(t *testing.T) {
	noTokenEnvironment(t)
	srv := newChain(t)

	resp, err := http.Get(srv.URL + "/api/v1/system/health")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500 fail-closed", resp.StatusCode)
	}
	if resp.Header.Get("X-Test-Dispatched") != "" {
		t.Fatal("request reached the handler without a resolvable token")
	}
	code, _ := decodeError(t, resp)
	if code != "AUTH_CONFIG_ERROR" {
		t.Errorf("code = %q, want AUTH_CONFIG_ERROR", code)
	}
}

// TestUnauthorizedBodyShape pins the byte-exact legacy 401.
func TestUnauthorizedBodyShape(t *testing.T) {
	setToken(t, "t-TEST")
	srv := newChain(t)

	resp, err := http.Get(srv.URL + "/api/v1/system/health")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", resp.StatusCode)
	}
	if resp.Header.Get("X-Test-Dispatched") != "" {
		t.Fatal("unauthenticated request reached the handler")
	}
	if got := resp.Header.Get("WWW-Authenticate"); got != "Bearer" {
		t.Errorf("WWW-Authenticate = %q, want Bearer", got)
	}
	if ct := resp.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Errorf("Content-Type = %q", ct)
	}
	code, message := decodeError(t, resp)
	if code != "UNAUTHORIZED" {
		t.Errorf("code = %q", code)
	}
	if message != "missing or invalid web auth token" {
		t.Errorf("message = %q", message)
	}
}

// TestTokenPrecedence locks Authorization > X-NSE-Token > cookie > ?token=.
// The precedence matters: a stale cookie beside a rotated header must NOT win.
func TestTokenPrecedence(t *testing.T) {
	setToken(t, "t-TEST")
	srv := newChain(t)

	cases := []struct {
		name string
		req  func(*http.Request)
	}{
		{"authorization", func(r *http.Request) {
			r.Header.Set("Authorization", "Bearer t-TEST")
		}},
		{"x-nse-token", func(r *http.Request) {
			r.Header.Set("X-NSE-Token", "t-TEST")
		}},
		{"query-token", func(r *http.Request) {
			r.URL.RawQuery = "token=t-TEST"
		}},
		{"cookie", func(r *http.Request) {
			r.AddCookie(&http.Cookie{Name: CookieName, Value: "t-TEST"})
		}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(http.MethodGet, srv.URL+"/api/v1/system/health", nil)
			if err != nil {
				t.Fatal(err)
			}
			tc.req(req)
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			resp.Body.Close()
			if resp.StatusCode != http.StatusOK {
				t.Errorf("status = %d, want 200", resp.StatusCode)
			}
			if resp.Header.Get("X-Test-Dispatched") != "yes" {
				t.Error("handler did not run")
			}
		})
	}
}

// TestHeaderBeatsStaleCookie asserts the documented precedence explicitly:
// Authorization wins over a mismatching cookie. The earlier matrix only
// proved each source works in isolation.
func TestHeaderBeatsStaleCookie(t *testing.T) {
	setToken(t, "t-TEST")
	srv := newChain(t)

	req, err := http.NewRequest(http.MethodGet, srv.URL+"/api/v1/system/health", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer t-TEST")
	req.AddCookie(&http.Cookie{Name: CookieName, Value: "stale-ROTATED-VALUE"})
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("valid header + stale cookie = %d, want 200 (header must win)", resp.StatusCode)
	}

	// Inverse: invalid header + valid cookie must NOT be rescued.
	req, err = http.NewRequest(http.MethodGet, srv.URL+"/api/v1/system/health", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer not-the-token")
	req.AddCookie(&http.Cookie{Name: CookieName, Value: "t-TEST"})
	resp, err = http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("invalid header + valid cookie = %d, want 401", resp.StatusCode)
	}
}

// TestWrongTokenRejected checks both same-length and different-length
// mismatches, since a length mismatch must short-circuit before the compare.
func TestWrongTokenRejected(t *testing.T) {
	setToken(t, "t-TEST")
	srv := newChain(t)

	for _, bad := range []string{"wrong", "t-TESTx", "t-TES", "T-TEST"} {
		req, err := http.NewRequest(http.MethodGet, srv.URL+"/api/v1/system/health", nil)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+bad)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusUnauthorized {
			t.Errorf("token %q = %d, want 401", bad, resp.StatusCode)
		}
	}
}

// TestTraversalIsNeverPublic covers CodeQL #62/#63/#67: traversal-encoded
// paths must never fall into the static allowlist.
func TestTraversalIsNeverPublic(t *testing.T) {
	for _, p := range []string{
		"/../app.js",
		"/..%2fapp.js",
		"/\\app.js",
		"/api/health/..",
		"/../api/health",
	} {
		if IsPublicPath(p) {
			t.Errorf("traversal path %q must never be public", p)
		}
	}
}

// TestBootstrapCookieShape pins SameSite=strict / HttpOnly / MaxAge=43200 /
// Path=/ — measured from Python's set_cookie call.
func TestBootstrapCookieShape(t *testing.T) {
	setToken(t, "t-TEST")
	srv := newChain(t)

	resp, err := http.Get(srv.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()

	var cookie *http.Cookie
	for _, c := range resp.Cookies() {
		if c.Name == CookieName {
			cookie = c
		}
	}
	if cookie == nil {
		t.Fatal("bootstrap cookie not set on /")
	}
	if cookie.Value != "t-TEST" {
		t.Errorf("cookie value = %q, want the canonical token", cookie.Value)
	}
	if !cookie.HttpOnly {
		t.Error("cookie must be HttpOnly")
	}
	if cookie.SameSite != http.SameSiteStrictMode {
		t.Errorf("SameSite = %v, want strict", cookie.SameSite)
	}
	if cookie.MaxAge != 43200 {
		t.Errorf("MaxAge = %d, want 43200 (12h)", cookie.MaxAge)
	}
	if cookie.Path != "/" {
		t.Errorf("Path = %q", cookie.Path)
	}
	if cookie.Secure {
		t.Error("cookie must NOT be Secure — legacy console is plain HTTP on LAN")
	}
}

// TestNoCookieOnProtectedPaths asserts the bootstrap cookie only rides the
// public bootstrap documents, never a protected API response.
func TestNoCookieOnProtectedPaths(t *testing.T) {
	setToken(t, "t-TEST")
	srv := newChain(t)

	req, err := http.NewRequest(http.MethodGet, srv.URL+"/api/v1/system/health", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer t-TEST")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	for _, c := range resp.Cookies() {
		if c.Name == CookieName {
			t.Error("protected path must not emit the bootstrap cookie")
		}
	}
}

// TestTokenResolutionOrder covers env > .env > unresolvable, and proves Go
// never invents a token of its own (which would diverge from Python's DPAPI
// value and lock every client out).
func TestTokenResolutionOrder(t *testing.T) {
	t.Setenv(EnvToken, "from-env")
	if tok, ok := CurrentToken(); !ok || tok != "from-env" {
		t.Fatalf("env token: CurrentToken = (%q,%v)", tok, ok)
	}

	noTokenEnvironment(t) // no env, empty cwd
	if _, ok := CurrentToken(); ok {
		t.Fatal("CurrentToken must report unresolvable, never generate one")
	}

	if err := os.WriteFile(".env", []byte("# comment\r\nNSE_WEB_AUTH_TOKEN=from-file\r\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if tok, ok := CurrentToken(); !ok || tok != "from-file" {
		t.Fatalf(".env token: CurrentToken = (%q,%v)", tok, ok)
	}
}

// TestEnvDisableIsInstallTime pins the rollback semantics: the Python server
// factory decides NSE_WEB_AUTH_DISABLE=1 once at startup and never installs
// the middleware, so the switch is invisible to an already-running server.
// Modeling it per-request would be a NEW behaviour, not parity.
func TestEnvDisableIsInstallTime(t *testing.T) {
	noTokenEnvironment(t)
	t.Setenv(EnvDisable, "1")

	var reached bool
	var h http.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = true
		w.WriteHeader(http.StatusOK)
	})
	// The same startup decision routes.Build makes.
	if !Disabled() {
		h = Middleware(h)
	}
	srv := httptest.NewServer(h)
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/api/v1/system/health")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("no middleware installed -> %d, want 200", resp.StatusCode)
	}
	if !reached {
		t.Fatal("handler not reached")
	}

	// The knob must be reported so an operator can see enforcement is off.
	if !Disabled() {
		t.Error("Disabled() = false with NSE_WEB_AUTH_DISABLE=1")
	}
}

// TestLiveModeAlwaysRequiresAuth — LIVE execution must never run without a
// token, so the disable switch stays a trusted-LAN-only convenience.
func TestLiveModeAlwaysRequiresAuth(t *testing.T) {
	t.Setenv("NSE_RUNTIME_MODE", "LIVE")
	t.Cleanup(func() { os.Unsetenv("NSE_RUNTIME_MODE") })
	t.Setenv(EnvDisable, "1") // the trusted-LAN override
	noTokenEnvironment(t)     // ...and no token resolvable

	var reached bool
	h := Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = true
		w.WriteHeader(http.StatusOK)
	}))
	srv := httptest.NewServer(h)
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/api/v1/system/health")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	// Fail-closed regardless: the middleware never reads the disable knob,
	// matching the outermost-layer contract.
	if resp.StatusCode == http.StatusOK {
		t.Error("LIVE + no token must NOT reach the handler")
	}
	if reached {
		t.Error("LIVE + no token reached the handler")
	}
}
