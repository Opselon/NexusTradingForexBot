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

// Proxy forwards requests to the Python runtime unchanged, or executes
// registered direct handlers for Candidate routes with automatic fallback.
type Proxy struct {
	py             *python.Client
	directHandlers map[string]http.HandlerFunc
}

// NewProxy returns a proxy bound to the given Python client.
func NewProxy(py *python.Client) *Proxy {
	return &Proxy{
		py:             py,
		directHandlers: make(map[string]http.HandlerFunc),
	}
}

// RegisterDirect registers a Go handler to be served directly for a candidate route,
// with automatic fallback to Python proxying on error or if direct serving is disabled.
func (p *Proxy) RegisterDirect(method, path string, h http.HandlerFunc) {
	if p.directHandlers == nil {
		p.directHandlers = make(map[string]http.HandlerFunc)
	}
	p.directHandlers[method+" "+path] = h
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

		// Fallback/direct forwarder to Python.
		forwardToPython := func(rw http.ResponseWriter, req *http.Request) {
			raw, err := p.py.DoRaw(req.Context(), method, full, req.Body)
			if err != nil {
				if be, ok := python.AsBoundary(err); ok {
					respond.ReplayBoundary(rw, req, be)
					return
				}
				// Legacy 4xx/5xx: Python ANSWERED. Replay its status + body
				// verbatim. Upgrading this to a synthesized v1 503 would report
				// an outage the upstream never had.
				if lr, ok := python.AsLegacy(err); ok {
					writeRawJSONStatus(rw, lr.Status, lr.Body)
					return
				}
				respond.FailDependencyUnavailable(rw, req, "upstream unavailable")
				return
			}

			// 204 No Content and empty bodies: nothing to decode. Serve the
			// upstream status as-is so DELETE etc. keep their contract.
			if len(raw) == 0 {
				rw.WriteHeader(http.StatusNoContent)
				return
			}

			if respond.IsV1Path(req.URL.Path) {
				var env pyEnvelope[json.RawMessage]
				if err := json.Unmarshal(raw, &env); err != nil {
					// Python did not return the v1 envelope. Serve the bytes
					// through rather than fabricating one, so a downstream format
					// change surfaces at the client instead of being masked.
					writeRawJSON(rw, http.StatusOK, raw)
					return
				}
				respond.OKWithMeta(rw, req, env.Data, env.Meta)
				return
			}

			// Legacy surface: bare payload, no envelope.
			writeRawJSON(rw, http.StatusOK, raw)
		}

		key := method + " " + path
		directH, hasDirect := p.directHandlers[key]
		target := routing.Route(method, path)

		// Wave 4: if direct serving is enabled and this candidate route has a registered direct handler,
		// serve it directly in Go with automatic fallback.
		if target == routing.TargetGoDirect && hasDirect {
			directH(w, r)
			return
		}

		// Non-candidate or candidate without direct handler: forward to Python with routing headers.
		reason := "unclassified"
		if d := routing.Decide(method, path); d.Classified {
			reason = d.Why
		}
		routing.SetRoutingHeaders(w, routing.TargetPythonProxy, reason)
		forwardToPython(w, r)
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
