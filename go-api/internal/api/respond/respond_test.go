// Package respond tests the frozen wire envelopes against the golden bodies
// captured from the live Python reference (phase_a_contracts.json).
package respond

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Opselon/NexusTradingForexBot/go-api/pkg/contracts"
)

func rec() *httptest.ResponseRecorder { return httptest.NewRecorder() }

func r() *http.Request {
	req := httptest.NewRequest(http.MethodGet, "/api/v1/x", nil)
	return req
}

// TestV1EnvelopeSuccess pins the Python v1 success shape:
// {"data":..., "meta":{"request_id":..., "generated_at":...}}
func TestV1EnvelopeSuccess(t *testing.T) {
	w := rec()
	OK(w, r(), map[string]any{"verdict": "READY"})
	if got := w.Code; got != http.StatusOK {
		t.Fatalf("code = %d", got)
	}
	if ct := w.Header().Get("Content-Type"); !strings.Contains(ct, "application/json") {
		t.Errorf("Content-Type = %q", ct)
	}
	body := w.Body.String()
	if !strings.Contains(body, `"verdict":"READY"`) {
		t.Errorf("data missing: %s", body)
	}
	if !strings.Contains(body, `"request_id":"req_`) {
		t.Errorf("request_id missing: %s", body)
	}
	if !strings.Contains(body, `"generated_at":"`) {
		t.Errorf("generated_at missing: %s", body)
	}
}

// TestV1EnvelopeError pins the Python v1 error shape and stable field order:
// code, message, details, request_id, retryable.
func TestV1EnvelopeError(t *testing.T) {
	w := rec()
	Fail(w, r(), contracts.CodeResourceNotFound,
		"The requested resource was not found.", nil)
	if got := w.Code; got != http.StatusNotFound {
		t.Fatalf("code = %d, want 404", got)
	}
	const want = `{"error":{"code":"RESOURCE_NOT_FOUND","message":"The requested resource was not found.","details":{},"request_id":"req_`
	if !strings.HasPrefix(strings.TrimSpace(w.Body.String()), want) {
		t.Fatalf("body prefix mismatch\n got: %s\nwant: %s", w.Body.String(), want)
	}
	if !strings.HasSuffix(strings.TrimSpace(w.Body.String()), `,"retryable":false}}`) {
		t.Fatalf("body suffix mismatch: %s", w.Body.String())
	}
}

// TestV1ErrorCodes covers the complete v1 error-code family from
// ERROR_SEMANTICS (web/api_v1/common.py) — a client must see identical
// codes, statuses and retryable flags from either runtime.
func TestV1ErrorCodes(t *testing.T) {
	cases := []struct {
		code   string
		status int
		retry  bool
	}{
		{contracts.CodeValidationError, 422, false},
		{contracts.CodeResourceNotFound, 404, false},
		{contracts.CodeConflict, 409, false},
		{contracts.CodeForbidden, 403, false},
		{contracts.CodeMethodNotAllowed, 405, false},
		{contracts.CodePayloadTooLarge, 413, false},
		{contracts.CodeInternalError, 500, false},
		{contracts.CodeDependencyUnavailable, 503, true},
		{contracts.CodeEngineUnavailable, 503, true},
		{contracts.CodeResourceUnavailable, 503, true},
		{contracts.CodeTimeout, 504, true},
	}
	if len(cases) != len(contracts.ErrorSemantics) {
		t.Fatalf("test covers %d codes, ERROR_SEMANTICS has %d",
			len(cases), len(contracts.ErrorSemantics))
	}
	for _, tc := range cases {
		t.Run(tc.code, func(t *testing.T) {
			w := rec()
			Fail(w, r(), tc.code, "boom", nil)
			if w.Code != tc.status {
				t.Errorf("status = %d, want %d", w.Code, tc.status)
			}
			sem, ok := contracts.ErrorSemantics[tc.code]
			if !ok {
				t.Fatalf("code %q missing from ErrorSemantics", tc.code)
			}
			if sem.Retryable != tc.retry {
				t.Errorf("retryable = %v, want %v", sem.Retryable, tc.retry)
			}
			if !strings.Contains(w.Body.String(), `"code":"`+tc.code+`"`) {
				t.Errorf("body lacks code: %s", w.Body.String())
			}
		})
	}
}

// TestV1ErrorDetailsAreNeverNil — the Python envelope always emits a details
// object (never null), or clients parsing details unconditionally break.
func TestV1ErrorDetailsAreNeverNil(t *testing.T) {
	w := rec()
	Fail(w, r(), contracts.CodeValidationError, "boom", nil)
	if strings.Contains(w.Body.String(), `"details":null`) {
		t.Error("details must be {} not null")
	}

	w = rec()
	Fail(w, r(), contracts.CodeValidationError, "boom",
		map[string]any{"field": "limit"})
	if !strings.Contains(w.Body.String(), `"field":"limit"`) {
		t.Errorf("details missing: %s", w.Body.String())
	}
}

// TestValidationErrorMirrorsPython pins the _v1_validation_handler contract:
// at most 20 entries, each {field, issue, input_present} and never the raw
// input value (which can carry secrets).
func TestValidationErrorMirrorsPython(t *testing.T) {
	w := rec()
	ValidationError(w, r(), []map[string]any{
		{"field": "limit", "issue": "not an integer", "input_present": true},
	})
	if w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("code = %d, want 422", w.Code)
	}
	body := w.Body.String()
	for _, key := range []string{`"field":"limit"`, `"issue":"not an integer"`,
		`"input_present":true`, `"code":"VALIDATION_ERROR"`} {
		if !strings.Contains(body, key) {
			t.Errorf("body lacks %s: %s", key, body)
		}
	}

	// Bounded: more than 20 errors must be truncated, not grown.
	w = rec()
	tooMany := make([]map[string]any, 50)
	for i := range tooMany {
		tooMany[i] = map[string]any{"field": "f", "issue": "bad"}
	}
	ValidationError(w, r(), tooMany)
	if strings.Count(w.Body.String(), `"field":"f"`) > 20 {
		t.Errorf("validation errors must be capped at 20: %s", w.Body.String())
	}
}

// TestLegacy422Envelope pins FastAPI's {"detail": ...} shape, which is what
// the React error boundary parses for validation messages on legacy routes.
func TestLegacy422Envelope(t *testing.T) {
	w := rec()
	LegacyValidationError(w, r(), []map[string]any{
		{"loc": []any{"query", "limit"},
			"msg":  "value is not a valid integer",
			"type": "type_error.integer"},
	})
	if w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("code = %d, want 422", w.Code)
	}
	body := w.Body.String()
	for _, want := range []string{`"value is not a valid integer"`,
		`"type_error.integer"`, `"loc"`} {
		if !strings.Contains(body, want) {
			t.Errorf("body lacks %s: %s", want, body)
		}
	}
}

// TestNotFoundAndMethodNotAllowed pin the two most common 4xx shapes.
func TestNotFoundAndMethodNotAllowed(t *testing.T) {
	w := rec()
	NotFound(w, r())
	if w.Code != http.StatusNotFound {
		t.Fatalf("NotFound = %d, want 404", w.Code)
	}
	if !strings.Contains(w.Body.String(), contracts.CodeResourceNotFound) {
		t.Errorf("body: %s", w.Body.String())
	}

	w = rec()
	MethodNotAllowed(w, r())
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("MethodNotAllowed = %d, want 405", w.Code)
	}
	if !strings.Contains(w.Body.String(), contracts.CodeMethodNotAllowed) {
		t.Errorf("body: %s", w.Body.String())
	}
}

// TestDependencyUnavailable is the no-Python-runtime path: Go must say so
// explicitly rather than fabricating engine state.
func TestDependencyUnavailable(t *testing.T) {
	w := rec()
	FailDependencyUnavailable(w, r(), "python runtime unreachable")
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("code = %d, want 503", w.Code)
	}
	if !strings.Contains(w.Body.String(), contracts.CodeDependencyUnavailable) {
		t.Errorf("body: %s", w.Body.String())
	}
	if !strings.Contains(w.Body.String(), `"retryable":true`) {
		t.Errorf("must be retryable: %s", w.Body.String())
	}
}

func TestErrorsAreSentinels(t *testing.T) {
	if !errors.Is(contracts.ErrValidation, contracts.ErrValidation) {
		t.Error("sentinel must be comparable with errors.Is")
	}
	if errors.Is(contracts.ErrNotFound, contracts.ErrInternal) {
		t.Error("distinct sentinels must not be equal")
	}
	// CodeOf round-trips each sentinel back to its wire code.
	if got := contracts.CodeOf(contracts.ErrDependencyUnavailable); got != contracts.CodeDependencyUnavailable {
		t.Errorf("CodeOf = %q", got)
	}
	if contracts.CodeOf(nil) != "" {
		t.Error("CodeOf(nil) must be empty")
	}
}

// TestCodeForStatus pins the _v1_http_handler status -> code mapping.
// 503 is the interesting case: three codes share it, and only
// ENGINE_UNAVAILABLE may win (Python's insertion order).
func TestCodeForStatus(t *testing.T) {
	cases := map[int]string{
		404: contracts.CodeResourceNotFound,
		405: contracts.CodeMethodNotAllowed,
		409: contracts.CodeConflict,
		413: contracts.CodePayloadTooLarge,
		504: contracts.CodeTimeout,
		503: contracts.CodeEngineUnavailable, // first match wins, not random
		422: contracts.CodeValidationError,
		403: contracts.CodeForbidden,
		500: contracts.CodeInternalError,
		502: contracts.CodeInternalError, // >=500 unmapped -> INTERNAL_ERROR
		400: "BAD_REQUEST",               // Python's default for 4xx-unmapped
		401: "BAD_REQUEST",
	}
	for status, want := range cases {
		if got := contracts.CodeForStatus(status); got != want {
			t.Errorf("CodeForStatus(%d) = %q, want %q", status, got, want)
		}
	}
}

// TestMessageForCode pins the canonical human-readable message per code.
func TestMessageForCode(t *testing.T) {
	cases := map[string]string{
		contracts.CodeResourceNotFound:      "The requested resource was not found.",
		contracts.CodeMethodNotAllowed:      "The HTTP method is not allowed for this resource.",
		contracts.CodeDependencyUnavailable: "A required dependency is not available.",
		contracts.CodeValidationError:       "The request parameters were invalid.",
		contracts.CodeEngineUnavailable:     "The trading engine is not attached to the API server.",
	}
	for code, want := range cases {
		if got := contracts.MessageForCode(code); got != want {
			t.Errorf("MessageForCode(%q) = %q, want %q", code, got, want)
		}
	}
}
