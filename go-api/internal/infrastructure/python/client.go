// Package python is the Python<->Go boundary: Go talks to the authoritative
// Python runtime over localhost HTTP and NEVER spawns a subprocess per
// request.
//
// Why this boundary exists (evidence-based, not fashionable):
//
//   - The facts Phase A needs (HealthEngine sweep, version metadata, engine
//     attachment, MT5 adapter state) are PRODUCED by Python. Reimplementing
//     them in Go would fabricate runtime facts, which the migration rules
//     explicitly forbid.
//   - Python stays the ML/MT5 authority. Go becomes the high-concurrency
//     API/control/event plane: auth, envelopes, static assets, streaming
//     fan-out, bounded concurrency, connection reuse.
//
// Every call is context-canceled, deadline-bounded and circuit-broken: a
// hung Python runtime must degrade one endpoint, never the whole Go process.
package python

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/observability"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/security/auth"
	"github.com/Opselon/NexusTradingForexBot/go-api/pkg/contracts"
)

// Boundary transport tuning (Wave 6).
//
// The default http.Transport has MaxIdleConnsPerHost=2, which caps the
// reusable connection pool at two idle sockets per upstream. Under any real
// concurrency the pool is exhausted and every overflow request pays a fresh
// 127.0.0.1 TCP setup (~1-3ms) plus a uvicorn accept; under burst load the
// backlog queues and shows up as a multi-hundred-ms p99. Raising the pool and
// keeping idle sockets alive across the request gap removes that per-request
// connection cost.
const (
	// maxIdleConnsPerHost is the reusable connection pool size for the single
	// localhost upstream. 128 comfortably covers the control plane's real
	// concurrency; beyond that the overflow cost is a new socket, not a stall.
	maxIdleConnsPerHost = 128
	// maxIdleConns bounds the whole pool (one host in practice).
	maxIdleConns = 256
	// idleConnTimeout is how long an unused socket stays in the pool. It must
	// stay comfortably BELOW the upstream's own keep-alive timeout, otherwise
	// Go reuses a socket the upstream has already half-closed and the request
	// fails with a spurious EOF (retried once, costing latency). uvicorn's
	// default timeout_keep_alive is 5s, so 70s is wrong; 4s is.
	idleConnTimeout = 4 * time.Second
	// dialTimeout bounds upstream connection setup so a hung Python runtime
	// degrades this request, not the process.
	dialTimeout = 5 * time.Second
	// keepAliveInterval is the TCP keep-alive probe interval for pooled
	// sockets. The default is 15s; on Windows a shorter interval surfaces a
	// dead upstream peer faster than the dial timeout alone.
	keepAliveInterval = 30 * time.Second
	// responseHeaderTimeout bounds the time waiting for the upstream response
	// headers once the request is written, so a slow upstream cannot hold a
	// pooled connection (and a goroutine) forever. The Client.Timeout stays the
	// outer bound for the whole call.
	responseHeaderTimeout = 25 * time.Second
)

// newBoundaryTransport builds the shared, keep-alive-enabled http.Transport
// used by every python.Client. One transport is shared across clients so the
// connection pool is process-wide (all clients talk to the same localhost
// upstream anyway); pooling is what makes the proxy not pay TCP setup per
// request.
//
// HTTP/2 is NOT attempted: the Python upstream is uvicorn over cleartext HTTP
// (no h2c negotiation), so ForceAttemptHTTP2 only adds an ALPN round-trip that
// always falls back to HTTP/1.1 anyway.
func newBoundaryTransport() *http.Transport {
	return &http.Transport{
		Proxy: http.ProxyFromEnvironment,
		DialContext: (&net.Dialer{
			Timeout:   dialTimeout,
			KeepAlive: keepAliveInterval,
		}).DialContext,
		MaxIdleConns:          maxIdleConns,
		MaxIdleConnsPerHost:   maxIdleConnsPerHost,
		MaxConnsPerHost:       0,
		IdleConnTimeout:       idleConnTimeout,
		ResponseHeaderTimeout: responseHeaderTimeout,
		ExpectContinueTimeout: 1 * time.Second,
	}
}

// sharedTransport is the single process-wide upstream transport. It is created
// once and never replaced: transports are safe for concurrent use and their
// pools are what we want every request to reuse.
var (
	sharedTransportOnce sync.Once
	sharedTransport     *http.Transport
)

// boundaryTransport returns the shared tuned transport, building it on first
// use.
func boundaryTransport() *http.Transport {
	sharedTransportOnce.Do(func() {
		sharedTransport = newBoundaryTransport()
	})
	return sharedTransport
}

// NewTransportForTest exposes the same transport construction used in
// production so tests can assert on its keep-alive configuration.
func NewTransportForTest() *http.Transport {
	return newBoundaryTransport()
}

// ErrNotConfigured means no Python origin is configured (engine not
// attached). Maps to the v1 DEPENDENCY_UNAVAILABLE / ENGINE_UNAVAILABLE codes.
var ErrNotConfigured = errors.New("python runtime not configured")

// ErrUnavailable means the configured origin failed to respond in time.
var ErrUnavailable = errors.New("python runtime unavailable")

// ErrBoundary is the sentinel BoundaryError unwraps to, so callers can use
// errors.Is(err, python.ErrBoundary) without knowing the envelope details.
var ErrBoundary = errors.New("python runtime returned a contract error envelope")

// ErrLegacy is the sentinel LegacyResponse unwraps to. It marks "the upstream
// ANSWERED with a non-v1 error body" — a normal FastAPI 404/422/403 on the
// legacy surface, not a boundary failure.
var ErrLegacy = errors.New("python runtime answered with a legacy error body")

// BoundaryError: Python answered with an HTTP >= 400 carrying a valid v1 error
// envelope. This is a CONTRACT response, not a transport failure, so the
// circuit breaker stays closed. The envelope is preserved so a handler can
// replay Python's exact error body — byte-level error parity.
type BoundaryError struct {
	Status   int
	Envelope contracts.ErrorEnvelope
}

func (e *BoundaryError) Error() string {
	return fmt.Sprintf("python runtime returned status %d", e.Status)
}

func (e *BoundaryError) Unwrap() error { return ErrBoundary }

// LegacyResponse: Python answered with an HTTP >= 400 that is NOT the v1
// error envelope — a legacy {"detail": ...} body. FastAPI emits these for
// every 404/422/403 on the dashboard surface. It is a normal answer and MUST
// be replayed verbatim: reclassifying it would fabricate an outage the
// upstream never reported.
type LegacyResponse struct {
	Status int
	Body   []byte
}

func (e *LegacyResponse) Error() string {
	return fmt.Sprintf("python runtime answered status %d (legacy body)", e.Status)
}

func (e *LegacyResponse) Unwrap() error { return ErrLegacy }

// AsBoundary unwraps a BoundaryError, returning false for anything else.
func AsBoundary(err error) (*BoundaryError, bool) {
	var be *BoundaryError
	if errors.As(err, &be) {
		return be, true
	}
	return nil, false
}

// AsLegacy unwraps a LegacyResponse, returning false for anything else.
func AsLegacy(err error) (*LegacyResponse, bool) {
	var lr *LegacyResponse
	if errors.As(err, &lr) {
		return lr, true
	}
	return nil, false
}

// Client calls the authoritative Python runtime.
type Client struct {
	origin string
	hc     *http.Client

	mu          sync.Mutex
	failures    int
	openUntil   time.Time
	maxFailures int
	coolDown    time.Duration
}

// Options configures the boundary client.
type Options struct {
	// Origin is the Python runtime base URL (e.g. http://127.0.0.1:8087).
	Origin string
	// Timeout bounds a single upstream call. The Python HealthEngine sweep
	// measures ~0.7-2.4s, but DB validation and dependency-cycle analysis can
	// take considerably longer under a cold or loaded engine. Classifying a
	// slow-but-successful answer as a circuit failure fabricates an outage
	// Python never reported, so the default is generous; callers pass their
	// own tighter ctx deadlines where appropriate.
	Timeout time.Duration
	// MaxFailures trips the circuit after N consecutive failures.
	MaxFailures int
	// Cooldown is how long the circuit stays open before a probe is allowed.
	CoolDown time.Duration
}

// DefaultOptions matches the observed Python behaviour.
func DefaultOptions() Options {
	return Options{
		Origin:      "http://127.0.0.1:8087",
		Timeout:     30 * time.Second,
		MaxFailures: 3,
		CoolDown:    5 * time.Second,
	}
}

// New builds a boundary client.
func New(opts Options) *Client {
	if opts.Timeout == 0 {
		opts.Timeout = DefaultOptions().Timeout
	}
	if opts.MaxFailures == 0 {
		opts.MaxFailures = DefaultOptions().MaxFailures
	}
	if opts.CoolDown == 0 {
		opts.CoolDown = DefaultOptions().CoolDown
	}
	return &Client{
		origin:      opts.Origin,
		hc:          &http.Client{Timeout: opts.Timeout, Transport: boundaryTransport()},
		maxFailures: opts.MaxFailures,
		coolDown:    opts.CoolDown,
	}
}

// Transport exposes the client's shared upstream transport so callers (and
// tests) can inspect or reuse the tuned connection pool. Never nil.
func (c *Client) Transport() http.RoundTripper {
	if c == nil || c.hc == nil || c.hc.Transport == nil {
		return boundaryTransport()
	}
	return c.hc.Transport
}

// Configured reports whether an origin is set (engine attached?).
func (c *Client) Configured() bool { return c != nil && c.origin != "" }

// tripped reports whether the circuit is currently open.
func (c *Client) tripped() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return time.Now().Before(c.openUntil)
}

func (c *Client) recordSuccess() {
	c.mu.Lock()
	c.failures = 0
	c.mu.Unlock()
}

func (c *Client) recordFailure() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.failures++
	if c.failures >= c.maxFailures {
		c.openUntil = time.Now().Add(c.coolDown)
		c.failures = 0
	}
}

// GetJSON performs a bounded GET and decodes the response body into out.
// It forwards the caller's correlation ID and auth token so the trace stays
// continuous and Python's own middleware does not reject the hop.
func (c *Client) GetJSON(ctx context.Context, path string, out any) error {
	return c.DoJSON(ctx, http.MethodGet, path, nil, out)
}

// DoJSON performs a bounded request of any method, decodes into out, and
// enforces the boundary contract:
//
//   - auth + X-Request-ID are forwarded (trace continuity, §30);
//   - HTTP >= 400 is an ERROR, never a silent success. The earlier bug this
//     fixes: a Python 401 decoded cleanly into the envelope struct, leaving
//     Data nil, and the handler then served {"data":null} with a 200 —
//     fabricating an empty payload instead of reporting the failure.
//
// body is JSON-marshalled when non-nil; use DoJSONRaw to forward a client
// request body byte-for-byte without re-encoding (streaming a POST through
// unchanged is what keeps Python's own validation authoritative).
func (c *Client) DoJSON(ctx context.Context, method, path string, body any, out any) error {
	var rdr io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		rdr = bytes.NewReader(b)
	}
	return c.do(ctx, method, path, rdr, out)
}

// DoJSONRaw forwards body untouched and decodes the response into out.
func (c *Client) DoJSONRaw(ctx context.Context, method, path string, body io.Reader, out any) error {
	return c.do(ctx, method, path, body, out)
}

// maxUpstreamBody bounds how many bytes of a Python response the buffered
// paths (DoRaw/DoJSON) will read before giving up. It is a runaway guard
// against a broken upstream emitting an endless stream, NOT a correctness
// limit: Python legitimately emits multi-megabyte payloads
// (/api/v1/research/strategies measures ~15 MiB with the full registry), and
// an undersized cap silently truncates a valid body mid-string, the unmarshal
// then fails, and the handler reports 503 DEPENDENCY_UNAVAILABLE — an outage
// the upstream never had. 64 MiB keeps the guard far above any real payload.
const maxUpstreamBody = 64 << 20

// DoRaw forwards the request and returns the raw response body. Use this when
// the caller must preserve Python's exact byte output (key order, float
// formatting, an envelope it does not want re-derived).
func (c *Client) DoRaw(ctx context.Context, method, path string, body io.Reader) ([]byte, error) {
	if !c.Configured() {
		return nil, ErrNotConfigured
	}
	if c.tripped() {
		return nil, ErrUnavailable
	}

	req, err := http.NewRequestWithContext(ctx, method, c.origin+path, body)
	if err != nil {
		return nil, err
	}
	if tok, ok := auth.CurrentToken(); ok && tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	if rid := observability.RequestIDFrom(ctx); rid != "" {
		req.Header.Set("X-Request-ID", rid)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.hc.Do(req)
	if err != nil {
		c.recordFailure()
		return nil, fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	defer resp.Body.Close()

	payload, err := io.ReadAll(io.LimitReader(resp.Body, maxUpstreamBody))
	if err != nil {
		c.recordFailure()
		return nil, fmt.Errorf("%w: %v", ErrUnavailable, err)
	}

	// Contract responses are replayed exactly, as DoJSON does. But the shape
	// check must be surface-specific: a legacy {"detail": ...} body is a
	// NORMAL FastAPI error answer (404 "Not Found", 422 validation, 403…),
	// not a boundary failure. Treating it as one upgrades every legacy 4xx to
	// a 503 DEPENDENCY_UNAVAILABLE, which fabricates an outage Python never
	// reported. Only the /api/v1 error envelope counts as a contract answer.
	if resp.StatusCode >= 400 {
		if strings.HasPrefix(path, "/api/v1/") {
			var env contracts.ErrorEnvelope
			if jerr := json.Unmarshal(payload, &env); jerr == nil && env.Error.Code != "" {
				return nil, &BoundaryError{Status: resp.StatusCode, Envelope: env}
			}
		}
		// Legacy surface, or a non-envelope v1 body: the upstream answered.
		// Hand the caller the status so it can serve the right body.
		return nil, &LegacyResponse{Status: resp.StatusCode, Body: payload}
	}

	// 204 / empty: nothing to interpret.
	if len(payload) == 0 {
		return nil, nil
	}
	c.recordSuccess()
	return payload, nil
}

// DoStream forwards the request and returns the live upstream response WITHOUT
// buffering the body. This is the only correct way to proxy a Server-Sent
// Events stream (text/event-stream): the buffered DoRaw path reads the whole
// body with io.ReadAll, which blocks forever on a stream that is intentionally
// open-ended, and even if it completed it would discard Python's
// Content-Type/Cache-Control/Connection headers and re-emit the bytes as
// application/json — Chrome's EventSource then aborts with
// "response has a MIME type (application/json) that is not
// text/event-stream", killing live tick/trace updates through the Go origin.
//
// The caller owns the returned response and MUST close resp.Body. Failure
// semantics mirror DoRaw: ErrNotConfigured / ErrUnavailable / *BoundaryError /
// *LegacyResponse are classified the same way so a stream's error answers
// cannot diverge from a buffered route's. Because the caller streams outside
// the circuit breaker's observation window, only the connection setup and
// header read count toward failure accounting — a long-lived stream must never
// be able to trip the breaker just by being long-lived.
func (c *Client) DoStream(ctx context.Context, method, path string, body io.Reader) (*http.Response, error) {
	if !c.Configured() {
		return nil, ErrNotConfigured
	}
	if c.tripped() {
		return nil, ErrUnavailable
	}

	req, err := http.NewRequestWithContext(ctx, method, c.origin+path, body)
	if err != nil {
		return nil, err
	}
	if tok, ok := auth.CurrentToken(); ok && tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	if rid := observability.RequestIDFrom(ctx); rid != "" {
		req.Header.Set("X-Request-ID", rid)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.hc.Do(req)
	if err != nil {
		c.recordFailure()
		return nil, fmt.Errorf("%w: %v", ErrUnavailable, err)
	}

	// A >=400 answer is a finite, complete response, not a stream: classify it
	// exactly as DoRaw does and hand the caller the boundary error so SSE
	// endpoints report Python's own 401/403/422/503 instead of hanging or
	// being masked. The body still has to be drained and closed here because
	// the caller never sees the Response on this path.
	if resp.StatusCode >= 400 {
		payload, rerr := io.ReadAll(io.LimitReader(resp.Body, maxUpstreamBody))
		_ = resp.Body.Close()
		if rerr != nil {
			c.recordFailure()
			return nil, fmt.Errorf("%w: %v", ErrUnavailable, rerr)
		}
		if strings.HasPrefix(path, "/api/v1/") {
			var env contracts.ErrorEnvelope
			if jerr := json.Unmarshal(payload, &env); jerr == nil && env.Error.Code != "" {
				return nil, &BoundaryError{Status: resp.StatusCode, Envelope: env}
			}
		}
		return nil, &LegacyResponse{Status: resp.StatusCode, Body: payload}
	}

	c.recordSuccess()
	return resp, nil
}

func (c *Client) do(ctx context.Context, method, path string, body io.Reader, out any) error {
	if !c.Configured() {
		return ErrNotConfigured
	}
	if c.tripped() {
		return ErrUnavailable
	}

	req, err := http.NewRequestWithContext(ctx, method, c.origin+path, body)
	if err != nil {
		return err
	}
	// Forward auth: Python enforces the same token on its own surface.
	if tok, ok := auth.CurrentToken(); ok && tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	// Forward correlation so one client request traces across the boundary.
	if rid := observability.RequestIDFrom(ctx); rid != "" {
		req.Header.Set("X-Request-ID", rid)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.hc.Do(req)
	if err != nil {
		c.recordFailure()
		return fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	defer resp.Body.Close()

	bodyBytes, err := io.ReadAll(io.LimitReader(resp.Body, maxUpstreamBody))
	if err != nil {
		c.recordFailure()
		return fmt.Errorf("%w: %v", ErrUnavailable, err)
	}

	// Classify BEFORE tripping the circuit. A >= 400 response carrying a valid
	// v1 envelope is Python honouring its contract (a 503 ENGINE_UNAVAILABLE
	// is the documented "no adapter" answer, not a crash). Only a response
	// with NO usable body counts as a real upstream failure.
	if resp.StatusCode >= 400 {
		var env contracts.ErrorEnvelope
		if jerr := json.Unmarshal(bodyBytes, &env); jerr == nil && env.Error.Code != "" {
			// Contract response: replay it exactly. Never re-derived here.
			return &BoundaryError{Status: resp.StatusCode, Envelope: env}
		}
		if resp.StatusCode >= 500 {
			c.recordFailure()
			return fmt.Errorf("%w: upstream status %d", ErrUnavailable, resp.StatusCode)
		}
		return fmt.Errorf("%w: upstream status %d", ErrBoundary, resp.StatusCode)
	}

	if err := json.Unmarshal(bodyBytes, out); err != nil {
		c.recordFailure()
		return fmt.Errorf("malformed JSON from python runtime: %w", ErrUnavailable)
	}
	c.recordSuccess()
	return nil
}
