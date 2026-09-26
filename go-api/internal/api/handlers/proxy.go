// Package handlers provides the generic pass-through proxy.
//
// The bulk of the NSE surface is a thin read of Python's in-process state with
// no Go-side logic to add. Rather than 420 near-identical handwritten adapters,
// Proxy implements one generic forwarder and the route table in table_gen.go
// enumerates exactly which paths it may serve. The table is generated from
// Python's own resolved route dump, so drift is a build-time-visible mismatch
// instead of a silent 404.
package handlers

import (
	"encoding/json"
	"io"
	"net/http"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/respond"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/routing"
)

// Proxy forwards requests to the Python runtime unchanged.
type Proxy struct {
	py *python.Client
}

// NewProxy returns a proxy bound to the given Python client.
func NewProxy(py *python.Client) *Proxy {
	return &Proxy{py: py}
}

// Handler returns an http.Handler for the given proxied operation.
//
// GET/DELETE are bodyless; POST/PUT stream the client body through to Python
// byte-for-byte, so any validation the Python side performs is still the one
// the client sees. Errors are replayed with Python's own envelope so status
// codes and error codes cannot diverge.
//
// The response is decoded and re-encoded only to serve the surface-appropriate
// shape: /api/v1 routes get the {data,meta} envelope, legacy routes get the
// bare payload Python emitted. Values are never inspected or rewritten —
// Python stays the single fact authority.
func (p *Proxy) Handler(method, path string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		full := r.URL.Path
		if r.URL.RawQuery != "" {
			full += "?" + r.URL.RawQuery
		}

		// Wave 3: dependency-aware routing. Record the classification the
		// table holds for this route on every proxied response, so an
		// operator can see in flight which routes carry a real Python/DB
		// dependency and which are Go-serving candidates. The decision does
		// NOT change behaviour yet - every route still forwards to Python -
		// but it is visible and testable now, and a later wave flips the
		// switch only for Candidate routes.
		if d := routing.Decide(method, path); d.Classified {
			w.Header().Set("X-NSE-Routing-Reason", d.Why)
		} else {
			w.Header().Set("X-NSE-Routing-Reason", "unclassified")
		}

		raw, err := p.py.DoRaw(r.Context(), method, full, r.Body)
		if err != nil {
			if be, ok := python.AsBoundary(err); ok {
				respond.ReplayBoundary(w, r, be)
				return
			}
			// Legacy 4xx/5xx: Python ANSWERED. Replay its status + body
			// verbatim. Upgrading this to a synthesized v1 503 would report
			// an outage the upstream never had.
			if lr, ok := python.AsLegacy(err); ok {
				writeRawJSONStatus(w, lr.Status, lr.Body)
				return
			}
			respond.FailDependencyUnavailable(w, r, "upstream unavailable")
			return
		}

		// 204 No Content and empty bodies: nothing to decode. Serve the
		// upstream status as-is so DELETE etc. keep their contract.
		if len(raw) == 0 {
			w.WriteHeader(http.StatusNoContent)
			return
		}

		if respond.IsV1Path(r.URL.Path) {
			var env pyEnvelope[json.RawMessage]
			if err := json.Unmarshal(raw, &env); err != nil {
				// Python did not return the v1 envelope. Serve the bytes
				// through rather than fabricating one, so a downstream format
				// change surfaces at the client instead of being masked.
				writeRawJSON(w, http.StatusOK, raw)
				return
			}
			respond.OKWithMeta(w, r, env.Data, env.Meta)
			return
		}

		// Legacy surface: bare payload, no envelope.
		writeRawJSON(w, http.StatusOK, raw)
	})
}

// writeRawJSON emits already-encoded JSON without re-parsing it, preserving
// Python's key order and float formatting byte-for-byte.
func writeRawJSON(w http.ResponseWriter, status int, b []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	io.WriteString(w, string(b))
}

// writeRawJSONStatus serves an upstream error body byte-for-byte at the
// upstream's own status code, for the legacy surface where {"detail": ...}
// must reach the client unchanged.
func writeRawJSONStatus(w http.ResponseWriter, status int, b []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	io.WriteString(w, string(b))
}
