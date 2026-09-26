// Package routing makes the per-route dependency classification available
// to the proxy, so Go knows which requests MUST reach Python and which are
// candidates for local serving.
package routing

// Decision explains why a request was routed the way it was. It is recorded
// on every proxied response so an operator can see the classification in
// flight instead of guessing from the table alone.
type Decision struct {
	// NeedsPython is true when the route was classified as carrying a real
	// dependency on the Python plane (DB access, a write, or a masked stub
	// under the probe).
	NeedsPython bool
	// Classified is false when the route is absent from the table: the
	// request forwards, but the caller knows the answer is the safe default
	// rather than an observed fact.
	Classified bool
	// Why is the classifier's reason (observed:db, write:assumed,
	// stateless-2xx, stub-in-2xx, empty-in-2xx, failed-in-2xx, non-2xx:NNN).
	Why string
}

// Decide returns the routing decision for a method+path.
//
// Unknown routes forward (NeedsPython true, Classified false): the proxy is
// the correct default for anything this table does not classify, and an
// unclassified route must never be served locally on a hint's absence.
func Decide(method, path string) Decision {
	e, ok := DepsTable[method+" "+path]
	if !ok {
		return Decision{NeedsPython: true, Classified: false, Why: "unclassified"}
	}
	return Decision{
		NeedsPython: e.NeedsPython,
		Classified:  true,
		Why:         e.Why,
	}
}

// Candidate reports whether a route is a GO-SERVABLE CANDIDATE: classified,
// and its dependency profile does not require Python. It is a HINT, not
// permission: a candidate is only served after a separate parity gate
// proves the served body matches Python's answer.
func Candidate(method, path string) bool {
	d := Decide(method, path)
	return d.Classified && !d.NeedsPython
}
