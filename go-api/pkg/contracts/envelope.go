// Package contracts holds the frozen NSE API wire contracts.
//
// Every type here mirrors the EXECUTABLE Python/FastAPI contract, verified
// against a live create_app()/create_v1_app() instance. Do not "improve"
// field names, casing, null semantics or ordering: the React frontend and the
// parity tests depend on byte-for-byte JSON equivalence.
package contracts

import (
	"errors"
)

// Envelope is the /api/v1/* success body: {"data": ..., "meta": ...}.
// Produced by nexus_scalp.web.api_v1.common.ok().
type Envelope[T any] struct {
	Data T            `json:"data"`
	Meta EnvelopeMeta `json:"meta"`
}

// EnvelopeMeta is the meta block on every v1 success response.
type EnvelopeMeta struct {
	// request_id is ALSO emitted as the X-Request-ID response header.
	RequestID string `json:"request_id"`
	// generated_at: ISO-8601 UTC timestamp (e.g. 2026-09-24T07:42:35.123456+00:00).
	GeneratedAt string `json:"generated_at"`
	// idempotency_key echoes the Idempotency-Key request header when present.
	IdempotencyKey *string `json:"idempotency_key,omitempty"`
}

// ErrorEnvelope is the /api/v1/* failure body: {"error": {...}}.
// Produced by nexus_scalp.web.api_v1.common.fail(). Never carries a
// traceback, filesystem path, SQL or secret.
type ErrorEnvelope struct {
	Error ErrorBody `json:"error"`
}

// ErrorBody is the single v1 error shape.
type ErrorBody struct {
	// code is one of the ErrorCode constants below (stable wire contract).
	Code string `json:"code"`
	// message is a generic, safe human string from _ERROR_MESSAGES.
	Message string `json:"message"`
	// details carries bounded structured detail (may be {}).
	Details any `json:"details"`
	// request_id correlates with logs and X-Request-ID.
	RequestID string `json:"request_id"`
	// retryable tells clients whether to retry (503/504 = true).
	Retryable bool `json:"retryable"`
}

// ErrorCode constants. The tuple is (HTTP status, retryable) — see
// ERROR_SEMANTICS in web/api_v1/common.go. UNKNOWN must never be emitted by
// a handler; it exists so a mapping miss is explicit rather than silent.
const (
	CodeMethodNotAllowed      = "METHOD_NOT_ALLOWED"
	CodePayloadTooLarge       = "PAYLOAD_TOO_LARGE"
	CodeValidationError       = "VALIDATION_ERROR"
	CodeResourceNotFound      = "RESOURCE_NOT_FOUND"
	CodeConflict              = "CONFLICT"
	CodeForbidden             = "FORBIDDEN"
	CodeEngineUnavailable     = "ENGINE_UNAVAILABLE"
	CodeDependencyUnavailable = "DEPENDENCY_UNAVAILABLE"
	CodeResourceUnavailable   = "RESOURCE_UNAVAILABLE"
	CodeTimeout               = "TIMEOUT"
	CodeInternalError         = "INTERNAL_ERROR"
)

// Sentinels let handlers return typed errors and map them to wire codes.
// Each sentinel's Error() string IS its wire code, so CodeOf round-trips.
var (
	ErrMethodNotAllowed      = errors.New(CodeMethodNotAllowed)
	ErrPayloadTooLarge       = errors.New(CodePayloadTooLarge)
	ErrValidation            = errors.New(CodeValidationError)
	ErrNotFound              = errors.New(CodeResourceNotFound)
	ErrConflict              = errors.New(CodeConflict)
	ErrForbidden             = errors.New(CodeForbidden)
	ErrEngineUnavailable     = errors.New(CodeEngineUnavailable)
	ErrDependencyUnavailable = errors.New(CodeDependencyUnavailable)
	ErrResourceUnavailable   = errors.New(CodeResourceUnavailable)
	ErrTimeout               = errors.New(CodeTimeout)
	ErrInternal              = errors.New(CodeInternalError)
)

// CodeOf returns the wire code carried by a sentinel error ("" for nil or an
// unrecognised error, which callers should treat as INTERNAL_ERROR).
func CodeOf(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

// DefaultErrorMessages mirrors _ERROR_MESSAGES exactly.
var DefaultErrorMessages = map[string]string{
	CodeMethodNotAllowed:      "The HTTP method is not allowed for this resource.",
	CodePayloadTooLarge:       "The request payload exceeds the allowed size.",
	CodeValidationError:       "The request parameters were invalid.",
	CodeResourceNotFound:      "The requested resource was not found.",
	CodeConflict:              "The request conflicts with the current state.",
	CodeForbidden:             "The requested operation is not allowed.",
	CodeEngineUnavailable:     "The trading engine is not attached to the API server.",
	CodeDependencyUnavailable: "A required dependency is not available.",
	CodeResourceUnavailable:   "The requested resource is temporarily unavailable.",
	CodeTimeout:               "The request timed out.",
	CodeInternalError:         "The server could not complete this request.",
}

// ErrorSemantics maps a v1 error code to (HTTP status, retryable).
// Frozen from ERROR_SEMANTICS in nexus_scalp/web/api_v1/common.py.
var ErrorSemantics = map[string]struct {
	HTTPStatus int
	Retryable  bool
}{
	CodeMethodNotAllowed:      {405, false},
	CodePayloadTooLarge:       {413, false},
	CodeValidationError:       {422, false},
	CodeResourceNotFound:      {404, false},
	CodeConflict:              {409, false},
	CodeForbidden:             {403, false},
	CodeEngineUnavailable:     {503, true},
	CodeDependencyUnavailable: {503, true},
	CodeResourceUnavailable:   {503, true},
	CodeTimeout:               {504, true},
	CodeInternalError:         {500, false},
}

// HTTPStatusForCode returns the canonical status for a code, defaulting to
// 500 so an unknown code can never masquerade as success.
func HTTPStatusForCode(code string) int {
	if s, ok := ErrorSemantics[code]; ok {
		return s.HTTPStatus
	}
	return 500
}

// errorSemanticsOrder preserves Python's dict INSERTION order. CodeForStatus
// scans it and returns the FIRST code whose status matches — with
// ENGINE_UNAVAILABLE / DEPENDENCY_UNAVAILABLE / RESOURCE_UNAVAILABLE all
// mapping to 503, a Go map iteration would pick one at random and break parity.
var errorSemanticsOrder = []string{
	CodeMethodNotAllowed,
	CodePayloadTooLarge,
	CodeValidationError,
	CodeResourceNotFound,
	CodeConflict,
	CodeForbidden,
	CodeEngineUnavailable,
	CodeDependencyUnavailable,
	CodeResourceUnavailable,
	CodeTimeout,
	CodeInternalError,
}

// CodeForStatus mirrors the status -> code mapping in _v1_http_handler
// (web/api_v1/errors.py). NOTE the quirk it reproduces: a status with no
// ERROR_SEMANTICS entry falls back to BAD_REQUEST for <500, but BAD_REQUEST is
// not itself a key in ERROR_SEMANTICS, so fail() resolves it to the
// INTERNAL_ERROR tuple -> HTTP 500. That is the EXECUTABLE contract; it is
// recorded in api/migration/inventory.yaml rather than silently "fixed".
func CodeForStatus(status int) string {
	switch status {
	case 404:
		return CodeResourceNotFound
	case 405:
		return CodeMethodNotAllowed
	case 409:
		return CodeConflict
	case 413:
		return CodePayloadTooLarge
	case 504:
		return CodeTimeout
	}
	for _, code := range errorSemanticsOrder {
		if ErrorSemantics[code].HTTPStatus == status {
			return code
		}
	}
	if status >= 500 {
		return CodeInternalError
	}
	// Deliberately mirrors Python's default even though it resolves to a 500.
	return "BAD_REQUEST"
}

// MessageForCode returns the canonical public message for a code.
func MessageForCode(code string) string {
	if m, ok := DefaultErrorMessages[code]; ok {
		return m
	}
	return DefaultErrorMessages[CodeInternalError]
}

// Pagination mirrors API_V1_PAGINATION v1 (build_page): items/page/page_size/
// has_more. No total counts anywhere — clients page until has_more is false.
type Pagination[T any] struct {
	Items    []T  `json:"items"`
	Page     int  `json:"page"`
	PageSize int  `json:"page_size"`
	HasMore  bool `json:"has_more"`
}

// PageParams are the validated query params. Constraints mirrored from
// parse_pagination: page >= 1, 1 <= page_size <= MaxPageSize.
type PageParams struct {
	Page     int
	PageSize int
}

const (
	DefaultPageSize = 50
	MaxPageSize     = 200
)
