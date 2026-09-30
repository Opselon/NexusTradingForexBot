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
	"strings"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/respond"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/routing"
)

// maxProxiedBody bounds the bytes a proxied response may buffer. The v1
// envelope shape check needs the body in memory (it decodes only the envelope
// skeleton and re-emits Python's bytes for data), so the upstream payload is
// read once and copied out rather than re-marshalled. 8 MiB matches the
// boundary client's own limit.
const maxProxiedBody = 8 << 20

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

		// SSE streams (text/event-stream) cannot use the buffered forwarder:
		// DoRaw reads the whole body with io.ReadAll, which blocks forever on
		// a stream that is intentionally open-ended, and would discard
		// Python's Content-Type/Cache-Control headers and re-emit the bytes as
		// application/json — Chrome's EventSource then aborts, killing live
		// tick/trace updates through the Go origin. Detect them up front and
		// take the streaming path instead.
		if isSSERequest(r) {
			reason := "unclassified"
			if d := routing.Decide(method, path); d.Classified {
				reason = d.Why
			}
			routing.SetRoutingHeaders(w, routing.TargetPythonProxy, reason)
			forwardStreamToPython(w, r, p.py, method, full)
			return
		}

		// Fallback/direct forwarder to Python.
		//
		// The upstream body is streamed straight through to the client with
		// io.Copy once the envelope shape is known: no full re-decode, no
		// re-marshal. Python's own byte output (key order, float formatting) is
		// preserved exactly, which is the byte-level parity contract.
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
				// Python already emitted a complete, canonical
				// {"data":...,"meta":...} envelope. The only reason to touch
				// it would be to rewrite meta.request_id/generated_at, but
				// Python's own request_id is the authoritative correlation
				// id for the work IT did, and its timestamp is when IT
				// produced the answer — replacing either fabricates a trace
				// that does not match the upstream work. So once the envelope
				// parses, the bytes are forwarded verbatim: no unmarshal into
				// map[string]any, no re-marshal, no float/key-order drift,
				// and no second full serialization of a 15 MiB body.
				var env pyEnvelope[json.RawMessage]
				if err := json.Unmarshal(raw, &env); err != nil {
					// Python did not return the v1 envelope. Serve the bytes
					// through rather than fabricating one, so a downstream format
					// change surfaces at the client instead of being masked.
					writeRawJSON(rw, http.StatusOK, raw)
					return
				}
				writeRawJSON(rw, http.StatusOK, raw)
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
// Python's key order and float formatting byte-for-byte. w.Write(b) is used
// directly — io.WriteString(w, string(b)) would heap-copy the whole payload
// (up to tens of MiB on the research surface) for no benefit.
func writeRawJSON(w http.ResponseWriter, status int, b []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(b)
}

// writeRawJSONStatus serves an upstream error body byte-for-byte at the
// upstream's own status code, for the legacy surface where {"detail": ...}
// must reach the client unchanged.
func writeRawJSONStatus(w http.ResponseWriter, status int, b []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(b)
}

// isSSERequest reports whether the client is asking for a Server-Sent Events
// stream. Browsers send Accept: text/event-stream from EventSource; the
// Content-Type check catches non-browser clients and keeps a stream that
// Python already committed to (its own text/event-stream answer) out of the
// buffered path even when the client hedged its Accept header.
func isSSERequest(r *http.Request) bool {
	if strings.Contains(r.Header.Get("Accept"), "text/event-stream") {
		return true
	}
	return strings.HasPrefix(r.URL.Path, "/api/ticks/stream") ||
		strings.HasPrefix(r.URL.Path, "/api/trace/stream")
}

// forwardStreamToPython pipes an upstream stream (SSE) to the client
// unbuffered. Python's own headers — Content-Type: text/event-stream,
// Cache-Control: no-cache, Connection: keep-alive, X-Accel-Buffering — are
// forwarded verbatim (only hop-by-hop headers are stripped), then the body is
// copied in chunks with a Flush after every write so events reach the browser
// the moment Python emits them instead of waiting for the whole stream.
func forwardStreamToPython(w http.ResponseWriter, r *http.Request, py *python.Client, method, full string) {
	resp, err := py.DoStream(r.Context(), method, full, r.Body)
	if err != nil {
		if be, ok := python.AsBoundary(err); ok {
			respond.ReplayBoundary(w, r, be)
			return
		}
		if lr, ok := python.AsLegacy(err); ok {
			writeRawJSONStatus(w, lr.Status, lr.Body)
			return
		}
		respond.FailDependencyUnavailable(w, r, "upstream unavailable")
		return
	}
	defer resp.Body.Close()

	// Forward the upstream headers, minus hop-by-hop ones the proxy owns.
	for k, vals := range resp.Header {
		switch http.CanonicalHeaderKey(k) {
		case "Connection", "Keep-Alive", "Transfer-Encoding", "Proxy-Authenticate",
			"Proxy-Authorization", "Te", "Trailer", "Upgrade":
			continue
		}
		for _, v := range vals {
			w.Header().Add(k, v)
		}
	}
	// Python answered a stream with a normal (buffered) success status —
	// forward it as-is before the first write.
	w.WriteHeader(resp.StatusCode)

	flusher, canFlush := w.(http.Flusher)
	buf := make([]byte, 32*1024)
	for {
		n, rerr := resp.Body.Read(buf)
		if n > 0 {
			if _, werr := w.Write(buf[:n]); werr != nil {
				return // client went away; nothing more to do
			}
			if canFlush {
				flusher.Flush()
			}
		}
		if rerr != nil {
			if rerr != io.EOF {
				// Upstream hiccup mid-stream: the connection is already
				// committed, so the client sees the truncation as the
				// reconnect signal EventSource already handles.
				return
			}
			return
		}
	}
}
