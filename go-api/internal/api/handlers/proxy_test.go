// Package handlers tests for the generic proxy.
package handlers

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
)

// stubClient implements the call surface Proxy uses without a network.
type stubClient struct {
	raw   []byte
	err   error
	calls int
}

func (s *stubClient) DoRaw(_ interface{ Done() <-chan struct{} }, method, path string, body interface{}) ([]byte, error) {
	s.calls++
	return s.raw, s.err
}

// TestLegacyErrorIsReplayed is the regression test for the highest-impact bug
// the full-surface harness found: a legacy {"detail": ...} 404/422 was being
// classified as a boundary failure and served as a v1 503
// DEPENDENCY_UNAVAILABLE. That fabricated an outage Python never reported —
// every dashboard 404 looked like the engine was down.
func TestLegacyErrorIsReplayed(t *testing.T) {
	body := []byte(`{"detail":"Not Found"}`)

	lr := &python.LegacyResponse{Status: http.StatusNotFound, Body: body}
	if !errors.Is(lr, python.ErrLegacy) {
		t.Fatal("LegacyResponse must unwrap to ErrLegacy")
	}
	if _, ok := python.AsLegacy(lr); !ok {
		t.Fatal("AsLegacy must recognise a LegacyResponse")
	}

	// Exercise the replay path the handler uses.
	rec := httptest.NewRecorder()
	writeRawJSONStatus(rec, lr.Status, lr.Body)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", rec.Code)
	}
	if got := rec.Body.String(); got != string(body) {
		t.Fatalf("body = %q, want the upstream body verbatim", got)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Fatalf("content-type = %q", ct)
	}
}

// TestLegacyResponseNotBoundary guards the classification itself: a legacy
// answer is neither a BoundaryError nor ErrUnavailable.
func TestLegacyResponseNotBoundary(t *testing.T) {
	lr := &python.LegacyResponse{Status: 422, Body: []byte(`{"detail":[]}`)}
	if errors.Is(lr, python.ErrBoundary) {
		t.Fatal("legacy answer must not classify as a boundary error")
	}
	if errors.Is(lr, python.ErrUnavailable) {
		t.Fatal("legacy answer must not classify as unavailable")
	}
	if _, ok := python.AsBoundary(lr); ok {
		t.Fatal("AsBoundary must reject a LegacyResponse")
	}
}

// TestProxyAttachesRoutingDecision pins the Wave 3 contract: every proxied
// response carries the dependency classification as X-NSE-Routing-Reason so
// the routing table is observable in flight. A route the table never
// classified reports "unclassified" - the safe default, never silence.
//
// The proxy is driven against a real python.Client bound to a stub upstream
// so the header is exercised on the actual forward path, not a mock of it.
func TestProxyAttachesRoutingDecision(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"success":true}`))
	}))
	defer upstream.Close()

	py := python.New(python.Options{Origin: upstream.URL})
	p := NewProxy(py)

	cases := []struct {
		method, path, want string
	}{
		// A real classified candidate (profiler: stateless 2xx).
		{"GET", "/api/algo/config", "stateless-2xx"},
		// A route outside the table must say so, not omit the header.
		{"GET", "/api/never-classified", "unclassified"},
		// A DB-backed route must not advertise itself as a candidate.
		{"GET", "/api/db/manage/validate", "observed:db"},
	}
	for _, c := range cases {
		rec := httptest.NewRecorder()
		p.Handler(c.method, c.path).ServeHTTP(
			rec, httptest.NewRequest(c.method, c.path, nil))
		if got := rec.Header().Get("X-NSE-Routing-Reason"); got != c.want {
			t.Errorf("Decide(%s %s): reason = %q, want %q", c.method, c.path, got, c.want)
		}
		if got := rec.Body.String(); !strings.Contains(got, `"success":true`) {
			t.Errorf("Decide(%s %s): body = %q, want the upstream payload forwarded",
				c.method, c.path, got)
		}
	}
}

// TestProxySSEStreamForwardedUnbuffered is the regression test for the live
// tick stream failing through the Go origin. Chrome's EventSource aborted with
// "response has a MIME type (application/json) that is not text/event-stream"
// because the buffered proxy read the whole SSE body with io.ReadAll (blocking
// forever on an intentionally open stream) and then served it with a hardcoded
// Content-Type: application/json. The stream path must instead pipe upstream
// bytes straight through with Python's own text/event-stream content type.
func TestProxySSEStreamForwardedUnbuffered(t *testing.T) {
	streamHits := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		streamHits++
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.WriteHeader(http.StatusOK)
		fl := w.(http.Flusher)
		for i := 0; i < 3; i++ {
			_, _ = w.Write([]byte("data: tick\n\n"))
			fl.Flush()
		}
	}))
	defer upstream.Close()

	py := python.New(python.Options{Origin: upstream.URL})
	p := NewProxy(py)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/api/ticks/stream", nil)
	req.Header.Set("Accept", "text/event-stream")
	p.Handler("GET", "/api/ticks/stream").ServeHTTP(rec, req)

	if got := rec.Header().Get("Content-Type"); got != "text/event-stream" {
		t.Errorf("Content-Type = %q, want text/event-stream (Python's own SSE type must reach the client)", got)
	}
	if got := rec.Header().Get("Cache-Control"); got != "no-cache" {
		t.Errorf("Cache-Control = %q, want no-cache", got)
	}
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	body := rec.Body.String()
	want := "data: tick\n\ndata: tick\n\ndata: tick\n\n"
	if body != want {
		t.Errorf("stream body = %q, want the upstream SSE bytes verbatim (%q)", body, want)
	}
	if streamHits != 1 {
		t.Errorf("upstream was hit %d times, want exactly 1 (one forwarded stream)", streamHits)
	}
}

// TestProxyLargePayloadNotTruncated is the regression test for the highest-
// impact bug found in the Wave 7 browser deep check: the research strategies
// endpoint emits ~15 MiB, the buffered proxy capped the upstream read at 8 MiB,
// the truncated body failed json.Unmarshal, and the client received a fabricated
// 503 DEPENDENCY_UNAVAILABLE — breaking the whole Research page through the Go
// origin. The cap is now sized far above any legitimate payload.
func TestProxyLargePayloadNotTruncated(t *testing.T) {
	// A valid v1 envelope whose data payload is 12 MiB — past the old 8 MiB
	// cap, well under the new 64 MiB guard.
	const n = 12 << 20
	big := strings.Repeat("a", n)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"data":{"blob":"` + big + `"},"meta":{"request_id":"req_x","generated_at":"2026-09-30T00:00:00+00:00"}}`))
	}))
	defer upstream.Close()

	py := python.New(python.Options{Origin: upstream.URL})
	p := NewProxy(py)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/api/v1/research/strategies", nil)
	p.Handler("GET", "/api/v1/research/strategies").ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d on a 12 MiB payload, want 200 (a legitimate upstream answer must not be classified as unavailable)", rec.Code)
	}
	if got := rec.Body.Len(); got < n {
		t.Errorf("forwarded body = %d bytes, want >= %d (the payload must arrive complete, not truncated)", got, n)
	}
	if !strings.Contains(rec.Body.String(), big) {
		t.Error("forwarded body lost the upstream payload")
	}
}

// TestProxyV1EnvelopeForwardedVerbatim pins the byte-level parity contract on
// the v1 surface: when Python already answered with {"data":...,"meta":...},
// Go must emit Python's exact bytes. Rewriting meta (request_id/generation
// time) or re-marshaling the body changes float formatting, key order, and
// correlation ids — the proxy re-marshal this replaces did all three.
func TestProxyV1EnvelopeForwardedVerbatim(t *testing.T) {
	// Deliberately non-alphabetical keys and a float formatting Go's encoder
	// would not reproduce identically.
	body := `{"data":{"zeta":1,"alpha":2.718281828459045,"nested":{"keep":"order"}},"meta":{"request_id":"req_py_origin","generated_at":"2026-09-30T01:02:03.456789+00:00","custom":"kept"}}`
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(body))
	}))
	defer upstream.Close()

	py := python.New(python.Options{Origin: upstream.URL})
	p := NewProxy(py)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/api/v1/risk/summary", nil)
	p.Handler("GET", "/api/v1/risk/summary").ServeHTTP(rec, req)

	if rec.Body.String() != body {
		t.Errorf("v1 body was rewritten\n got: %s\nwant: %s", rec.Body.String(), body)
	}
}

// registered candidates are served directly with target "go-direct",
// and when disabled or unhandled, route to Python.
func TestProxyDirectServing(t *testing.T) {
	upstreamCalls := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls++
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"data":{"upstream":true},"meta":{}}`))
	}))
	defer upstream.Close()

	py := python.New(python.Options{Origin: upstream.URL})
	p := NewProxy(py)

	// Register a direct handler for a known candidate route.
	p.RegisterDirect("GET", "/api/v1/features/contract", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-NSE-Routing-Target", "go-direct")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"direct":true}`))
	})

	// 1. Candidate with direct handler -> served directly without touching upstream.
	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/api/v1/features/contract", nil)
	p.Handler("GET", "/api/v1/features/contract").ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), `"direct":true`) {
		t.Fatalf("expected direct response, got %s", rec.Body.String())
	}
	if upstreamCalls != 0 {
		t.Fatalf("expected 0 upstream calls for direct serving, got %d", upstreamCalls)
	}

	// 2. Candidate with direct handler when disabled -> proxies to upstream.
	t.Setenv("NSE_GO_DIRECT_SERVING_DISABLE", "1")
	rec2 := httptest.NewRecorder()
	req2 := httptest.NewRequest("GET", "/api/v1/features/contract", nil)
	p.Handler("GET", "/api/v1/features/contract").ServeHTTP(rec2, req2)

	if rec2.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec2.Code)
	}
	if !strings.Contains(rec2.Body.String(), `"upstream":true`) {
		t.Fatalf("expected upstream response when disabled, got %s", rec2.Body.String())
	}
	if target := rec2.Header().Get("X-NSE-Routing-Target"); target != "python-proxy" {
		t.Fatalf("expected X-NSE-Routing-Target python-proxy, got %q", target)
	}
	if upstreamCalls != 1 {
		t.Fatalf("expected 1 upstream call, got %d", upstreamCalls)
	}
}
