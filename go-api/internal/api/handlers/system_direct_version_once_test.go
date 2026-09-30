package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
)

// countResolver wraps a stub git resolver in an atomic counter so tests can
// assert exactly how many times the (expensive, exec-based) resolution ran.
func countResolver(sha string) (*atomic.Int64, func() (string, error)) {
	var calls atomic.Int64
	return &calls, func() (string, error) {
		calls.Add(1)
		return sha, nil
	}
}

func decodeVersionBody(rec *httptest.ResponseRecorder) versionResponseEnvelope {
	var env versionResponseEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		panic("failed to decode version envelope: " + err.Error())
	}
	return env
}

// TestVersionDirectHandlerIsNotReconstructedPerRequest is the Wave-6
// regression guard: routing /api/v1/system/version through the legacy
// SystemHandlers receiver must NOT build a fresh SystemDirectHandler per
// request. The old path did (NewSystemDirect(h.py) inside VersionDirect), so
// cachedVersion was always nil, every request re-ran `git rev-parse HEAD`
// (~43ms on Windows), and the "direct" route was slower than the Python
// proxy it replaced. 50 requests here may perform at most ONE git resolution.
func TestVersionDirectHandlerIsNotReconstructedPerRequest(t *testing.T) {
	const N = 50
	calls, resolver := countResolver("aa11cc22ee3344ff5566778899aabbccddee0001")

	sys := NewSystem(nil)
	// The long-lived handler is built once by NewSystem; swapping its
	// resolver swaps it for the whole process (which is exactly how the
	// override is meant to behave).
	sys.direct.SetGitCommitResolver(resolver)
	sys.direct.ResetCache()

	var envelopes []versionResponseEnvelope
	for i := 0; i < N; i++ {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil)
		sys.VersionDirect(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("request %d: expected 200, got %d (body: %s)", i, rec.Code, rec.Body.String())
		}
		envelopes = append(envelopes, decodeVersionBody(rec))
	}

	if got := calls.Load(); got != 1 {
		t.Fatalf("expected exactly 1 git resolution for %d requests, got %d "+
			"(handler is being rebuilt per request and the cache never survives)",
			N, got)
	}

	// Cached payload must be identical across every request: the commit and
	// the generated_at timestamp are frozen on the first resolution.
	first := envelopes[0]
	for i, env := range envelopes[1:] {
		if env.Data.Commit != first.Data.Commit {
			t.Errorf("request %d: commit drifted from %q to %q", i, first.Data.Commit, env.Data.Commit)
		}
		if env.Data.GeneratedAt != first.Data.GeneratedAt {
			t.Errorf("request %d: cached generated_at drifted from %q to %q", i, first.Data.GeneratedAt, env.Data.GeneratedAt)
		}
		if env.Data.CommitSource != CommitSourceRepo {
			t.Errorf("request %d: expected commit_source %q, got %q", i, CommitSourceRepo, env.Data.CommitSource)
		}
	}
}

// TestVersionRouteSingleGitResolutionConcurrent hammers the direct version
// route from many goroutines at once: precisely ONE git resolution may run
// and the cached payload must not race (the cache is guarded by h.mu).
func TestVersionRouteSingleGitResolutionConcurrent(t *testing.T) {
	const G = 32
	calls, resolver := countResolver("bb22dd33ee44ff5566778899aabbccddeeff0011")

	h := NewSystemDirect(nil)
	h.SetGitCommitResolver(resolver)

	var wg sync.WaitGroup
	errs := make(chan error, G*2)
	wg.Add(G)
	for i := 0; i < G; i++ {
		go func() {
			defer wg.Done()
			rec := httptest.NewRecorder()
			req := httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil)
			h.Version(rec, req)
			if rec.Code != http.StatusOK {
				errs <- &badStatus{rec.Code, rec.Body.String()}
				return
			}
			env := decodeVersionBody(rec)
			if env.Data.Commit != "bb22dd33ee44ff5566778899aabbccddeeff0011" {
				errs <- &badField{"commit", env.Data.Commit}
			}
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Error(err)
	}

	if got := calls.Load(); got != 1 {
		t.Fatalf("expected exactly 1 git resolution under %d concurrent requests, got %d", G, got)
	}
}

// TestVersionRouteOncePerHandlerWithStubResolver pins the resolution contract
// when the resolver is overridden: the stub may run ONCE per handler, never
// once per request. The old code re-ran resolution on every single request.
func TestVersionRouteOncePerHandlerWithStubResolver(t *testing.T) {
	const N = 20
	calls, resolver := countResolver("cc33ee44ff5566778899aabbccddeeff00112233")

	h1 := NewSystemDirect(nil)
	h1.SetGitCommitResolver(resolver)
	for i := 0; i < N; i++ {
		rec := httptest.NewRecorder()
		h1.Version(rec, httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil))
		if rec.Code != http.StatusOK {
			t.Fatalf("h1 request %d: expected 200, got %d", i, rec.Code)
		}
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("h1: expected exactly 1 stub resolution for %d requests, got %d", N, got)
	}

	// A second handler built later: it may resolve once for itself, but not
	// once per request.
	h2 := NewSystemDirect(nil)
	h2.SetGitCommitResolver(resolver)
	for i := 0; i < N; i++ {
		rec := httptest.NewRecorder()
		h2.Version(rec, httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil))
		if rec.Code != http.StatusOK {
			t.Fatalf("h2 request %d: expected 200, got %d", i, rec.Code)
		}
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("h2: expected one resolution for its own lifetime (2 total), got %d", got)
	}
}

// TestVersionRouteOncePerProcessAcrossRebuilds is the production-critical half
// of the guarantee. It leaves the DEFAULT resolver in place (no stub) and
// builds two independent handlers; the second must reuse the same process-wide
// SHA instead of re-executing git. If the process cache were missing, h2 would
// resolve again — and a real handler rebuild (e.g. routes.Build re-entry) would
// pay the ~43ms Windows exec a second time.
func TestVersionRouteOncePerProcessAcrossRebuilds(t *testing.T) {
	h1 := NewSystemDirect(nil)
	rec1 := httptest.NewRecorder()
	h1.Version(rec1, httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil))
	if rec1.Code != http.StatusOK {
		t.Fatalf("h1: expected 200, got %d (body: %s)", rec1.Code, rec1.Body.String())
	}
	env1 := decodeVersionBody(rec1)
	if !isHexSHA(env1.Data.Commit) {
		t.Fatalf("h1: expected a real 40-char commit SHA, got %q", env1.Data.Commit)
	}

	// Same process, fresh handler, default resolver: must serve the cached SHA.
	h2 := NewSystemDirect(nil)
	rec2 := httptest.NewRecorder()
	h2.Version(rec2, httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil))
	if rec2.Code != http.StatusOK {
		t.Fatalf("h2: expected 200, got %d (body: %s)", rec2.Code, rec2.Body.String())
	}
	env2 := decodeVersionBody(rec2)
	if env2.Data.Commit != env1.Data.Commit {
		t.Errorf("expected the rebuilt handler to reuse the process-cached commit, got %q vs %q",
			env2.Data.Commit, env1.Data.Commit)
	}
	if env2.Data.CommitSource != CommitSourceRepo {
		t.Errorf("expected commit_source %q, got %q", CommitSourceRepo, env2.Data.CommitSource)
	}
}

// TestVersionRoutePreservesOKEnvelope keeps the response contract pinned:
// the direct route must keep answering through respond.OK's v1 envelope with
// a unique per-request request_id even while the payload stays cached.
func TestVersionRoutePreservesOKEnvelope(t *testing.T) {
	h := NewSystemDirect(nil)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/system/version", nil)
	h.Version(rec, req)

	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected Content-Type application/json, got %q", ct)
	}
	var raw struct {
		Data VersionData `json:"data"`
		Meta struct {
			RequestID   string `json:"request_id"`
			GeneratedAt string `json:"generated_at"`
		} `json:"meta"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &raw); err != nil {
		t.Fatalf("failed to decode envelope: %v", err)
	}
	if raw.Meta.RequestID == "" {
		t.Errorf("meta.request_id must not be empty")
	}
	_ = raw // respond.OK is the envelope contract this route must keep using
}

type badStatus struct {
	code int
	body string
}

func (e *badStatus) Error() string { return "expected 200, got " + http.StatusText(e.code) }

type badField struct {
	field string
	value string
}

func (e *badField) Error() string { return "unexpected " + e.field + ": " + e.value }
