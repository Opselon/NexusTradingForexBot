// Package observability provides request correlation and safe logging for the
// NSE Go control plane.
package observability

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"net/http"
	"strings"
)

// ContextKey is the private context key type (never an exported string —
// avoids collisions with other packages).
type contextKey struct{}

var requestIDKey = contextKey{}

// HeaderRequestID is the correlation header name. Go's http package
// canonicalises it to "X-Request-Id" on the wire; HTTP header names are
// case-insensitive, so clients that send/parse "x-request-id" keep working.
const HeaderRequestID = "X-Request-ID"

// MaxRequestIDLen mirrors the Python contract: incoming ids are trimmed to 64.
const MaxRequestIDLen = 64

// NewRequestID mirrors Python new_request_id(): "req_" + uuid4().hex[:10].
// 5 random bytes -> 10 hex chars, so the id is 14 chars like Python's.
func NewRequestID() string {
	var b [5]byte
	if _, err := rand.Read(b[:]); err != nil {
		// crypto/rand failure must not panic a control plane; fall back to a
		// constant so correlation still works (never a zero-length id).
		return "req_0000000000"
	}
	return "req_" + hex.EncodeToString(b[:])
}

// RequestIDFrom returns the correlation id on ctx, or "".
func RequestIDFrom(ctx context.Context) string {
	if v, ok := ctx.Value(requestIDKey).(string); ok {
		return v
	}
	return ""
}

// WithRequestID returns a context carrying id.
func WithRequestID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, requestIDKey, id)
}

// requestIDFromRequest mirrors request_id_from_request(): an incoming
// X-Request-ID wins (the browser client attaches the id it shows the user),
// otherwise a fresh one. Trimmed to 64.
func requestIDFromRequest(r *http.Request) string {
	if h := strings.TrimSpace(r.Header.Get(HeaderRequestID)); h != "" {
		if len(h) > MaxRequestIDLen {
			return h[:MaxRequestIDLen]
		}
		return h
	}
	return NewRequestID()
}

// RequestIDMiddleware attaches a correlation id to every request and echoes
// it on the response. Mirrors attach_request_id_middleware (web/errors.py):
// inbound header > generated, always present on the response.
func RequestIDMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := requestIDFromRequest(r)
		w.Header().Set(HeaderRequestID, id)
		next.ServeHTTP(w, r.WithContext(WithRequestID(r.Context(), id)))
	})
}
