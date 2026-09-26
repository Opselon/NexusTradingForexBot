"""Wave 3: emit the Go-side dependency-aware routing table.

Input  : api/migration/route_dependencies.json (classified)
Output : go-api/internal/routing/deps_table.go

The table tells the Go plane, per route, whether the request MUST be
forwarded to Python or is a Go-servable candidate. This is the data that
makes "route perfectly in future" possible: instead of treating all 466
routes as an opaque proxy surface, Go knows which ones carry a real
dependency on the Python/DB plane.

Design constraints baked into the generator:

  * GENERATED, not handwritten - same discipline as the route table. The
    source of truth is the runtime profiler, and regenerating is a one
    command (profile -> classify -> emit).
  * The table is ADVISORY for now: every route still proxies to Python
    (Wave 3 classifies; serving is a later wave). Getting the
    classification wrong has no runtime cost today and builds real
    evidence for the serving decision.
  * needs_python=false means "candidate", never "safe to serve". The
    classifier already rejects stubs, empty answers and self-reported
    failures; the Go side treats the flag as a HINT, not an authority.
  * Unknown routes (not present in the table) default to needs_python=true
    because the proxy is the correct default for anything unclassified.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

MAIN = Path(r"C:/Users/Capsizer/source/repos/nse-api-golang-migration")
SRC = MAIN / "api" / "migration" / "route_dependencies.json"
OUT = MAIN / "go-api" / "internal" / "routing" / "deps_table.go"

HEADER = """// Code generated from api/migration/route_dependencies.json. DO NOT EDIT.
//
// Source: the runtime dependency profiler (scratch/profile_route_deps.py) +
// classifier (scratch/classify_route_deps.py), which replay every route
// against a live FastAPI app and record which subsystems the handler
// actually touched.
//
// Wave 3: dependency-aware routing. needs_python=true means the handler
// was observed to read/write the relational store, was a write (assumed),
// or answered with a masked stub/empty body under the probe - it MUST be
// forwarded. needs_python=false means the route answered a substantive,
// stateless 2xx and is a CANDIDATE for serving from Go.
//
// This table is ADVISORY: every route still proxies to Python. The flag is
// evidence for the future serving decision, not permission to serve - a
// candidate is only served after a separate parity gate proves the body.
package routing

// DepEntry is the per-route dependency classification.
type DepEntry struct {
\tMethod      string
\tPath        string
\tNeedsPython bool
\tWhy         string
}

// DepsTable maps "METHOD /path" to its dependency classification. Routes
// absent from this table are unclassified and must forward to Python.
var DepsTable = map[string]DepEntry{
"""

FOOTER = """}

// NeedsPython reports whether a request must be forwarded to the Python
// plane. Unknown routes forward: the proxy is the safe default for
// anything this table does not classify.
func NeedsPython(method, path string) bool {
\tif e, ok := DepsTable[method+" "+path]; ok {
\t\treturn e.NeedsPython
\t}
\treturn true
}
"""


def go_literal(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def main() -> int:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    routes = data["routes"]

    entries = []
    for key, rec in sorted(routes.items()):
        method, path = key.split(" ", 1)
        entries.append(
            f'\t"{go_literal(key)}": {{Method: "{method}", '
            f'Path: "{go_literal(path)}", NeedsPython: '
            f"{'true' if rec['needs_python'] else 'false'}, "
            f'Why: "{rec.get("why", "")}"}},'
        )

    summary = Counter(rec["why"] for rec in routes.values())
    n_np = sum(1 for rec in routes.values() if rec["needs_python"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        HEADER + "\n".join(entries) + "\n" + FOOTER,
        encoding="utf-8",
        newline="\n",
    )

    print(f"routes emitted    : {len(entries)}")
    print(f"needs Python      : {n_np}")
    print(f"Go candidates     : {len(entries) - n_np}")
    print(f"by reason         : {dict(summary)}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
