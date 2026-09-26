package routing

import "testing"

// TestDecideRequiresPython pins the three classes that MUST forward. Each
// is a route the profiler actually observed touching the relational store
// or that the classifier could not prove stateless - so serving any of them
// from Go would ship a stub, an empty history, or a stale constant.
func TestDecideRequiresPython(t *testing.T) {
	cases := []struct {
		method, path, why string
	}{
		// Observed relational access: Go has no database driver, by design.
		{"GET", "/api/db/manage/validate", "observed:db"},
		{"DELETE", "/api/model-studio/models/{model_id}", "observed:db"},
		// Masked stub: 200 ENGINE_UNAVAILABLE under the probe, real data with
		// an engine attached.
		{"GET", "/api/account/drawdown", "stub-in-2xx"},
		{"GET", "/api/account/performance", "stub-in-2xx"},
		// Empty answer: [] with no engine, audit rows with one.
		{"GET", "/api/account/trades", "empty-in-2xx"},
		// A write with no observed DB hit: not safely replayable, so the
		// only defensible answer. (A write that also hits the DB reports
		// observed:db, which wins.)
		{"POST", "/api/db/manage/backup", "observed:db"},
	}
	for _, c := range cases {
		d := Decide(c.method, c.path)
		if !d.Classified {
			t.Errorf("Decide(%s,%s): Classified=false, want true", c.method, c.path)
			continue
		}
		if !d.NeedsPython {
			t.Errorf("Decide(%s,%s): NeedsPython=false, want true (%s)",
				c.method, c.path, d.Why)
		}
		if d.Why != c.why {
			t.Errorf("Decide(%s,%s): Why=%q, want %q", c.method, c.path, d.Why, c.why)
		}
		if Candidate(c.method, c.path) {
			t.Errorf("Candidate(%s,%s) = true, want false", c.method, c.path)
		}
	}
}

// TestDecideCandidates pins routes that answered a substantive, stateless
// 2xx and touched no subsystem. These are the SERVING CANDIDATES - the
// population a later wave may serve from Go after a parity gate.
func TestDecideCandidates(t *testing.T) {
	cases := []struct {
		method, path, why string
	}{
		{"GET", "/api/algo/config", "stateless-2xx"},
		{"GET", "/api/ai-providers", "stateless-2xx"},
	}
	for _, c := range cases {
		d := Decide(c.method, c.path)
		if !d.Classified {
			t.Errorf("Decide(%s,%s): Classified=false, want true", c.method, c.path)
		}
		if d.NeedsPython {
			t.Errorf("Decide(%s,%s): NeedsPython=true, want false (%s)",
				c.method, c.path, d.Why)
		}
		if d.Why != c.why {
			t.Errorf("Decide(%s,%s): Why=%q, want %q", c.method, c.path, d.Why, c.why)
		}
		if !Candidate(c.method, c.path) {
			t.Errorf("Candidate(%s,%s) = false, want true", c.method, c.path)
		}
	}
}

// TestUnknownRouteForwards is the safety floor: a route the table never
// classified must forward, and must NOT be a candidate. An absent entry may
// mean "new route the profiler has not seen", which is exactly the case
// where the proxy is the correct default.
func TestUnknownRouteForwards(t *testing.T) {
	d := Decide("GET", "/api/does-not-exist-yet")
	if d.Classified {
		t.Error("Decide(unknown): Classified=true, want false")
	}
	if !d.NeedsPython {
		t.Error("Decide(unknown): NeedsPython=false, want true (forward)")
	}
	if Candidate("GET", "/api/does-not-exist-yet") {
		t.Error("Candidate(unknown) = true, want false")
	}
}

// TestTableCompleteness is the structural invariant: every entry has a
// method and path, and the Why field is one the classifier actually emits.
// A new Why appearing here means the generator and classifier drifted.
func TestTableCompleteness(t *testing.T) {
	valid := map[string]bool{
		"observed:db":   true,
		"write:assumed": true,
		"stateless-2xx": true,
		"stub-in-2xx":   true,
		"empty-in-2xx":  true,
		"failed-in-2xx": true,
		"non-2xx:422":   true,
		"non-2xx:404":   true,
		"non-2xx:503":   true,
	}
	n := 0
	for key, e := range DepsTable {
		n++
		if e.Method == "" || e.Path == "" {
			t.Errorf("DepsTable[%q]: empty Method or Path", key)
		}
		if e.Method+" "+e.Path != key {
			t.Errorf("DepsTable[%q]: key does not match Method+Path (%s %s)",
				key, e.Method, e.Path)
		}
		if !valid[e.Why] {
			t.Errorf("DepsTable[%q]: unknown Why %q", key, e.Why)
		}
	}
	if n < 100 {
		t.Errorf("DepsTable has %d entries, want >= 100 (did the generator run?)", n)
	}
}
