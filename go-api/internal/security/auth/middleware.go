// Package auth implements the NSE WEB-AUTH-P0 token contract.
//
// Frozen from src/nexus_scalp/web/auth.py. Semantics (verified against a live
// create_app() instance):
//
//   - FAIL-CLOSED: every request is authenticated unless its path is on the
//     public allowlist. Unknown paths are never public.
//   - Token sources, first wins: NSE_WEB_AUTH_TOKEN env >
//     SecureSecretStore["web_auth_token"] > generated once + persisted. No
//     anonymous mode. A resolution failure blocks everything (500 with the
//     AUTH_CONFIG_ERROR detail), never fails open.
//   - Constant-time compare (crypto/subtle), length-checked first.
//   - Token accepted on: Authorization: Bearer, X-NSE-Token header,
//     nse_web_auth cookie (opt-out NSE_WEB_AUTH_COOKIE_DISABLE=1), and
//     ?token= (kept for EventSource clients that cannot set headers).
//     Headers win over the cookie.
//   - LIVE mode always requires auth regardless of environment.
//
// The public allowlist is the single source of truth — traversal-encoded
// paths ("..", "\\") are NEVER public (CodeQL #62/#63/#67).
package auth

import (
	"crypto/subtle"
	"errors"
	"net/http"
	"os"
	"strings"
)

// Env knobs (identical names to the Python contract).
const (
	EnvToken         = "NSE_WEB_AUTH_TOKEN"
	EnvDisable       = "NSE_WEB_AUTH_DISABLE"
	EnvCookieDisable = "NSE_WEB_AUTH_COOKIE_DISABLE"

	// CookieName is the first-party HttpOnly bootstrap cookie name.
	CookieName = "nse_web_auth"
	SecretName = "web_auth_token"
)

// ErrTokenUnresolvable means a token must exist and could not be produced.
// The middleware FAILS CLOSED in this state.
var ErrTokenUnresolvable = errors.New("web auth token unresolvable")

// PublicPaths mirrors PUBLIC_PATHS in auth.py: static shells and documents
// only. Every /api/** route enforces.
var PublicPaths = map[string]bool{
	"/api/health":  true,
	"/health":      true,
	"/healthz":     true,
	"/favicon.ico": true,
	// BUG-267 cookie-bootstrap entry documents (static shells, no state).
	"/":                     true,
	"/index.html":           true,
	"/alt":                  true,
	"/alt/":                 true,
	"/app.js":               true,
	"/app.js.map":           true,
	"/api_client.js":        true,
	"/styles.css":           true,
	"/responsive.css":       true,
	"/cc_styles.css":        true,
	"/tailwind.css":         true,
	"/tv_widget_styles.css": true,
	"/tv_widget.js":         true,
	"/tv_widget.html":       true,
	"/control_center.js":    true,
	"/forensic_console.js":  true,
	"/news_intelligence.js": true,
	"/replay_panel.js":      true,
	"/marketplace.js":       true,
	"/model_studio_ui.js":   true,
	"/dependency_api.js":    true,
	"/dependency_graph.js":  true,
	"/dependency_ui.js":     true,
	"/dependency.html":      true,
	"/dependency":           true,
	"/command_center.html":  true,
	"/first_setup.html":     true,
}

// PublicPrefixes mirrors PUBLIC_PREFIXES. A trailing slash is mandatory on
// /alt/ so the prefix can never match anything outside the console mount.
var PublicPrefixes = []string{"/static/", "/assets/", "/vendor/", "/alt/"}

// shellDenyPrefixes mirrors _SHELL_DENY_PREFIXES (auth.py). /api, /ws and /web
// are data classes: an unmatched child must stay gated (every data route keeps
// full token enforcement). /alt is included because its surfaces are
// allowlisted EXPLICITLY in PublicPaths + the /alt/ prefix above; without the
// deny entry the dotless rule below would wrongly publish the never-public
// shapes /altx and /alternative-api.
var shellDenyPrefixes = []string{"/api", "/ws", "/web", "/alt"}

// isPublicStaticShell mirrors _is_public_static_shell (CONTRACT frozen
// decision #7): a path is a public static shell when its LAST segment contains
// no "." (a dot marks a file/asset, which needs an explicit allowlist entry
// instead) AND it starts with no deny prefix. Static shells carry no state and
// no credentials, so deep links like /trading must render the SPA shell from a
// tokenless first navigation while every data route stays gated.
func isPublicStaticShell(path string) bool {
	for _, p := range shellDenyPrefixes {
		if strings.HasPrefix(path, p) {
			return false
		}
	}
	last := path
	if i := strings.LastIndex(path, "/"); i >= 0 {
		last = path[i+1:]
	}
	return !strings.Contains(last, ".")
}

// PublicJSAssets mirrors PUBLIC_JS_ASSETS (repo-root static scripts).
var PublicJSAssets = map[string]bool{
	"ux_i18n.js": true, "ux_conn.js": true, "ux.js": true, "ux_signal.js": true,
	"ux_attention.js": true, "ux_palette.js": true, "api_client.js": true,
	"cc_components.js": true, "cc_state.js": true,
	"command_center_console.js": true, "command_center_spatial.js": true,
	"command_center_timemachine.js": true, "command_center_ui.js": true,
	"backtest_report_ui.js": true,
}

// CookieBootstrapPaths mirrors COOKIE_BOOTSTRAP_PATHS: public paths whose
// response carries the bootstrap Set-Cookie.
var CookieBootstrapPaths = map[string]bool{
	"/": true, "/index.html": true, "/app.js": true, "/api_client.js": true,
	"/alt": true, "/alt/": true, "/first_setup.html": true,
}

// IsPublicPath is the single source of truth for the no-token allowlist.
// Path-traversal separators can never be public. It is path-only: callers
// without a method context (parity probes, SPA dispatch) keep using it.
func IsPublicPath(path string) bool {
	if strings.Contains(path, "..") || strings.Contains(path, "\\") {
		return false
	}
	if PublicPaths[path] {
		return true
	}
	name := strings.TrimLeft(path, "/")
	if PublicJSAssets[name] {
		return true
	}
	for _, p := range PublicPrefixes {
		if strings.HasPrefix(path, p) {
			return true
		}
	}
	return false
}

// IsPublicPathMethod mirrors is_public_path(path, method): the dotless static
// shell rule is GET/HEAD-only, so a non-GET/HEAD never widens the surface.
func IsPublicPathMethod(path, method string) bool {
	if IsPublicPath(path) {
		return true
	}
	if method != "" && method != "GET" && method != "HEAD" {
		return false
	}
	return isPublicStaticShell(path)
}

// Disabled reports whether the NSE_WEB_AUTH_DISABLE=1 rollback knob is set.
//
// Like Python's _install_web_auth_if_enabled this is evaluated ONCE at
// startup (in routes.Build) and not per request: the middleware is simply
// never installed. A per-request check would differ from the reference
// implementation. Trusted-LAN only — never combine with LIVE execution or a
// routable host binding.
func Disabled() bool {
	return strings.TrimSpace(os.Getenv(EnvDisable)) == "1"
}

// Middleware enforces token auth. It wraps next; unauthenticated requests get
// the exact legacy 401 body and never reach the handler.
//
// The caller is responsible for calling Disabled() first: this function
// installs unconditionally, because a silently-skipped enforcement layer is
// exactly the class of bug WEB-AUTH-P0 exists to prevent.
func Middleware(next http.Handler) http.Handler {
	token, err := resolveToken()
	mw := &middleware{token: token, err: err}
	return mw.wrap(next)
}

type middleware struct {
	token string
	err   error
}

func (m *middleware) wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Public paths pass through (and may receive the bootstrap cookie).
		if IsPublicPathMethod(r.URL.Path, r.Method) {
			if CookieBootstrapPaths[r.URL.Path] {
				m.setBootstrapCookie(w)
			}
			next.ServeHTTP(w, r)
			return
		}
		if m.err != nil || m.token == "" {
			// Fail-closed: no token resolvable -> block everything.
			writeAuthConfigError(w)
			return
		}
		supplied := extractToken(r)
		if supplied == "" {
			writeUnauthorized(w)
			return
		}
		// Constant-time compare; compare the lengths first so a timing
		// signal cannot leak the token length.
		suppliedB, tokenB := []byte(supplied), []byte(m.token)
		if len(suppliedB) != len(tokenB) {
			writeUnauthorized(w)
			return
		}
		if subtle.ConstantTimeCompare(suppliedB, tokenB) != 1 {
			writeUnauthorized(w)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// setBootstrapCookie issues the first-party HttpOnly cookie carrying the SAME
// canonical token the middleware enforces. Headers still win over the cookie.
// Mirrors Python set_cookie(max_age=60*60*12, httponly=True,
// samesite="strict", path="/").
func (m *middleware) setBootstrapCookie(w http.ResponseWriter) {
	if m.token == "" || os.Getenv(EnvCookieDisable) != "" {
		return
	}
	http.SetCookie(w, &http.Cookie{
		Name:     CookieName,
		Value:    m.token,
		Path:     "/",
		MaxAge:   60 * 60 * 12,
		HttpOnly: true,
		SameSite: http.SameSiteStrictMode,
		Secure:   false, // the legacy console is served over plain HTTP on LAN
	})
}

// extractToken reads Authorization > X-NSE-Token > cookie > ?token=.
func extractToken(r *http.Request) string {
	if auth := r.Header.Get("Authorization"); strings.HasPrefix(strings.ToLower(auth), "bearer ") {
		if v := strings.TrimSpace(auth[7:]); v != "" {
			return v
		}
	}
	if xt := strings.TrimSpace(r.Header.Get("X-NSE-Token")); xt != "" {
		return xt
	}
	if os.Getenv(EnvCookieDisable) == "" {
		if c, err := r.Cookie(CookieName); err == nil {
			if v := strings.TrimSpace(c.Value); v != "" {
				return v
			}
		}
	}
	if q := r.URL.Query().Get("token"); strings.TrimSpace(q) != "" {
		return strings.TrimSpace(q)
	}
	return ""
}

// resolveToken mirrors _resolve_token's resolution CHAIN, with one deliberate,
// safety-preserving deviation: Go NEVER generates a token.
//
// Why: Python persists the generated token in the DPAPI secret store
// (SecureSecretStore["web_auth_token"]), which Go cannot read or write. If Go
// minted its own value the two runtimes would enforce DIFFERENT tokens and
// every request would 401 — a divergence, not an upgrade. auth_boot.publish()
// exports the canonical token to NSE_WEB_AUTH_TOKEN and the gitignored
// repo-root .env, which is exactly what Go reads.
//
// No token resolvable -> ErrTokenUnresolvable -> the middleware answers
// AUTH_CONFIG_ERROR (fail-closed), never an anonymous mode.
func resolveToken() (string, error) {
	if env := strings.TrimSpace(os.Getenv(EnvToken)); env != "" {
		return env, nil
	}
	if stored, err := readDotEnvToken(); err == nil && stored != "" {
		return stored, nil
	}
	return "", ErrTokenUnresolvable
}

// readDotEnvToken reads NSE_WEB_AUTH_TOKEN from a .env beside the working
// directory — the file auth_boot.publish() writes. Tolerant of missing files,
// comments, CRLF and quoted values.
func readDotEnvToken() (string, error) {
	for _, p := range []string{".env", "../.env"} {
		b, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(b), "\n") {
			line = strings.TrimSpace(strings.TrimSuffix(line, "\r"))
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			k, v, ok := strings.Cut(line, "=")
			if !ok || strings.TrimSpace(k) != EnvToken {
				continue
			}
			v = strings.TrimSpace(v)
			v = strings.Trim(v, `"'`)
			if v != "" {
				return v, nil
			}
		}
	}
	return "", ErrTokenUnresolvable
}

// CurrentToken mirrors current_web_auth_token(): NEVER generates, so a
// read-only probe can never mint a second authoritative value.
func CurrentToken() (string, bool) {
	if tok, err := resolveToken(); err == nil {
		return tok, true
	}
	return "", false
}

// writeUnauthorized emits the exact legacy 401 body.
func writeUnauthorized(w http.ResponseWriter) {
	const body = `{"ok":false,"error":{"code":"UNAUTHORIZED","message":"missing or invalid web auth token"}}`
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("WWW-Authenticate", "Bearer")
	w.WriteHeader(http.StatusUnauthorized)
	_, _ = w.Write([]byte(body))
}

// writeAuthConfigError emits the fail-closed 500 used when a token must exist
// and cannot be produced.
func writeAuthConfigError(w http.ResponseWriter) {
	const body = `{"ok":false,"error":{"code":"AUTH_CONFIG_ERROR","message":"web auth token unresolvable (fail-closed)"}}`
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusInternalServerError)
	_, _ = w.Write([]byte(body))
}
