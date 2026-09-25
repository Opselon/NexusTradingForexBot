// Package handlers — Phase B read-only domains: research, risk, runtime.
//
// All of these are thin adapters over the Python runtime. The pagination
// contract is VALIDATED IN GO rather than proxied, because reproducing
// FastAPI's 422 locally means the client gets an immediate, contract-
// identical rejection without a round trip — and the boundary stays free of
// validation-shaped traffic.
//
// The 422 shape was captured live from FastAPI's RequestValidationError
// handler (api_v1/errors.py): field/issue/input_present, never the raw input.
package handlers

import (
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/respond"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
	"github.com/Opselon/NexusTradingForexBot/go-api/pkg/contracts"
)

// Pagination limits mirrored from api_v1/research.py's Query() constraints.
// The v1 pagination model is page >= 1, 1 <= page_size <= 200.
const (
	minPage         = 1
	maxPage         = 10_000
	minPageSize     = 1
	maxPageSize     = 200
	defaultPageSize = 50
)

// validationIssue is one entry in the bounded details.errors list. Field
// names and issue codes match Pydantic v2's error types exactly.
type validationIssue struct {
	Field        string `json:"field"`
	Issue        string `json:"issue"`
	InputPresent bool   `json:"input_present"`
}

// validatePagination reproduces FastAPI's Query(ge=1, le=...) behaviour for
// the page/page_size pair, writing the v1 VALIDATION_ERROR body on failure.
//
// Mirrors parse_pagination + the RequestValidationError handler. The issue
// code is Pydantic v2's error type, and the three cases are DISTINCT:
//
//	"int_parsing"        value present but not an integer
//	"greater_than_equal" integer below the minimum
//	"less_than_equal"    integer above the maximum
//
// Conflating them would send the wrong code to a client that distinguishes
// "typo'd the query string" from "asked for a page that cannot exist".
// input_present is true iff the caller supplied the parameter at all, and the
// raw value is NEVER echoed (it can carry injected content).
//
// NOTE the deliberate asymmetry: an ABSENT param uses the Query default and
// is always valid; only a PRESENT-but-invalid value 422s.
func validatePagination(w http.ResponseWriter, r *http.Request) (page, pageSize int, ok bool) {
	page, pageSize = 1, defaultPageSize
	var issues []validationIssue

	if v := r.URL.Query().Get("page"); v != "" {
		n, err := strconv.Atoi(v)
		switch {
		case err != nil:
			issues = append(issues, validationIssue{
				Field: "page", Issue: "int_parsing", InputPresent: true,
			})
		case n < minPage:
			issues = append(issues, validationIssue{
				Field: "page", Issue: "greater_than_equal", InputPresent: true,
			})
		case n > maxPage:
			issues = append(issues, validationIssue{
				Field: "page", Issue: "less_than_equal", InputPresent: true,
			})
		default:
			page = n
		}
	}

	if v := r.URL.Query().Get("page_size"); v != "" {
		n, err := strconv.Atoi(v)
		switch {
		case err != nil:
			issues = append(issues, validationIssue{
				Field: "page_size", Issue: "int_parsing", InputPresent: true,
			})
		case n < minPageSize:
			issues = append(issues, validationIssue{
				Field: "page_size", Issue: "greater_than_equal", InputPresent: true,
			})
		case n > maxPageSize:
			issues = append(issues, validationIssue{
				Field: "page_size", Issue: "less_than_equal", InputPresent: true,
			})
		default:
			pageSize = n
		}
	}

	if len(issues) > 0 {
		respond.ValidationError(w, r, issueMaps(issues))
		return 0, 0, false
	}
	return page, pageSize, true
}

// issueMaps converts the typed issues into the details.errors shape.
func issueMaps(issues []validationIssue) []map[string]any {
	out := make([]map[string]any, 0, len(issues))
	for _, i := range issues {
		out = append(out, map[string]any{
			"field":         i.Field,
			"issue":         i.Issue,
			"input_present": i.InputPresent,
		})
	}
	return out
}

// ResearchHandlers serves /api/v1/research/* (5 operations, all GET).
type ResearchHandlers struct {
	py *python.Client
}

// NewResearch builds the research handlers.
func NewResearch(py *python.Client) *ResearchHandlers { return &ResearchHandlers{py: py} }

// proxyGET fetches path from Python and re-envelopes. On a contract-level
// rejection it replays Python's envelope verbatim; on a transport failure it
// reports DEPENDENCY_UNAVAILABLE. It never fabricates data.
func (h *ResearchHandlers) proxyGET(w http.ResponseWriter, r *http.Request, path string) {
	var env pyEnvelope[map[string]any]
	if err := h.py.DoJSON(r.Context(), http.MethodGet, path, nil, &env); err != nil {
		h.replayOrFail(w, r, err, "research subsystem unavailable")
		return
	}
	respond.OK(w, r, env.Data)
}

func (h *ResearchHandlers) replayOrFail(w http.ResponseWriter, r *http.Request, err error, fallback string) {
	if be, ok := python.AsBoundary(err); ok {
		respond.ReplayBoundary(w, r, be)
		return
	}
	if errors.Is(err, python.ErrNotConfigured) {
		respond.FailDependencyUnavailable(w, r, fallback)
		return
	}
	respond.FailDependencyUnavailable(w, r, fallback)
}

// Status: GET /api/v1/research/status
func (h *ResearchHandlers) Status(w http.ResponseWriter, r *http.Request) {
	h.proxyGET(w, r, "/api/v1/research/status")
}

// Strategies: GET /api/v1/research/strategies[lifecycle=…][page=…][page_size=…]
// Pagination is validated in Go (422 on violation), then the query is
// forwarded verbatim so Python's registry filter stays authoritative.
func (h *ResearchHandlers) Strategies(w http.ResponseWriter, r *http.Request) {
	if _, _, ok := validatePagination(w, r); !ok {
		return
	}
	h.proxyGET(w, r, "/api/v1/research/strategies"+preserveQuery(r))
}

// StrategyDetail: GET /api/v1/research/strategies/{strategy_id}
func (h *ResearchHandlers) StrategyDetail(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("strategy_id")
	if id == "" || strings.ContainsAny(id, "/\\") {
		respond.Fail(w, r, contracts.CodeValidationError, "invalid strategy id", nil)
		return
	}
	h.proxyGET(w, r, "/api/v1/research/strategies/"+id)
}

// Runs: GET /api/v1/research/runs[strategy_id=…][page=…][page_size=…]
func (h *ResearchHandlers) Runs(w http.ResponseWriter, r *http.Request) {
	if _, _, ok := validatePagination(w, r); !ok {
		return
	}
	h.proxyGET(w, r, "/api/v1/research/runs"+preserveQuery(r))
}

// Datasets: GET /api/v1/research/datasets
func (h *ResearchHandlers) Datasets(w http.ResponseWriter, r *http.Request) {
	h.proxyGET(w, r, "/api/v1/research/datasets")
}

// preserveQuery forwards the ORIGINAL query string untouched. This is
// deliberate: Python owns the semantics of every filter (lifecycle is a free
// text filter, not an enum — an unknown value legitimately returns an empty
// page with 200). Re-validating it in Go would invent constraints Python
// does not have.
func preserveQuery(r *http.Request) string {
	q := r.URL.Query().Encode()
	if q == "" {
		return ""
	}
	return "?" + q
}

// RiskHandlers serves /api/v1/risk/* (2 operations, both GET).
type RiskHandlers struct {
	py *python.Client
}

// NewRisk builds the risk handlers.
func NewRisk(py *python.Client) *RiskHandlers { return &RiskHandlers{py: py} }

// Status: GET /api/v1/risk/status
func (h *RiskHandlers) Status(w http.ResponseWriter, r *http.Request) {
	var env pyEnvelope[map[string]any]
	if err := h.py.DoJSON(r.Context(), http.MethodGet, "/api/v1/risk/status", nil, &env); err != nil {
		h.replayOrFail(w, r, err, "risk state unavailable")
		return
	}
	respond.OK(w, r, env.Data)
}

// Summary: GET /api/v1/risk/summary
func (h *RiskHandlers) Summary(w http.ResponseWriter, r *http.Request) {
	var env pyEnvelope[map[string]any]
	if err := h.py.DoJSON(r.Context(), http.MethodGet, "/api/v1/risk/summary", nil, &env); err != nil {
		h.replayOrFail(w, r, err, "risk summary unavailable")
		return
	}
	respond.OK(w, r, env.Data)
}

func (h *RiskHandlers) replayOrFail(w http.ResponseWriter, r *http.Request, err error, fallback string) {
	if be, ok := python.AsBoundary(err); ok {
		respond.ReplayBoundary(w, r, be)
		return
	}
	respond.FailDependencyUnavailable(w, r, fallback)
}

// RuntimeHandlers serves the READ part of /api/v1/runtime/* (3 of 5
// operations). /mode/validate and /mode/preview are POST and belong to the
// control-API phase, not here.
type RuntimeHandlers struct {
	py *python.Client
}

// NewRuntime builds the runtime read handlers.
func NewRuntime(py *python.Client) *RuntimeHandlers { return &RuntimeHandlers{py: py} }

// Mode: GET /api/v1/runtime/mode
func (h *RuntimeHandlers) Mode(w http.ResponseWriter, r *http.Request) {
	h.proxyGET(w, r, "/api/v1/runtime/mode")
}

// Freshness: GET /api/v1/runtime/freshness
func (h *RuntimeHandlers) Freshness(w http.ResponseWriter, r *http.Request) {
	h.proxyGET(w, r, "/api/v1/runtime/freshness")
}

// Shutdown: GET /api/v1/runtime/shutdown
func (h *RuntimeHandlers) Shutdown(w http.ResponseWriter, r *http.Request) {
	h.proxyGET(w, r, "/api/v1/runtime/shutdown")
}

func (h *RuntimeHandlers) proxyGET(w http.ResponseWriter, r *http.Request, path string) {
	var env pyEnvelope[map[string]any]
	if err := h.py.DoJSON(r.Context(), http.MethodGet, path, nil, &env); err != nil {
		if be, ok := python.AsBoundary(err); ok {
			respond.ReplayBoundary(w, r, be)
			return
		}
		respond.FailDependencyUnavailable(w, r, "runtime state unavailable")
		return
	}
	respond.OK(w, r, env.Data)
}
