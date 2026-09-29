package routing

import (
	"bytes"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRouteTargeting(t *testing.T) {
	// Ensure direct serving is enabled for this test
	t.Setenv(EnvDirectServingDisable, "")

	tests := []struct {
		name       string
		method     string
		path       string
		wantTarget Target
	}{
		{
			name:       "stateless candidate routes directly to Go",
			method:     "GET",
			path:       "/api/algo/config",
			wantTarget: TargetGoDirect,
		},
		{
			name:       "restart-matrix candidate routes directly to Go",
			method:     "GET",
			path:       "/api/ai-providers/restart-matrix",
			wantTarget: TargetGoDirect,
		},
		{
			name:       "observed:db route routes to Python proxy",
			method:     "GET",
			path:       "/api/db/manage/validate",
			wantTarget: TargetPythonProxy,
		},
		{
			name:       "stub-in-2xx route routes to Python proxy",
			method:     "GET",
			path:       "/api/account/drawdown",
			wantTarget: TargetPythonProxy,
		},
		{
			name:       "write route routes to Python proxy",
			method:     "POST",
			path:       "/api/db/manage/backup",
			wantTarget: TargetPythonProxy,
		},
		{
			name:       "unknown route routes to Python proxy",
			method:     "GET",
			path:       "/api/not-in-table-surface",
			wantTarget: TargetPythonProxy,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := Route(tt.method, tt.path)
			if got != tt.wantTarget {
				t.Errorf("Route(%s, %s) = %q, want %q", tt.method, tt.path, got, tt.wantTarget)
			}
		})
	}
}

func TestTargetStringValues(t *testing.T) {
	if TargetGoDirect.String() != "go-direct" {
		t.Errorf("TargetGoDirect.String() = %q, want %q", TargetGoDirect.String(), "go-direct")
	}
	if TargetPythonProxy.String() != "python-proxy" {
		t.Errorf("TargetPythonProxy.String() = %q, want %q", TargetPythonProxy.String(), "python-proxy")
	}
	if TargetPythonFallback.String() != "python-fallback" {
		t.Errorf("TargetPythonFallback.String() = %q, want %q", TargetPythonFallback.String(), "python-fallback")
	}
}

func TestDirectServingDisableSwitch(t *testing.T) {
	// Baseline: enabled
	t.Setenv(EnvDirectServingDisable, "")
	if !DirectServingEnabled() {
		t.Error("DirectServingEnabled() = false, want true when env is empty")
	}
	if got := Route("GET", "/api/algo/config"); got != TargetGoDirect {
		t.Errorf("Route with direct serving enabled = %q, want %q", got, TargetGoDirect)
	}

	// Disabled via "1"
	t.Setenv(EnvDirectServingDisable, "1")
	if DirectServingEnabled() {
		t.Error("DirectServingEnabled() = true, want false when env=1")
	}
	if got := Route("GET", "/api/algo/config"); got != TargetPythonProxy {
		t.Errorf("Route with env=1 = %q, want %q", got, TargetPythonProxy)
	}

	// Disabled via "true"
	t.Setenv(EnvDirectServingDisable, "true")
	if DirectServingEnabled() {
		t.Error("DirectServingEnabled() = true, want false when env=true")
	}
	if got := Route("GET", "/api/algo/config"); got != TargetPythonProxy {
		t.Errorf("Route with env=true = %q, want %q", got, TargetPythonProxy)
	}

	// Disabled via uppercase "TRUE"
	t.Setenv(EnvDirectServingDisable, "TRUE")
	if DirectServingEnabled() {
		t.Error("DirectServingEnabled() = true, want false when env=TRUE")
	}

	// Disabled via padded " 1 "
	t.Setenv(EnvDirectServingDisable, " 1 ")
	if DirectServingEnabled() {
		t.Error("DirectServingEnabled() = true, want false when env=' 1 '")
	}

	// Explicitly enabled via "0"
	t.Setenv(EnvDirectServingDisable, "0")
	if !DirectServingEnabled() {
		t.Error("DirectServingEnabled() = false, want true when env=0")
	}
	if got := Route("GET", "/api/algo/config"); got != TargetGoDirect {
		t.Errorf("Route with env=0 = %q, want %q", got, TargetGoDirect)
	}

	// Explicitly enabled via "false"
	t.Setenv(EnvDirectServingDisable, "false")
	if !DirectServingEnabled() {
		t.Error("DirectServingEnabled() = false, want true when env=false")
	}
}

func TestSetRoutingHeaders(t *testing.T) {
	// Nil writer safe
	SetRoutingHeaders(nil, TargetGoDirect, "some-reason")

	rec := httptest.NewRecorder()
	SetRoutingHeaders(rec, TargetGoDirect, "stateless-2xx")

	if got := rec.Header().Get(HeaderRoutingTarget); got != "go-direct" {
		t.Errorf("Header %s = %q, want %q", HeaderRoutingTarget, got, "go-direct")
	}
	if got := rec.Header().Get(HeaderRoutingReason); got != "stateless-2xx" {
		t.Errorf("Header %s = %q, want %q", HeaderRoutingReason, got, "stateless-2xx")
	}

	// Update to fallback
	SetRoutingHeaders(rec, TargetPythonFallback, "direct-failed")
	if got := rec.Header().Get(HeaderRoutingTarget); got != "python-fallback" {
		t.Errorf("Header %s = %q, want %q", HeaderRoutingTarget, got, "python-fallback")
	}
	if got := rec.Header().Get(HeaderRoutingReason); got != "direct-failed" {
		t.Errorf("Header %s = %q, want %q", HeaderRoutingReason, got, "direct-failed")
	}

	// Empty reason does not set or wipe
	rec2 := httptest.NewRecorder()
	SetRoutingHeaders(rec2, TargetPythonProxy, "")
	if got := rec2.Header().Get(HeaderRoutingTarget); got != "python-proxy" {
		t.Errorf("Header %s = %q, want %q", HeaderRoutingTarget, got, "python-proxy")
	}
	if got := rec2.Header().Get(HeaderRoutingReason); got != "" {
		t.Errorf("Header %s = %q, want empty", HeaderRoutingReason, got)
	}
}

func TestServeWithFallback_DirectSuccess(t *testing.T) {
	ResetFallbackTotal()

	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/api/algo/config", nil)

	fallbackCalled := false
	directFn := func(w http.ResponseWriter, r *http.Request) error {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, err := w.Write([]byte(`{"status":"direct-ok"}`))
		return err
	}
	fallbackFn := func(w http.ResponseWriter, r *http.Request) {
		fallbackCalled = true
	}

	ServeWithFallback(rec, req, directFn, fallbackFn)

	if fallbackCalled {
		t.Error("fallbackFn was called on directFn success, want not called")
	}
	if got := rec.Header().Get(HeaderRoutingTarget); got != "go-direct" {
		t.Errorf("Header %s = %q, want %q", HeaderRoutingTarget, got, "go-direct")
	}
	if got := rec.Header().Get(HeaderRoutingReason); got != "stateless-2xx" {
		t.Errorf("Header %s = %q, want %q", HeaderRoutingReason, got, "stateless-2xx")
	}
	if rec.Code != http.StatusOK {
		t.Errorf("Code = %d, want %d", rec.Code, http.StatusOK)
	}
	if body := rec.Body.String(); body != `{"status":"direct-ok"}` {
		t.Errorf("Body = %q, want %q", body, `{"status":"direct-ok"}`)
	}
	if FallbackTotal() != 0 {
		t.Errorf("FallbackTotal() = %d, want 0", FallbackTotal())
	}
}

func TestServeWithFallback_DirectError(t *testing.T) {
	ResetFallbackTotal()

	rec := httptest.NewRecorder()
	// Pre-set header simulating direct serving target
	rec.Header().Set(HeaderRoutingTarget, "go-direct")
	req := httptest.NewRequest("GET", "/api/algo/config", nil)

	fallbackCalled := false
	directFn := func(w http.ResponseWriter, r *http.Request) error {
		// Dirty write that should be discarded
		w.Header().Set("X-Dirty-Header", "dirty")
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("dirty partial failure output"))
		return errors.New("simulated direct serving failure")
	}
	fallbackFn := func(w http.ResponseWriter, r *http.Request) {
		fallbackCalled = true
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"fallback-ok"}`))
	}

	ServeWithFallback(rec, req, directFn, fallbackFn)

	if !fallbackCalled {
		t.Error("fallbackFn was not called on directFn error, want called")
	}
	// Verify header is updated from go-direct to python-fallback
	if got := rec.Header().Get(HeaderRoutingTarget); got != "python-fallback" {
		t.Errorf("Header %s = %q, want %q", HeaderRoutingTarget, got, "python-fallback")
	}
	// Verify reason is preserved or populated
	if got := rec.Header().Get(HeaderRoutingReason); got != "stateless-2xx" {
		t.Errorf("Header %s = %q, want %q", HeaderRoutingReason, got, "stateless-2xx")
	}
	// Verify dirty header was discarded
	if dirty := rec.Header().Get("X-Dirty-Header"); dirty != "" {
		t.Errorf("X-Dirty-Header = %q, want empty (discarded)", dirty)
	}
	// Verify body and status code reflect fallbackFn
	if rec.Code != http.StatusOK {
		t.Errorf("Code = %d, want %d", rec.Code, http.StatusOK)
	}
	if body := rec.Body.String(); body != `{"status":"fallback-ok"}` {
		t.Errorf("Body = %q, want %q", body, `{"status":"fallback-ok"}`)
	}
	if FallbackTotal() != 1 {
		t.Errorf("FallbackTotal() = %d, want 1", FallbackTotal())
	}
}

func TestServeWithFallback_DirectPanic(t *testing.T) {
	ResetFallbackTotal()

	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/api/algo/config", nil)

	fallbackCalled := false
	directFn := func(w http.ResponseWriter, r *http.Request) error {
		panic("unexpected crash inside direct handler")
	}
	fallbackFn := func(w http.ResponseWriter, r *http.Request) {
		fallbackCalled = true
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("recovered-by-fallback"))
	}

	// Must not panic the caller
	ServeWithFallback(rec, req, directFn, fallbackFn)

	if !fallbackCalled {
		t.Error("fallbackFn was not called on panic, want called")
	}
	if got := rec.Header().Get(HeaderRoutingTarget); got != "python-fallback" {
		t.Errorf("Header %s = %q, want %q", HeaderRoutingTarget, got, "python-fallback")
	}
	if body := rec.Body.String(); body != "recovered-by-fallback" {
		t.Errorf("Body = %q, want %q", body, "recovered-by-fallback")
	}
	if FallbackTotal() != 1 {
		t.Errorf("FallbackTotal() = %d, want 1", FallbackTotal())
	}
}

func TestServeWithFallback_PreservesRequestBody(t *testing.T) {
	ResetFallbackTotal()

	reqBody := `{"action":"test-body"}`
	rec := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/api/algo/config", bytes.NewBufferString(reqBody))

	var readInDirect string
	var readInFallback string

	directFn := func(w http.ResponseWriter, r *http.Request) error {
		b, err := io.ReadAll(r.Body)
		if err != nil {
			return err
		}
		readInDirect = string(b)
		return errors.New("failing direct execution after reading body")
	}

	fallbackFn := func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		readInFallback = string(b)
		w.WriteHeader(http.StatusOK)
	}

	ServeWithFallback(rec, req, directFn, fallbackFn)

	if readInDirect != reqBody {
		t.Errorf("direct read = %q, want %q", readInDirect, reqBody)
	}
	if readInFallback != reqBody {
		t.Errorf("fallback read = %q, want %q", readInFallback, reqBody)
	}
	if got := rec.Header().Get(HeaderRoutingTarget); got != "python-fallback" {
		t.Errorf("Header %s = %q, want %q", HeaderRoutingTarget, got, "python-fallback")
	}
}

func TestServeWithFallback_NilSafety(t *testing.T) {
	// Should return safely when w is nil
	ServeWithFallback(nil, nil, nil, nil)
}
