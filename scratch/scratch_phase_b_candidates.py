"""Phase B candidate selection: read-only, GET-only endpoints with React
consumers, ranked by consumer count so we migrate the most-used surface
first (spec §6, §23).

Excludes anything with a mutation verb (POST/PUT/DELETE) — those are the
high-risk Phase J group — and anything already proven.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent
INV = REPO / "api" / "migration" / "inventory.yaml"


def main() -> None:
    lines = INV.read_text(encoding="utf-8").splitlines()

    cur_domain = None
    cur_method = None
    entries = []
    for line in lines:
        dm = re.match(r'^domain "(.*)":', line)
        if dm:
            cur_domain = dm.group(1)
            continue
        mm = re.match(r"\s*- method:\s*(\S+)\s*$", line)
        if mm:
            cur_method = mm.group(1)
            continue
        pm = re.match(r"\s*path:\s*(.+?)\s*$", line)
        if not (pm and cur_method and cur_domain):
            continue
        path = pm.group(1).strip().strip('"')
        entries.append((cur_domain, cur_method, path, line))

    # Collect per-entry metadata by re-reading the block that follows.
    blocks = []
    cur = None
    cur_dom = None
    cur_meth = None
    for line in lines:
        dm = re.match(r'^domain "(.*)":', line)
        if dm:
            cur_dom = dm.group(1)
            continue
        mm = re.match(r"\s*- method:\s*(\S+)\s*$", line)
        if mm:
            cur_meth = mm.group(1)
            cur = {"domain": cur_dom, "method": cur_meth, "consumers": []}
            blocks.append(cur)
            continue
        if cur is None:
            continue
        pm = re.match(r"\s*path:\s*(.+?)\s*$", line)
        if pm and "path" not in cur:
            cur["path"] = pm.group(1).strip().strip('"')
            continue
        rm = re.match(r"\s*-\s*path:\s*(\S+)\s*$", line)
        if rm:
            cur["consumers"].append(rm.group(1))
            continue
        cm = re.match(r"\s*react_consumers:\s*$", line)
        if cm:
            continue

    # Simpler: re-parse with full block capture.
    blocks = []
    cur_dom = None
    cur = None
    for line in lines:
        dm = re.match(r'^domain "(.*)":', line)
        if dm:
            cur_dom = dm.group(1)
            continue
        mm = re.match(r"\s*- method:\s*(\S+)\s*$", line)
        if mm:
            if cur is not None:
                blocks.append(cur)
            cur = {"domain": cur_dom, "method": mm.group(1),
                   "consumers": [], "kind": "http"}
            continue
        if cur is None:
            continue
        pm = re.match(r"\s*path:\s*(.+?)\s*$", line)
        if pm and "path" not in cur:
            cur["path"] = pm.group(1).strip().strip('"')
            continue
        if re.match(r"\s*kind:\s*(\S+)", line):
            cur["kind"] = re.match(r"\s*kind:\s*(\S+)", line).group(1)
            continue
        # react consumer list entries are "- frontend/src/..."
        rm = re.match(r"\s*-\s*(frontend/\S+)\s*$", line)
        if rm:
            cur["consumers"].append(rm.group(1))
            continue
        # state line ends the useful part
        sm = re.match(r"\s*state:\s*(\S+)", line)
        if sm:
            cur["state"] = sm.group(1)
    if cur is not None:
        blocks.append(cur)

    get_only = [b for b in blocks if b["method"] == "GET" and b["kind"] == "http"]
    print(f"GET-only http entries: {len(get_only)}")
    with_react = [b for b in get_only if b.get("consumers")]
    print(f"GET-only WITH react consumers: {len(with_react)}")

    by_domain = defaultdict(list)
    for b in with_react:
        by_domain[b["domain"]].append(b)

    print("\n=== candidate domains (GET-only, React-consumed), by route count ===")
    for dom in sorted(by_domain, key=lambda d: -len(by_domain[d])):
        routes = by_domain[dom]
        total_consumers = sum(len(r["consumers"]) for r in routes)
        print(f"  {dom:28s} routes={len(routes):3d} consumer_sites={total_consumers:3d}")
        for r in sorted(routes, key=lambda r: -len(r["consumers"]))[:4]:
            print(f"      {r['path']:48s} <- {len(r['consumers'])} sites")


if __name__ == "__main__":
    main()
