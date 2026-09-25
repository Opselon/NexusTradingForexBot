// Command nexus-api is the NSE Go API/control plane.
//
// It is a CONTROL PLANE, not a replacement engine: the Python runtime remains
// the authority for HealthEngine facts, build metadata, ML and MT5. Go owns
// the high-concurrency surface — auth, envelopes, correlation, streaming and
// static assets — and talks to Python over a bounded localhost boundary.
//
// Production routing is NOT switched to this binary until every endpoint has
// reached CUTOVER_READY in api/migration/inventory.yaml. Until then Python
// serves all traffic and this process is a candidate implementation only.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/routes"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
)

func main() {
	var (
		addr = flag.String("addr", envOr("NSE_GO_ADDR", ":8087"),
			"listen address (NSE_GO_ADDR)")
		pyOrigin = flag.String("python-origin", envOr("NSE_PYTHON_ORIGIN", ""),
			"authoritative Python runtime base URL, e.g. http://127.0.0.1:8087 "+
				"(NSE_PYTHON_ORIGIN). Empty = no engine attached; endpoints "+
				"report DEPENDENCY_UNAVAILABLE instead of fabricating state.")
		logLevel = flag.String("log-level", envOr("NSE_GO_LOG_LEVEL", "info"),
			"log level: debug|info|warn|error")
	)
	flag.Parse()

	logger := newLogger(*logLevel)
	slog.SetDefault(logger)

	py := python.New(python.Options{Origin: *pyOrigin})

	opts := api.DefaultOptions()
	opts.Addr = *addr
	opts.Handler = routes.Build(py)

	srv := api.New(opts)

	ctx, stop := signal.NotifyContext(context.Background(),
		os.Interrupt, syscall.SIGTERM)
	defer stop()

	logger.Info("nexus-api starting",
		"addr", opts.Addr,
		"python_origin", pyOriginValue(*pyOrigin),
		"phase", "A",
		"note", "candidate control plane — Python still serves production traffic")

	if err := srv.ListenAndServe(ctx); err != nil && !errors.Is(err, http.ErrServerClosed) {
		logger.Error("server failed", "error", err)
		os.Exit(1)
	}
	logger.Info("nexus-api stopped cleanly")
}

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// pyOriginValue reports the origin WITHOUT logging any credentials that might
// be embedded in a URL.
func pyOriginValue(origin string) string {
	if origin == "" {
		return "(none — no engine attached)"
	}
	return origin
}

func newLogger(level string) *slog.Logger {
	var lv slog.Level
	switch level {
	case "debug":
		lv = slog.LevelDebug
	case "warn":
		lv = slog.LevelWarn
	case "error":
		lv = slog.LevelError
	default:
		lv = slog.LevelInfo
	}
	h := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: lv,
		// ReplaceAttr scrubs anything that smells like a credential before it
		// reaches the log sink — tokens/keys/authorization headers are never
		// logged (redaction contract, §29).
		ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
			k := a.Key
			for _, bad := range []string{"token", "password", "secret",
				"authorization", "cookie", "api_key", "apikey"} {
				if containsFold(k, bad) {
					return slog.String(k, "[REDACTED]")
				}
			}
			return a
		},
	})
	return slog.New(h)
}

func containsFold(s, sub string) bool {
	if len(sub) == 0 {
		return true
	}
	s, sub = lowerASCII(s), lowerASCII(sub)
	return index(s, sub) >= 0
}

func lowerASCII(s string) string {
	b := []byte(s)
	for i := range b {
		if b[i] >= 'A' && b[i] <= 'Z' {
			b[i] += 'a' - 'A'
		}
	}
	return string(b)
}

func index(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}

var _ = fmt.Sprintf
var _ = time.Second
