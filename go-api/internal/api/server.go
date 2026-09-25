// Package api wires the NSE Go control plane: net/http server, middleware
// chain, and the route table.
package api

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// Server is the NSE Go API control plane. It owns its lifecycle: bounded
// timeouts, graceful shutdown, and a single cancelable context tree for every
// request-bound goroutine it spawns.
type Server struct {
	httpServer *http.Server
}

// Options configures the server. All have safe defaults.
type Options struct {
	// Addr is the bind address. Empty means the NSE canonical default.
	Addr string
	// Handler, when set, overrides the default ServeMux. Use this to install
	// the full middleware chain built by routes.Build.
	Handler http.Handler
	// ReadTimeout caps the full request read (headers + body). The legacy
	// server has no explicit cap; we add one because an unbounded read is a
	// trivial DoS vector on a control plane exposed to a LAN.
	ReadTimeout time.Duration
	// WriteTimeout caps response writing, INCLUDING streaming. SSE handlers
	// must therefore run their own cancellation (they cannot outlive this).
	WriteTimeout time.Duration
	// IdleTimeout caps keep-alive idle connections.
	IdleTimeout time.Duration
	// MaxHeaderBytes bounds request header size.
	MaxHeaderBytes int
	// MaxBodyBytes bounds request body size for JSON endpoints.
	MaxBodyBytes int64
}

// DefaultOptions matches the documented NSE runtime port and safe bounds.
func DefaultOptions() Options {
	return Options{
		Addr:           ":8087",
		ReadTimeout:    15 * time.Second,
		WriteTimeout:   60 * time.Second,
		IdleTimeout:    120 * time.Second,
		MaxHeaderBytes: 1 << 20, // 1 MiB
		MaxBodyBytes:   8 << 20, // 8 MiB
	}
}

// New builds the server. opts.Handler installs the full route+middleware
// chain (routes.Build); a nil handler falls back to an empty ServeMux.
func New(opts Options) *Server {
	if opts.Handler == nil {
		opts.Handler = http.NewServeMux()
	}
	srv := &Server{
		httpServer: &http.Server{
			Addr:              opts.Addr,
			Handler:           opts.Handler,
			ReadHeaderTimeout: 10 * time.Second,
			ReadTimeout:       opts.ReadTimeout,
			WriteTimeout:      opts.WriteTimeout,
			IdleTimeout:       opts.IdleTimeout,
			MaxHeaderBytes:    opts.MaxHeaderBytes,
		},
	}
	return srv
}

// Handler returns the root http.Handler (for tests and composition).
func (s *Server) Handler() http.Handler { return s.httpServer.Handler }

// Addr returns the configured bind address.
func (s *Server) Addr() string { return s.httpServer.Addr }

// ListenAndServe starts serving. It blocks until Shutdown is called.
func (s *Server) ListenAndServe(ctx context.Context) error {
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = s.httpServer.Shutdown(shutdownCtx)
	}()
	err := s.httpServer.ListenAndServe()
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}
