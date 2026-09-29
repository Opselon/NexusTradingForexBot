package handlers

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/routing"
)

type capabilitiesResponseEnvelope struct {
	Data CapabilitiesData `json:"data"`
	Meta struct {
		RequestID   string `json:"request_id"`
		GeneratedAt string `json:"generated_at"`
	} `json:"meta"`
}

type versionResponseEnvelope struct {
	Data VersionData `json:"data"`
	Meta struct {
		RequestID   string `json:"request_id"`
		GeneratedAt string `json:"generated_at"`
	} `json:"meta"`
}

func TestDirectCapabilitiesFieldsAndEnvelope(t *testing.T) {
	h := NewSystemDirect(nil)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/system/capabilities", nil)

	h.Capabilities(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d (body: %s)", rec.Code, rec.Body.String())
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected Content-Type application/json, got %q", ct)
	}

	var env capabilitiesResponseEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("failed to decode capabilities envelope: %v", err)
	}

	// Validate Meta block
	if env.Meta.RequestID == "" {
		t.Errorf("meta.request_id must not be empty")
	}
	if env.Meta.GeneratedAt == "" {
		t.Errorf("meta.generated_at must not be empty")
	}

	// Validate Data block
	data := env.Data
	if data.APIVersion != "v1" {
		t.Errorf("expected api_version 'v1', got %q", data.APIVersion)
	}
	if data.Spec != "docs/api/API_PLATFORM_V1.md" {
		t.Errorf("expected spec 'docs/api/API_PLATFORM_V1.md', got %q", data.Spec)
	}
	if !data.ReadOnly {
		t.Errorf("expected read_only true, got %v", data.ReadOnly)
	}
	if data.DomainCount != 19 {
		t.Errorf("expected domain_count 19, got %d", data.DomainCount)
	}
	if data.EndpointCount != 182 {
		t.Errorf("expected endpoint_count 182, got %d", data.EndpointCount)
	}
	if data.GeneratedAt == "" {
		t.Errorf("data.generated_at must not be empty")
	}

	// Pagination validation
	p := data.Pagination
	if p.Model != "page" {
		t.Errorf("expected pagination model 'page', got %q", p.Model)
	}
	if p.PageParam != "page" {
		t.Errorf("expected page_param 'page', got %q", p.PageParam)
	}
	if p.PageSizeParam != "page_size" {
		t.Errorf("expected page_size_param 'page_size', got %q", p.PageSizeParam)
	}
	if p.MaxPageSize != 200 {
		t.Errorf("expected max_page_size 200, got %d", p.MaxPageSize)
	}

	// Domains validation (all 19 domains must be present and mapped to 1)
	expectedDomains := []string{
		"audit", "config", "database", "decisions", "execution",
		"features", "incidents", "indicators", "market", "marketplace",
		"model", "observability", "positions", "research", "risk",
		"runtime", "shadow", "signals", "system",
	}
	if len(data.Domains) != len(expectedDomains) {
		t.Errorf("expected %d domains, got %d", len(expectedDomains), len(data.Domains))
	}
	for _, domain := range expectedDomains {
		if val, exists := data.Domains[domain]; !exists || val != 1 {
			t.Errorf("domain %q missing or invalid value: exists=%v, val=%d", domain, exists, val)
		}
	}
}

func TestDirectVersionFieldsAndEnvelope(t *testing.T) {
	h := NewSystemDirect(nil)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil)

	h.Version(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d (body: %s)", rec.Code, rec.Body.String())
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected Content-Type application/json, got %q", ct)
	}

	var env versionResponseEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("failed to decode version envelope: %v", err)
	}

	// Validate Meta block
	if env.Meta.RequestID == "" {
		t.Errorf("meta.request_id must not be empty")
	}
	if env.Meta.GeneratedAt == "" {
		t.Errorf("meta.generated_at must not be empty")
	}

	// Validate Data block
	data := env.Data
	if data.Product != "NexusScalpEngine" {
		t.Errorf("expected product 'NexusScalpEngine', got %q", data.Product)
	}
	if data.ProductDisplay != "Nexus Trading Forex Bot" {
		t.Errorf("expected product_display 'Nexus Trading Forex Bot', got %q", data.ProductDisplay)
	}
	if data.Version != "9.0.14" {
		t.Errorf("expected version '9.0.14', got %q", data.Version)
	}
	if len(data.Commit) != 40 {
		t.Errorf("expected 40-character commit hash, got %q", data.Commit)
	}
	if data.CommitSource != "repository" {
		t.Errorf("expected commit_source 'repository', got %q", data.CommitSource)
	}
	if data.CommitStatus != "RELEASE_COMMIT" {
		t.Errorf("expected commit_status 'RELEASE_COMMIT', got %q", data.CommitStatus)
	}
	if data.Channel != "production" {
		t.Errorf("expected channel 'production', got %q", data.Channel)
	}
	if data.GeneratedAt == "" {
		t.Errorf("data.generated_at must not be empty")
	}
}

func TestCapabilitiesAndVersionCaching(t *testing.T) {
	h := NewSystemDirect(nil)

	// Test Capabilities caching
	req1 := httptest.NewRequest(http.MethodGet, "/api/v1/system/capabilities", nil)
	rec1 := httptest.NewRecorder()
	h.Capabilities(rec1, req1)
	var env1 capabilitiesResponseEnvelope
	_ = json.Unmarshal(rec1.Body.Bytes(), &env1)

	time.Sleep(10 * time.Millisecond)

	req2 := httptest.NewRequest(http.MethodGet, "/api/v1/system/capabilities", nil)
	rec2 := httptest.NewRecorder()
	h.Capabilities(rec2, req2)
	var env2 capabilitiesResponseEnvelope
	_ = json.Unmarshal(rec2.Body.Bytes(), &env2)

	// Cached data.generated_at should be identical
	if env1.Data.GeneratedAt != env2.Data.GeneratedAt {
		t.Errorf("expected cached data.generated_at to match: %q vs %q",
			env1.Data.GeneratedAt, env2.Data.GeneratedAt)
	}
	// Per-request meta.request_id should differ
	if env1.Meta.RequestID == env2.Meta.RequestID {
		t.Errorf("expected different request IDs per call, got identical: %q", env1.Meta.RequestID)
	}

	// Test Version caching
	vreq1 := httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil)
	vrec1 := httptest.NewRecorder()
	h.Version(vrec1, vreq1)
	var venv1 versionResponseEnvelope
	_ = json.Unmarshal(vrec1.Body.Bytes(), &venv1)

	time.Sleep(10 * time.Millisecond)

	vreq2 := httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil)
	vrec2 := httptest.NewRecorder()
	h.Version(vrec2, vreq2)
	var venv2 versionResponseEnvelope
	_ = json.Unmarshal(vrec2.Body.Bytes(), &venv2)

	if venv1.Data.GeneratedAt != venv2.Data.GeneratedAt {
		t.Errorf("expected cached version data.generated_at to match: %q vs %q",
			venv1.Data.GeneratedAt, venv2.Data.GeneratedAt)
	}
	if venv1.Meta.RequestID == venv2.Meta.RequestID {
		t.Errorf("expected different request IDs for version calls, got identical: %q", venv1.Meta.RequestID)
	}
}

func TestVersionFallbackToPython(t *testing.T) {
	mockPyServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/system/version" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": map[string]any{
				"product":         "NexusScalpEngine",
				"product_display": "Nexus Trading Forex Bot",
				"version":         "9.0.14",
				"commit":          "11223344556677889900aabbccddeeff11223344",
				"commit_source":   "python",
				"commit_status":   "RELEASE_COMMIT",
				"channel":         "production",
			},
			"meta": map[string]any{
				"request_id":   "req_mock_py",
				"generated_at": "2026-09-29T12:00:00.000000+00:00",
			},
		})
	}))
	defer mockPyServer.Close()

	py := python.New(python.Options{Origin: mockPyServer.URL})
	h := NewSystemDirect(py)
	// Simulate git resolution failure so it falls back to Python
	h.SetGitCommitResolver(func() (string, error) {
		return "", errors.New("git command failed")
	})

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil)
	h.Version(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200 on fallback, got %d", rec.Code)
	}

	var env versionResponseEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("failed to decode version: %v", err)
	}

	if env.Data.Commit != "11223344556677889900aabbccddeeff11223344" {
		t.Errorf("expected commit from Python fallback, got %q", env.Data.Commit)
	}
	if env.Data.CommitSource != "python" {
		t.Errorf("expected commit_source 'python', got %q", env.Data.CommitSource)
	}
}

func TestVersionFallbackToBinary(t *testing.T) {
	h := NewSystemDirect(nil)
	// Simulate git failure with no Python client configured
	h.SetGitCommitResolver(func() (string, error) {
		return "", errors.New("git unavailable")
	})

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil)
	h.Version(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}

	var env versionResponseEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("failed to unmarshal: %v", err)
	}

	if env.Data.Commit != "unknown" {
		t.Errorf("expected commit 'unknown', got %q", env.Data.Commit)
	}
	if env.Data.CommitSource != "binary" {
		t.Errorf("expected commit_source 'binary', got %q", env.Data.CommitSource)
	}
	if env.Data.CommitStatus != "NOT_RECORDED" {
		t.Errorf("expected commit_status 'NOT_RECORDED', got %q", env.Data.CommitStatus)
	}
	if env.Data.Product != "NexusScalpEngine" {
		t.Errorf("expected product 'NexusScalpEngine', got %q", env.Data.Product)
	}
	if env.Data.Version != "9.0.14" {
		t.Errorf("expected version '9.0.14', got %q", env.Data.Version)
	}
}

func TestCapabilitiesFallbackToPython(t *testing.T) {
	mockPyServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/system/capabilities" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": map[string]any{
				"api_version":    "v1",
				"spec":           "docs/api/API_PLATFORM_V1.md",
				"read_only":      true,
				"domains":        map[string]int{"audit": 1, "custom": 1},
				"domain_count":   2,
				"endpoint_count": 42,
				"pagination": map[string]any{
					"model":           "page",
					"page_param":      "page",
					"page_size_param": "page_size",
					"max_page_size":   100,
				},
				"generated_at": "2026-09-29T10:00:00.000000+00:00",
			},
			"meta": map[string]any{
				"request_id":   "req_mock_cap",
				"generated_at": "2026-09-29T10:00:00.000000+00:00",
			},
		})
	}))
	defer mockPyServer.Close()

	py := python.New(python.Options{Origin: mockPyServer.URL})
	h := NewSystemDirect(py)
	h.SetForceFallback(true)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/system/capabilities", nil)
	h.Capabilities(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}

	var env capabilitiesResponseEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("failed to decode capabilities: %v", err)
	}

	if env.Data.EndpointCount != 42 {
		t.Errorf("expected endpoint_count 42 from Python fallback, got %d", env.Data.EndpointCount)
	}
	if env.Data.DomainCount != 2 {
		t.Errorf("expected domain_count 2 from Python fallback, got %d", env.Data.DomainCount)
	}
}

func TestDirectServingDisabledEnv(t *testing.T) {
	mockPyServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": map[string]any{
				"product":         "NexusScalpEngine",
				"product_display": "Nexus Trading Forex Bot",
				"version":         "9.0.14",
				"commit":          "disabled_env_commit",
				"commit_source":   "python",
				"commit_status":   "RELEASE_COMMIT",
				"channel":         "production",
			},
			"meta": map[string]any{
				"request_id":   "req_disabled_env",
				"generated_at": "2026-09-29T10:00:00.000000+00:00",
			},
		})
	}))
	defer mockPyServer.Close()

	_ = os.Setenv(routing.EnvDirectServingDisable, "1")
	defer func() { _ = os.Unsetenv(routing.EnvDirectServingDisable) }()

	py := python.New(python.Options{Origin: mockPyServer.URL})
	h := NewSystemDirect(py)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil)
	h.Version(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}

	var env versionResponseEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("failed to decode: %v", err)
	}

	if env.Data.Commit != "disabled_env_commit" {
		t.Errorf("expected commit from Python fallback when direct serving disabled, got %q", env.Data.Commit)
	}
}

func TestSystemHandlersDirectMethods(t *testing.T) {
	sys := NewSystem(nil)

	// Test CapabilitiesDirect
	recCap := httptest.NewRecorder()
	reqCap := httptest.NewRequest(http.MethodGet, "/api/v1/system/capabilities", nil)
	sys.CapabilitiesDirect(recCap, reqCap)

	if recCap.Code != http.StatusOK {
		t.Errorf("CapabilitiesDirect: expected 200, got %d", recCap.Code)
	}
	var envCap capabilitiesResponseEnvelope
	if err := json.Unmarshal(recCap.Body.Bytes(), &envCap); err != nil {
		t.Errorf("CapabilitiesDirect: unmarshal error: %v", err)
	}
	if envCap.Data.EndpointCount != 182 {
		t.Errorf("CapabilitiesDirect: expected endpoint_count 182, got %d", envCap.Data.EndpointCount)
	}

	// Test VersionDirect
	recVer := httptest.NewRecorder()
	reqVer := httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil)
	sys.VersionDirect(recVer, reqVer)

	if recVer.Code != http.StatusOK {
		t.Errorf("VersionDirect: expected 200, got %d", recVer.Code)
	}
	var envVer versionResponseEnvelope
	if err := json.Unmarshal(recVer.Body.Bytes(), &envVer); err != nil {
		t.Errorf("VersionDirect: unmarshal error: %v", err)
	}
	if envVer.Data.Product != "NexusScalpEngine" {
		t.Errorf("VersionDirect: expected product NexusScalpEngine, got %q", envVer.Data.Product)
	}

	// Test standalone handlers
	capHandler := SystemCapabilitiesDirectHandler(nil)
	recCap2 := httptest.NewRecorder()
	capHandler(recCap2, reqCap)
	if recCap2.Code != http.StatusOK {
		t.Errorf("SystemCapabilitiesDirectHandler: expected 200, got %d", recCap2.Code)
	}

	verHandler := SystemVersionDirectHandler(nil)
	recVer2 := httptest.NewRecorder()
	verHandler(recVer2, reqVer)
	if recVer2.Code != http.StatusOK {
		t.Errorf("SystemVersionDirectHandler: expected 200, got %d", recVer2.Code)
	}
}
