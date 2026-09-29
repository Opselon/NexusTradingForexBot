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

// TestProxyDirectServing exercises Wave 4 direct serving on the proxy handler:
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
