// Package handlers tests for the generic proxy.
package handlers

import (
	"errors"
	"net/http"
	"net/http/httptest"
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
