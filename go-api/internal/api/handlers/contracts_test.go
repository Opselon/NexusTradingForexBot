// Package handlers tests direct Go serving for feature contracts and config schema.
package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/routing"
	"github.com/Opselon/NexusTradingForexBot/go-api/pkg/contracts"
)

type v1Envelope[T any] struct {
	Data T `json:"data"`
	Meta struct {
		RequestID   string `json:"request_id"`
		GeneratedAt string `json:"generated_at"`
	} `json:"meta"`
}

func TestFeatureContractDirect(t *testing.T) {
	h := NewContracts(nil)

	for _, tc := range []struct {
		name    string
		handler http.HandlerFunc
	}{
		{"FeatureContract", h.FeatureContract},
		{"ContractAlias", h.Contract},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			req := httptest.NewRequest(http.MethodGet, "/api/v1/features/contract", nil)

			tc.handler(w, req)

			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, want %d", w.Code, http.StatusOK)
			}

			ct := w.Header().Get("Content-Type")
			if !strings.HasPrefix(ct, "application/json") {
				t.Errorf("Content-Type = %q, want application/json", ct)
			}

			target := w.Header().Get(routing.HeaderRoutingTarget)
			if target != string(routing.TargetGoDirect) {
				t.Errorf("routing target = %q, want %q", target, routing.TargetGoDirect)
			}

			reason := w.Header().Get(routing.HeaderRoutingReason)
			if reason != "stateless-2xx" {
				t.Errorf("routing reason = %q, want %q", reason, "stateless-2xx")
			}

			var env v1Envelope[FeatureContractData]
			if err := json.Unmarshal(w.Body.Bytes(), &env); err != nil {
				t.Fatalf("unmarshal error: %v; body: %s", err, w.Body.String())
			}

			if !strings.HasPrefix(env.Meta.RequestID, "req_") {
				t.Errorf("meta.request_id = %q, want req_ prefix", env.Meta.RequestID)
			}
			if env.Meta.GeneratedAt == "" {
				t.Error("meta.generated_at is empty")
			}

			d := env.Data
			if d.SchemaID != "scalp_v3" {
				t.Errorf("schema_id = %q, want %q", d.SchemaID, "scalp_v3")
			}
			if d.FeatureCount != 70 {
				t.Errorf("feature_count = %d, want 70", d.FeatureCount)
			}
			if d.FeatureSchemaHash != "235b8fccc96b7e0e" {
				t.Errorf("feature_schema_hash = %q, want %q", d.FeatureSchemaHash, "235b8fccc96b7e0e")
			}
			if !d.RegistryCanonical {
				t.Errorf("registry_canonical = %v, want true", d.RegistryCanonical)
			}

			// Groups validation
			if d.Groups.Base0To49.Count != 0 {
				t.Errorf("groups.base_0_49.count = %d, want 0", d.Groups.Base0To49.Count)
			}
			if d.Groups.Base0To49.Indices == nil {
				t.Error("groups.base_0_49.indices is nil, want non-nil empty slice")
			}
			if d.Groups.News50To59.Count != 0 {
				t.Errorf("groups.news_50_59.count = %d, want 0", d.Groups.News50To59.Count)
			}
			if d.Groups.News50To59.Indices == nil {
				t.Error("groups.news_50_59.indices is nil, want non-nil empty slice")
			}
			if d.Groups.Liquidity60To69.Count != 0 {
				t.Errorf("groups.liquidity_60_69.count = %d, want 0", d.Groups.Liquidity60To69.Count)
			}
			if d.Groups.Liquidity60To69.Indices == nil {
				t.Error("groups.liquidity_60_69.indices is nil, want non-nil empty slice")
			}

			// Ensure raw JSON contains [] and not null
			bodyStr := w.Body.String()
			if strings.Contains(bodyStr, `"indices":null`) {
				t.Errorf("serialized body has null indices: %s", bodyStr)
			}
			if !strings.Contains(bodyStr, `"indices":[]`) {
				t.Errorf("serialized body missing empty array indices: %s", bodyStr)
			}

			// First 10 names
			if len(d.First10Names) != 10 {
				t.Fatalf("first_10_names len = %d, want 10", len(d.First10Names))
			}
			if d.First10Names[0] != "upper_wick_ratio" {
				t.Errorf("first_10_names[0] = %q, want %q", d.First10Names[0], "upper_wick_ratio")
			}
			if d.First10Names[9] != "rapid_reversal_spike_val" {
				t.Errorf("first_10_names[9] = %q, want %q", d.First10Names[9], "rapid_reversal_spike_val")
			}
		})
	}
}

func TestFeatureGroupsDirect(t *testing.T) {
	h := NewContracts(nil)

	for _, tc := range []struct {
		name    string
		handler http.HandlerFunc
	}{
		{"FeatureGroups", h.FeatureGroups},
		{"GroupsAlias", h.Groups},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			req := httptest.NewRequest(http.MethodGet, "/api/v1/features/groups", nil)

			tc.handler(w, req)

			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, want %d", w.Code, http.StatusOK)
			}

			target := w.Header().Get(routing.HeaderRoutingTarget)
			if target != string(routing.TargetGoDirect) {
				t.Errorf("routing target = %q, want %q", target, routing.TargetGoDirect)
			}

			reason := w.Header().Get(routing.HeaderRoutingReason)
			if reason != "stateless-2xx" {
				t.Errorf("routing reason = %q, want %q", reason, "stateless-2xx")
			}

			var env v1Envelope[FeatureGroupsData]
			if err := json.Unmarshal(w.Body.Bytes(), &env); err != nil {
				t.Fatalf("unmarshal error: %v", err)
			}

			d := env.Data
			if d.Dimension != 70 {
				t.Errorf("dimension = %d, want 70", d.Dimension)
			}

			// Base family: 50 features
			if d.Families.Base.Count != 50 {
				t.Errorf("families.base.count = %d, want 50", d.Families.Base.Count)
			}
			if len(d.Families.Base.Names) != 50 {
				t.Errorf("families.base.names len = %d, want 50", len(d.Families.Base.Names))
			}
			if d.Families.Base.Names[0] != "upper_wick_ratio" {
				t.Errorf("base[0] = %q, want upper_wick_ratio", d.Families.Base.Names[0])
			}
			if d.Families.Base.Names[49] != "feat_ob_fib_50_60_alignment" {
				t.Errorf("base[49] = %q, want feat_ob_fib_50_60_alignment", d.Families.Base.Names[49])
			}

			// Liquidity family: 10 features
			if d.Families.Liquidity.Count != 10 {
				t.Errorf("families.liquidity.count = %d, want 10", d.Families.Liquidity.Count)
			}
			if len(d.Families.Liquidity.Names) != 10 {
				t.Errorf("families.liquidity.names len = %d, want 10", len(d.Families.Liquidity.Names))
			}
			if d.Families.Liquidity.Names[0] != "bsl_distance_atr" {
				t.Errorf("liquidity[0] = %q, want bsl_distance_atr", d.Families.Liquidity.Names[0])
			}
			if d.Families.Liquidity.Names[9] != "post_sweep_displacement" {
				t.Errorf("liquidity[9] = %q, want post_sweep_displacement", d.Families.Liquidity.Names[9])
			}

			// News family: 10 features
			if d.Families.News.Count != 10 {
				t.Errorf("families.news.count = %d, want 10", d.Families.News.Count)
			}
			if len(d.Families.News.Names) != 10 {
				t.Errorf("families.news.names len = %d, want 10", len(d.Families.News.Names))
			}
			if d.Families.News.Names[0] != "active_high_impact_events" {
				t.Errorf("news[0] = %q, want active_high_impact_events", d.Families.News.Names[0])
			}
			if d.Families.News.Names[9] != "news_state" {
				t.Errorf("news[9] = %q, want news_state", d.Families.News.Names[9])
			}
		})
	}
}

func TestConfigSchemaDirect(t *testing.T) {
	h := NewContracts(nil)

	for _, tc := range []struct {
		name    string
		handler http.HandlerFunc
	}{
		{"ConfigSchema", h.ConfigSchema},
		{"SchemaAlias", h.Schema},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			req := httptest.NewRequest(http.MethodGet, "/api/v1/config/schema", nil)

			tc.handler(w, req)

			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, want %d", w.Code, http.StatusOK)
			}

			target := w.Header().Get(routing.HeaderRoutingTarget)
			if target != string(routing.TargetGoDirect) {
				t.Errorf("routing target = %q, want %q", target, routing.TargetGoDirect)
			}

			reason := w.Header().Get(routing.HeaderRoutingReason)
			if reason != "stateless-2xx" {
				t.Errorf("routing reason = %q, want %q", reason, "stateless-2xx")
			}

			var env v1Envelope[ConfigSchemaData]
			if err := json.Unmarshal(w.Body.Bytes(), &env); err != nil {
				t.Fatalf("unmarshal error: %v", err)
			}

			d := env.Data
			if d.Count != 14 {
				t.Errorf("count = %d, want 14", d.Count)
			}
			if len(d.Sections) != 14 {
				t.Fatalf("sections len = %d, want 14", len(d.Sections))
			}
			if d.At == "" {
				t.Error("at timestamp is empty")
			}

			// Validate key sections
			sectionMap := make(map[string]ConfigSectionSchema)
			for _, s := range d.Sections {
				sectionMap[s.Name] = s
			}

			exec, ok := sectionMap["execution"]
			if !ok {
				t.Fatal("section 'execution' missing")
			}
			if exec.Required {
				t.Errorf("execution.required = %v, want false", exec.Required)
			}
			if exec.Type != "ExecutionConfig" {
				t.Errorf("execution.type = %q, want ExecutionConfig", exec.Type)
			}
			if len(exec.Fields) != 6 {
				t.Errorf("execution.fields len = %d, want 6", len(exec.Fields))
			}

			risk, ok := sectionMap["risk"]
			if !ok {
				t.Fatal("section 'risk' missing")
			}
			if risk.Type != "RiskConfig" {
				t.Errorf("risk.type = %q, want RiskConfig", risk.Type)
			}
			if len(risk.Fields) != 7 {
				t.Errorf("risk.fields len = %d, want 7", len(risk.Fields))
			}

			algo, ok := sectionMap["algo"]
			if !ok {
				t.Fatal("section 'algo' missing")
			}
			if len(algo.Fields) != 29 {
				t.Errorf("algo.fields len = %d, want 29", len(algo.Fields))
			}

			news, ok := sectionMap["news"]
			if !ok {
				t.Fatal("section 'news' missing")
			}
			if news.Type != "nexus_scalp.news.config.NewsConfig | None" {
				t.Errorf("news.type = %q, want nexus_scalp.news.config.NewsConfig | None", news.Type)
			}
			if len(news.Fields) != 0 {
				t.Errorf("news.fields len = %d, want 0", len(news.Fields))
			}

			// Check raw JSON: sections without fields must omit "fields" key
			bodyStr := w.Body.String()
			// Unmarshal into generic slice of maps to inspect raw keys
			var rawMap struct {
				Data struct {
					Sections []map[string]any `json:"sections"`
				} `json:"data"`
			}
			if err := json.Unmarshal([]byte(bodyStr), &rawMap); err != nil {
				t.Fatalf("raw map unmarshal: %v", err)
			}

			for _, s := range rawMap.Data.Sections {
				name, _ := s["name"].(string)
				_, hasFields := s["fields"]
				switch name {
				case "news", "candle_intel", "forensic_report", "database_hygiene", "storage", "learning":
					if hasFields {
						t.Errorf("section %q has unexpected 'fields' key in raw JSON: %v", name, s["fields"])
					}
				case "execution", "risk", "paper_data", "telegram", "mt5", "model", "algo", "freshness":
					if !hasFields {
						t.Errorf("section %q missing 'fields' key in raw JSON", name)
					}
				}
			}
		})
	}
}

func TestFallbackToPythonWhenUnconfigured(t *testing.T) {
	h := NewContracts(nil, WithForceFallback(true))

	for _, tc := range []struct {
		name string
		fn   http.HandlerFunc
		path string
	}{
		{"FeatureContract", h.FeatureContract, "/api/v1/features/contract"},
		{"FeatureGroups", h.FeatureGroups, "/api/v1/features/groups"},
		{"ConfigSchema", h.ConfigSchema, "/api/v1/config/schema"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			req := httptest.NewRequest(http.MethodGet, tc.path, nil)

			tc.fn(w, req)

			if w.Code != http.StatusServiceUnavailable {
				t.Errorf("status = %d, want 503", w.Code)
			}

			target := w.Header().Get(routing.HeaderRoutingTarget)
			if target != string(routing.TargetPythonFallback) {
				t.Errorf("routing target = %q, want %q", target, routing.TargetPythonFallback)
			}

			var env contracts.ErrorEnvelope
			if err := json.Unmarshal(w.Body.Bytes(), &env); err != nil {
				t.Fatalf("unmarshal error: %v", err)
			}
			if env.Error.Code != contracts.CodeDependencyUnavailable {
				t.Errorf("error code = %q, want DEPENDENCY_UNAVAILABLE", env.Error.Code)
			}
			if env.Error.Message != "schema contract unavailable" {
				t.Errorf("error message = %q, want 'schema contract unavailable'", env.Error.Message)
			}
		})
	}
}

func TestFallbackToPythonWithMockServer(t *testing.T) {
	mockPayload := `{"data":{"proxied":true,"note":"from python backend"},"meta":{"request_id":"req_mock_123","generated_at":"2026-09-29T12:00:00+00:00"}}`

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(mockPayload))
	}))
	defer srv.Close()

	opts := python.Options{
		Origin:  srv.URL,
		Timeout: 2 * time.Second,
	}
	pyClient := python.New(opts)

	h := NewContracts(pyClient, WithForceFallback(true))

	for _, tc := range []struct {
		name string
		fn   http.HandlerFunc
		path string
	}{
		{"FeatureContract", h.FeatureContract, "/api/v1/features/contract"},
		{"FeatureGroups", h.FeatureGroups, "/api/v1/features/groups"},
		{"ConfigSchema", h.ConfigSchema, "/api/v1/config/schema"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			req := httptest.NewRequest(http.MethodGet, tc.path, nil)

			tc.fn(w, req)

			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200", w.Code)
			}

			target := w.Header().Get(routing.HeaderRoutingTarget)
			if target != string(routing.TargetPythonFallback) {
				t.Errorf("routing target = %q, want %q", target, routing.TargetPythonFallback)
			}

			var env struct {
				Data struct {
					Proxied bool   `json:"proxied"`
					Note    string `json:"note"`
				} `json:"data"`
			}
			if err := json.Unmarshal(w.Body.Bytes(), &env); err != nil {
				t.Fatalf("unmarshal error: %v; body: %s", err, w.Body.String())
			}
			if !env.Data.Proxied {
				t.Errorf("proxied = %v, want true", env.Data.Proxied)
			}
			if env.Data.Note != "from python backend" {
				t.Errorf("note = %q, want 'from python backend'", env.Data.Note)
			}
		})
	}
}

func TestConstructorWithOptions(t *testing.T) {
	h1 := NewContracts(nil)
	if h1.py != nil || h1.forceFallback {
		t.Errorf("h1 has unexpected state: %+v", h1)
	}

	opts := python.Options{Origin: "http://127.0.0.1:9999"}
	py := python.New(opts)
	h2 := NewContracts(nil, WithPythonClient(py), WithForceFallback(true))
	if h2.py != py {
		t.Errorf("h2.py not set correctly")
	}
	if !h2.forceFallback {
		t.Errorf("h2.forceFallback = %v, want true", h2.forceFallback)
	}
}
