package routing

import (
	"bytes"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"sync/atomic"
)

// Target describes the execution target for an incoming request.
type Target string

const (
	// TargetGoDirect indicates a candidate route served directly by Go without calling Python.
	TargetGoDirect Target = "go-direct"
	// TargetPythonProxy indicates a route needing Python (DB access, write, stateful logic).
	TargetPythonProxy Target = "python-proxy"
	// TargetPythonFallback indicates an attempted Go direct serving that failed or errored,
	// triggering fallback to Python.
	TargetPythonFallback Target = "python-fallback"
)

// String returns the string representation of Target.
func (t Target) String() string {
	return string(t)
}

const (
	// HeaderRoutingTarget is the response header indicating the routing target.
	HeaderRoutingTarget = "X-NSE-Routing-Target"
	// HeaderRoutingReason is the response header carrying the classification why.
	HeaderRoutingReason = "X-NSE-Routing-Reason"
	// EnvDirectServingDisable is the environment variable that disables Go direct serving.
	EnvDirectServingDisable = "NSE_GO_DIRECT_SERVING_DISABLE"
)

var fallbackTotal atomic.Int64

// FallbackTotal returns the total number of fallback occurrences recorded.
func FallbackTotal() int64 {
	return fallbackTotal.Load()
}

// ResetFallbackTotal resets the fallback counter (useful in tests).
func ResetFallbackTotal() {
	fallbackTotal.Store(0)
}

// DirectServingEnabled reports whether direct Go serving is enabled.
// If NSE_GO_DIRECT_SERVING_DISABLE is "1" or "true" (case-insensitive),
// direct serving is disabled and all requests route to TargetPythonProxy.
func DirectServingEnabled() bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv(EnvDirectServingDisable)))
	return v != "1" && v != "true"
}

// Route determines the Target for a given HTTP method and path.
// If direct serving is disabled, it returns TargetPythonProxy.
// If Candidate(method, path) is true, it returns TargetGoDirect.
// Otherwise, it returns TargetPythonProxy.
func Route(method, path string) Target {
	if !DirectServingEnabled() {
		return TargetPythonProxy
	}
	if Candidate(method, path) {
		return TargetGoDirect
	}
	return TargetPythonProxy
}

// SetRoutingHeaders attaches routing response headers:
// - X-NSE-Routing-Target: "go-direct" | "python-proxy" | "python-fallback"
// - X-NSE-Routing-Reason: reason string (e.g. from Decide(method, path).Why)
func SetRoutingHeaders(w http.ResponseWriter, target Target, reason string) {
	if w == nil {
		return
	}
	w.Header().Set(HeaderRoutingTarget, string(target))
	if reason != "" {
		w.Header().Set(HeaderRoutingReason, reason)
	}
}

// responseBuffer captures writes from directFn so that if directFn fails
// or panics, the buffer can be safely discarded without sending corrupted
// or partial HTTP responses to the client before fallbackFn runs.
type responseBuffer struct {
	header      http.Header
	body        bytes.Buffer
	statusCode  int
	wroteHeader bool
}

func newResponseBuffer() *responseBuffer {
	return &responseBuffer{
		header: make(http.Header),
	}
}

func (b *responseBuffer) Header() http.Header {
	return b.header
}

func (b *responseBuffer) Write(p []byte) (int, error) {
	if !b.wroteHeader {
		b.WriteHeader(http.StatusOK)
	}
	return b.body.Write(p)
}

func (b *responseBuffer) WriteHeader(statusCode int) {
	if b.wroteHeader {
		return
	}
	b.statusCode = statusCode
	b.wroteHeader = true
}

func (b *responseBuffer) apply(w http.ResponseWriter) {
	for k, vv := range b.header {
		w.Header()[k] = append([]string(nil), vv...)
	}
	w.Header().Set(HeaderRoutingTarget, string(TargetGoDirect))
	if b.wroteHeader {
		w.WriteHeader(b.statusCode)
	}
	if b.body.Len() > 0 {
		_, _ = w.Write(b.body.Bytes())
	}
}

// ServeWithFallback executes directFn to serve a request directly in Go.
// If directFn returns nil (success), headers reflect TargetGoDirect and the
// buffered response is committed to w.
// If directFn returns an error (or panics, recovered), records fallback, sets
// header TargetPythonFallback, and calls fallbackFn.
func ServeWithFallback(
	w http.ResponseWriter,
	r *http.Request,
	directFn func(w http.ResponseWriter, r *http.Request) error,
	fallbackFn func(w http.ResponseWriter, r *http.Request),
) {
	if w == nil {
		return
	}

	// Preserve request body in case directFn consumes it and then fails.
	var bodyBytes []byte
	if r != nil && r.Body != nil && r.Body != http.NoBody {
		bodyBytes, _ = io.ReadAll(r.Body)
		r.Body = io.NopCloser(bytes.NewReader(bodyBytes))
	}

	buf := newResponseBuffer()

	var directErr error
	var panicked bool

	func() {
		defer func() {
			if rec := recover(); rec != nil {
				panicked = true
				directErr = fmt.Errorf("direct serving panic: %v", rec)
			}
		}()
		directErr = directFn(buf, r)
	}()

	if directErr == nil && !panicked {
		if r != nil && r.URL != nil && w.Header().Get(HeaderRoutingReason) == "" {
			d := Decide(r.Method, r.URL.Path)
			if d.Classified {
				w.Header().Set(HeaderRoutingReason, d.Why)
			}
		}
		buf.apply(w)
		return
	}

	// Record fallback occurrence.
	recordFallback(r, directErr)

	// Set fallback routing header on the real ResponseWriter.
	w.Header().Set(HeaderRoutingTarget, string(TargetPythonFallback))
	if r != nil && r.URL != nil && w.Header().Get(HeaderRoutingReason) == "" {
		d := Decide(r.Method, r.URL.Path)
		if d.Classified {
			w.Header().Set(HeaderRoutingReason, d.Why)
		}
	}

	// Restore request body for fallback handler if it was read.
	if bodyBytes != nil && r != nil {
		r.Body = io.NopCloser(bytes.NewReader(bodyBytes))
	}

	if fallbackFn != nil {
		fallbackFn(w, r)
	}
}

func recordFallback(r *http.Request, err error) {
	fallbackTotal.Add(1)
	method := ""
	path := ""
	if r != nil {
		method = r.Method
		if r.URL != nil {
			path = r.URL.Path
		}
	}
	slog.Warn("routing: direct serving failed, falling back to python",
		"method", method,
		"path", path,
		"error", err,
	)
}
