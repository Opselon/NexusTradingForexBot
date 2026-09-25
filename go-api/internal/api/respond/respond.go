// Package respond writes NSE API response envelopes in the exact shape the
// Python reference implementation produces.
//
// Two distinct envelopes exist and MUST NOT be unified:
//
//   - /api/v1/* -> {"data": ..., "meta": ...} / {"error": {...}}   (v1)
//   - legacy     -> raw JSON success, {"available","success","error"} on
//     failure (safe_error_payload), FastAPI's {"detail": ...} on 422/404.
//
// Serialization matches Python's json.dumps with separators (",", ":") —
// i.e. compact, no spaces — which is what FastAPI's JSONResponse emits. Go's
// json.Marshal produces the same compact form.
package respond

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/observability"
	"github.com/Opselon/NexusTradingForexBot/go-api/pkg/contracts"
)

// IsV1Path mirrors common._is_v1_path: the v1 envelope applies only under
// /api/v1. Exported so handlers pick the correct envelope without each one
// re-deriving the rule.
func IsV1Path(p string) bool { return strings.HasPrefix(p, "/api/v1") }

// UTCNowISO mirrors common.utc_now_iso(): timezone-aware ISO-8601 UTC.
// Python's datetime.isoformat() includes microseconds; we match that with
// RFC3339Nano trimmed to a stable layout so timestamps stay lexicographic.
func UTCNowISO() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05.999999+00:00")
}

// writeJSON emits status + body with the canonical content type.
func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

// metaEntry preserves Python's meta key ORDER. Python's ok() builds meta as
// {"request_id": ..., "generated_at": ...} then meta_extra.update(...) —
// insertion order, which JSONResponse emits verbatim. A Go map reorders keys
// randomly, and React clients + byte-level parity expect the exact order.
type metaEntry struct {
	keys []string
	vals map[string]any
}

func newMeta(rid string) *metaEntry {
	m := &metaEntry{vals: map[string]any{}}
	m.set("request_id", rid)
	m.set("generated_at", UTCNowISO())
	return m
}

func (m *metaEntry) set(k string, v any) {
	if _, hit := m.vals[k]; !hit {
		m.keys = append(m.keys, k)
	}
	m.vals[k] = v
}

// MarshalJSON emits keys in insertion order, mirroring Python's dict.
func (m *metaEntry) MarshalJSON() ([]byte, error) {
	var b strings.Builder
	b.WriteByte('{')
	for i, k := range m.keys {
		if i > 0 {
			b.WriteByte(',')
		}
		kb, err := json.Marshal(k)
		if err != nil {
			return nil, err
		}
		b.Write(kb)
		b.WriteByte(':')
		vb, err := json.Marshal(m.vals[k])
		if err != nil {
			return nil, err
		}
		b.Write(vb)
	}
	b.WriteByte('}')
	return []byte(b.String()), nil
}

// OK writes the v1 success envelope: {"data": ..., "meta": {...}} and ensures
// X-Request-ID is present (the correlation middleware already set it; this is
// a no-op unless a handler built its own response).
func OK(w http.ResponseWriter, r *http.Request, data any) {
	OKWithMeta(w, r, data, nil)
}

// OKWithMeta is OK plus extra meta fields (e.g. idempotency_key echo).
// Python: ok(request, data, meta_extra=...) merges extra keys into meta.
func OKWithMeta(w http.ResponseWriter, r *http.Request, data any, extra map[string]any) {
	rid := observability.RequestIDFrom(r.Context())
	if rid == "" {
		rid = observability.NewRequestID()
	}
	meta := newMeta(rid)
	for k, v := range extra {
		meta.set(k, v)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"data": data,
		"meta": meta,
	})
}

// Fail writes the v1 error envelope with the code's canonical HTTP status.
// Mirrors common.fail(): never exposes tracebacks, paths or secrets.
func Fail(w http.ResponseWriter, r *http.Request, code string, message string, details any) {
	rid := observability.RequestIDFrom(r.Context())
	if rid == "" {
		rid = observability.NewRequestID()
	}
	if message == "" {
		message = contracts.MessageForCode(code)
	}
	if details == nil {
		details = map[string]any{}
	}
	sem := contracts.ErrorSemantics[code]
	status := contracts.HTTPStatusForCode(code)
	writeJSON(w, status, contracts.ErrorEnvelope{
		Error: contracts.ErrorBody{
			Code:      code,
			Message:   message,
			Details:   details,
			RequestID: rid,
			Retryable: sem.Retryable,
		},
	})
}

// FailDependencyUnavailable is the common "Python runtime not reachable" path.
func FailDependencyUnavailable(w http.ResponseWriter, r *http.Request, message string) {
	Fail(w, r, contracts.CodeDependencyUnavailable, message, nil)
}

// FailEngineUnavailable is the canonical "engine not attached" path
// (adapter_or_503 in Python).
func FailEngineUnavailable(w http.ResponseWriter, r *http.Request) {
	Fail(w, r, contracts.CodeEngineUnavailable, "", nil)
}

// ReplayBoundary re-emits a Python error envelope verbatim. When Python has
// already decided the failure mode (e.g. 503 ENGINE_UNAVAILABLE vs 503
// DEPENDENCY_UNAVAILABLE), Go's job is to relay that decision, not re-derive
// it from the HTTP status alone — the status is ambiguous (several codes map
// to 503) but the envelope is not.
//
// The request_id is replaced with the caller's own so the Go response stays
// correlated to the Go request; the code/message/retryable are preserved.
func ReplayBoundary(w http.ResponseWriter, r *http.Request, be *python.BoundaryError) {
	status := contracts.HTTPStatusForCode(be.Envelope.Error.Code)
	if status == 0 {
		status = be.Status
	}
	if status < 400 {
		status = http.StatusBadGateway
	}
	rid := observability.RequestIDFrom(r.Context())
	if rid == "" {
		rid = observability.NewRequestID()
	}
	body := contracts.ErrorEnvelope{Error: be.Envelope.Error}
	body.Error.RequestID = rid
	writeJSON(w, status, body)
}

// NotFound writes a v1 RESOURCE_NOT_FOUND envelope.
func NotFound(w http.ResponseWriter, r *http.Request) {
	Fail(w, r, contracts.CodeResourceNotFound, "", nil)
}

// ValidationError writes a v1 VALIDATION_ERROR with bounded field details.
// Mirrors _v1_validation_handler: at most 20 entries, each
// {field, issue, input_present} — never the raw input value.
func ValidationError(w http.ResponseWriter, r *http.Request, errors []map[string]any) {
	if len(errors) > 20 {
		errors = errors[:20]
	}
	Fail(w, r, contracts.CodeValidationError, "Request validation failed.",
		map[string]any{"errors": errors})
}

// MethodNotAllowed writes the v1 METHOD_NOT_ALLOWED envelope (405).
func MethodNotAllowed(w http.ResponseWriter, r *http.Request) {
	Fail(w, r, contracts.CodeMethodNotAllowed, "", nil)
}

// LegacyValidationError writes FastAPI's DEFAULT 422 body —
// {"detail": [{"loc","msg","type"}]} — which is what non-/api/v1 routes emit
// (register_v1_exception_handlers passes legacy paths straight through).
// The React legacy error boundary parses this exact shape.
func LegacyValidationError(w http.ResponseWriter, r *http.Request, errs []map[string]any) {
	writeJSON(w, http.StatusUnprocessableEntity, map[string]any{"detail": errs})
}

// LegacyHTTPError writes the legacy {"detail": ...} body for Starlette
// HTTPException on non-/api/v1 paths.
func LegacyHTTPError(w http.ResponseWriter, status int, detail any) {
	if detail == nil {
		detail = http.StatusText(status)
	}
	writeJSON(w, status, map[string]any{"detail": detail})
}
