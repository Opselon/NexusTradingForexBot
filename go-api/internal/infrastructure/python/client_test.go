// Package python tests for the Wave 6 boundary transport tuning.
package python

import (
	"net/http"
	"testing"
	"time"
)

// TestTransportKeepAliveEnabled pins the Wave 6 fix: the boundary client must
// use a transport whose idle connection pool is large enough for real
// concurrency and whose idle timeout keeps sockets alive ACROSS requests but
// below the upstream's own keep-alive window.
func TestTransportKeepAliveEnabled(t *testing.T) {
	tr := NewTransportForTest()

	trans := tr

	if trans.MaxIdleConnsPerHost < 32 {
		t.Errorf("MaxIdleConnsPerHost = %d, want >= 32 (pool must cover real concurrency)",
			trans.MaxIdleConnsPerHost)
	}
	if trans.DisableKeepAlives {
		t.Error("DisableKeepAlives = true; keep-alives must stay enabled so TCP " +
			"connections to the upstream are reused instead of re-dialed per request")
	}
	if trans.IdleConnTimeout <= 0 {
		t.Errorf("IdleConnTimeout = %v, want a bounded positive timeout", trans.IdleConnTimeout)
	}
	// Must stay strictly under uvicorn's default timeout_keep_alive (5s) or Go
	// would reuse a socket the upstream has already half-closed.
	if trans.IdleConnTimeout >= 5*time.Second {
		t.Errorf("IdleConnTimeout = %v, must stay below the upstream's 5s keep-alive window "+
			"(a pooled socket closed server-side yields spurious EOFs and retries)",
			trans.IdleConnTimeout)
	}
	if trans.ResponseHeaderTimeout <= 0 {
		t.Errorf("ResponseHeaderTimeout = %v, want bounded so a slow upstream cannot hold a pooled connection forever",
			trans.ResponseHeaderTimeout)
	}
}

// TestClientUsesSharedTunedTransport verifies the client actually installs the
// tuned transport, and that all clients share one pool.
func TestClientUsesSharedTunedTransport(t *testing.T) {
	c := New(Options{Origin: "http://127.0.0.1:8087"})
	trans, ok := c.Transport().(*http.Transport)
	if !ok {
		t.Fatalf("client transport is %T, want *http.Transport", c.Transport())
	}
	if trans.DisableKeepAlives {
		t.Error("client transport has keep-alives disabled")
	}
	if trans.MaxIdleConnsPerHost < 32 {
		t.Errorf("client MaxIdleConnsPerHost = %d, want >= 32", trans.MaxIdleConnsPerHost)
	}
	if trans.IdleConnTimeout <= 0 || trans.IdleConnTimeout >= 5*time.Second {
		t.Errorf("client IdleConnTimeout = %v, want bounded and under 5s", trans.IdleConnTimeout)
	}

	c2 := New(Options{Origin: "http://127.0.0.1:9999"})
	if c.Transport() != c2.Transport() {
		t.Error("two clients built in-process must share the one tuned transport " +
			"(the pool is what makes the proxy not pay TCP setup per request)")
	}
}

// TestClientNilSafeTransport covers the nil-receiver path.
func TestClientNilSafeTransport(t *testing.T) {
	var c *Client
	tr := c.Transport()
	if tr == nil {
		t.Fatal("Transport() must never return nil")
	}
	if trans, ok := tr.(*http.Transport); !ok || trans.DisableKeepAlives {
		t.Errorf("nil-receiver transport = %v, want a tuned *http.Transport with keep-alives on", tr)
	}
}
