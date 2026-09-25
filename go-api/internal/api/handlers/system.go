// Package handlers implements the NSE API endpoints. Phase A: system domain.
//
// CONTRACT NOTE — every handler here is a thin adapter, never a fact factory.
// The authoritative facts (HealthEngine sweep, build metadata, engine
// attachment) are produced by the Python runtime; Go fetches them over the
// bounded python.Client and re-envelopes them in the v1 contract. A Python
// failure becomes a v1 DEPENDENCY_UNAVAILABLE envelope, never a fabricated
// "healthy" verdict — that distinction is the whole point of the boundary.
package handlers

import (
	"errors"
	"net/http"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/respond"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
	"github.com/Opselon/NexusTradingForexBot/go-api/pkg/contracts"
)

// SystemHandlers serves the /api/v1/system/* domain (10 operations).
type SystemHandlers struct {
	py *python.Client
}

// NewSystem builds the handlers with a Python boundary client.
// py may be unconfigured (no engine attached): handlers then report
// DEPENDENCY_UNAVAILABLE instead of inventing state.
func NewSystem(py *python.Client) *SystemHandlers {
	return &SystemHandlers{py: py}
}

// The Python contract types we proxy. Field names are the EXECUTABLE ones.
type pyHealth struct {
	Verdict          string           `json:"verdict"`
	Checks           []map[string]any `json:"checks"`
	CriticalFailures []string         `json:"critical_failures"`
}

type pyVersion struct {
	Product string `json:"product"`
	Version string `json:"version"`
	Commit  string `json:"commit"`
	Channel string `json:"channel"`
}

type pyEnvelope[T any] struct {
	Data T              `json:"data"`
	Meta map[string]any `json:"meta"`
}

// required layers for /readiness, mirrored from system_readiness().
var readinessRequired = map[string]bool{
	"SYSTEM":         true,
	"RUNTIME":        true,
	"CONFIGURATION":  true,
	"DATABASE":       true,
	"MODEL":          true,
	"FEATURE_SCHEMA": true,
}

// Health: GET /api/v1/system/health
func (h *SystemHandlers) Health(w http.ResponseWriter, r *http.Request) {
	var env pyEnvelope[pyHealth]
	if err := h.py.GetJSON(r.Context(), "/api/v1/system/health", &env); err != nil {
		respond.FailDependencyUnavailable(w, r, "health engine unavailable")
		return
	}
	respond.OK(w, r, env.Data)
}

// Status: GET /api/v1/system/status
func (h *SystemHandlers) Status(w http.ResponseWriter, r *http.Request) {
	var env pyEnvelope[map[string]any]
	if err := h.py.GetJSON(r.Context(), "/api/v1/system/status", &env); err != nil {
		respond.FailDependencyUnavailable(w, r, "health engine unavailable")
		return
	}
	respond.OK(w, r, env.Data)
}

// Readiness: GET /api/v1/system/readiness
// Computed from the health block: ready unless verdict is NOT READY or any
// REQUIRED layer FAILED. Mirrors system_readiness() exactly.
func (h *SystemHandlers) Readiness(w http.ResponseWriter, r *http.Request) {
	var env pyEnvelope[pyHealth]
	if err := h.py.GetJSON(r.Context(), "/api/v1/system/health", &env); err != nil {
		respond.FailDependencyUnavailable(w, r, "health engine unavailable")
		return
	}
	blk := env.Data
	ready := blk.Verdict != "NOT READY"
	var required, optional []map[string]any
	for _, c := range blk.Checks {
		cat, _ := c["category"].(string)
		if readinessRequired[cat] {
			required = append(required, c)
			if v, _ := c["verdict"].(string); v == "FAIL" {
				ready = false
			}
		} else {
			optional = append(optional, c)
		}
	}
	respond.OK(w, r, map[string]any{
		"ready":           ready,
		"verdict":         blk.Verdict,
		"required_layers": required,
		"optional_layers": optional,
	})
}

// Version: GET /api/v1/system/version
func (h *SystemHandlers) Version(w http.ResponseWriter, r *http.Request) {
	var env pyEnvelope[pyVersion]
	if err := h.py.GetJSON(r.Context(), "/api/v1/system/version", &env); err != nil {
		respond.FailDependencyUnavailable(w, r, "build metadata unavailable")
		return
	}
	respond.OK(w, r, env.Data)
}

// Runtime: GET /api/v1/system/runtime
func (h *SystemHandlers) Runtime(w http.ResponseWriter, r *http.Request) {
	var env pyEnvelope[map[string]any]
	if err := h.py.GetJSON(r.Context(), "/api/v1/system/runtime", &env); err != nil {
		respond.FailDependencyUnavailable(w, r, "runtime state unavailable")
		return
	}
	respond.OK(w, r, env.Data)
}

// Capabilities: GET /api/v1/system/capabilities
// Proxies Python — the capability surface is a statement about the RUNNING
// platform, not about which routes this particular Go binary happens to own.
// Reporting Go's own partial table here would tell React "only system
// exists", breaking the frontend's feature detection (self-reported counts
// diverge from the real mounted surface by design, but they must diverge from
// the SAME source).
func (h *SystemHandlers) Capabilities(w http.ResponseWriter, r *http.Request) {
	var env pyEnvelope[map[string]any]
	err := h.py.DoJSON(r.Context(), http.MethodGet, "/api/v1/system/capabilities", nil, &env)
	if err != nil {
		h.replayBoundary(w, r, err, "capabilities unavailable")
		return
	}
	// Label which runtime answered. Divergence on the capability shape is then
	// an explicit, inspected field rather than an accidental one.
	data := map[string]any{}
	for k, v := range env.Data {
		data[k] = v
	}
	data["implementation"] = "go"
	respond.OK(w, r, data)
}

// Workers: GET /api/v1/system/workers
func (h *SystemHandlers) Workers(w http.ResponseWriter, r *http.Request) {
	var env pyEnvelope[map[string]any]
	if err := h.py.GetJSON(r.Context(), "/api/v1/system/workers", &env); err != nil {
		respond.FailDependencyUnavailable(w, r, "worker state unavailable")
		return
	}
	respond.OK(w, r, env.Data)
}

// Diagnostics: GET /api/v1/system/diagnostics
func (h *SystemHandlers) Diagnostics(w http.ResponseWriter, r *http.Request) {
	var env pyEnvelope[map[string]any]
	if err := h.py.GetJSON(r.Context(), "/api/v1/system/diagnostics", &env); err != nil {
		respond.FailDependencyUnavailable(w, r, "diagnostics unavailable")
		return
	}
	respond.OK(w, r, env.Data)
}

// DiagnosticsRun: POST /api/v1/system/diagnostics/run (idempotent; echoes the
// Idempotency-Key header into meta.idempotency_key).
func (h *SystemHandlers) DiagnosticsRun(w http.ResponseWriter, r *http.Request) {
	var env pyEnvelope[map[string]any]
	err := h.py.DoJSON(r.Context(), http.MethodPost, "/api/v1/system/diagnostics/run", nil, &env)
	if err != nil {
		h.replayBoundary(w, r, err, "diagnostics unavailable")
		return
	}
	meta := map[string]any{}
	if k := r.Header.Get("Idempotency-Key"); k != "" {
		if len(k) > 128 {
			k = k[:128]
		}
		meta["idempotency_key"] = k
	}
	respond.OKWithMeta(w, r, env.Data, meta)
}

// Replay the Python error body verbatim. Python is the authority for whether
// a dependency is missing (503 ENGINE_UNAVAILABLE) or the runtime is down
// (DEPENDENCY_UNAVAILABLE) — Go must not re-derive that from the status code,
// which was the original bug: a 503 tripped the circuit and surfaced the
// wrong code.
func (h *SystemHandlers) replayBoundary(w http.ResponseWriter, r *http.Request, err error, fallback string) {
	if be, ok := python.AsBoundary(err); ok {
		respond.ReplayBoundary(w, r, be)
		return
	}
	respond.FailDependencyUnavailable(w, r, fallback)
}

// Refresh: POST /api/v1/system/refresh (idempotent; echoes Idempotency-Key).
// Proxies Python — Go must not invent "refreshed": false, because Python's
// real contract is an honest 503 ENGINE_UNAVAILABLE when there is no adapter.
func (h *SystemHandlers) Refresh(w http.ResponseWriter, r *http.Request) {
	var env pyEnvelope[map[string]any]
	err := h.py.DoJSON(r.Context(), http.MethodPost, "/api/v1/system/refresh", nil, &env)
	if err != nil {
		if errors.Is(err, python.ErrNotConfigured) {
			// Go is not attached to Python at all: the closest honest answer.
			respond.Fail(w, r, contracts.CodeEngineUnavailable, "", nil)
			return
		}
		h.replayBoundary(w, r, err, "refresh unavailable")
		return
	}
	meta := map[string]any{}
	if k := r.Header.Get("Idempotency-Key"); k != "" {
		if len(k) > 128 {
			k = k[:128]
		}
		meta["idempotency_key"] = k
	}
	respond.OKWithMeta(w, r, env.Data, meta)
}

// MethodNotAllowedHandler emits the canonical 405 for a known path with the
// wrong verb (e.g. POST /api/v1/system/health).
func MethodNotAllowedHandler(w http.ResponseWriter, r *http.Request) {
	respond.MethodNotAllowed(w, r)
}

// NotFoundHandler emits the canonical v1 404 envelope for unknown /api/v1
// paths. Non-v1 paths use the legacy FastAPI {"detail": "Not Found"} body,
// which the legacy surface still serves.
func NotFoundHandler(w http.ResponseWriter, r *http.Request) {
	respond.NotFound(w, r)
}
