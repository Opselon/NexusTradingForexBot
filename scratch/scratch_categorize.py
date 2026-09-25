"""Categorize the remaining DISCOVERED routes to plan batch migration.

Groups by (surface, method, has-path-params, kind) so we can size each batch
and spot what needs special handling (mutations, websockets, SSE).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent
INV = REPO / "api" / "migration" / "inventory.yaml"


def main() -> None:
    lines = INV.read_text(encoding="utf-8").splitlines()
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
            cur = {"domain": cur_dom, "method": mm.group(1), "consumers": []}
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
        rm = re.match(r"\s*-\s*(frontend/\S+)\s*$", line)
        if rm:
            cur["consumers"].append(rm.group(1))
            continue
        sm = re.match(r"\s*state:\s*(\S+)", line)
        if sm:
            cur["state"] = sm.group(1)
    if cur is not None:
        blocks.append(cur)

    pending = [b for b in blocks if b.get("state") == "DISCOVERED"]
    print(f"total blocks: {len(blocks)}  pending: {len(pending)}")

    print("\n=== by kind x method ===")
    c = Counter((b.get("kind", "?"), b["method"]) for b in pending)
    for k in sorted(c):
        print(f"  {k[0]:8s} {k[1]:8s} {c[k]:4d}")

    print("\n=== by surface x method ===")
    c = Counter((b["domain"].split(":")[0], b["method"]) for b in pending)
    for k in sorted(c):
        print(f"  {k[0]:10s} {k[1]:8s} {c[k]:4d}")

    # Path params => needs dynamic routing
    with_params = [b for b in pending if "{" in b.get("path", "")]
    print(f"\n=== paths with params: {len(with_params)} ===")
    for b in with_params[:25]:
        print(f"  {b['method']:6s} {b['path']}")

    # Domains with any mutation
    dom_methods = defaultdict(set)
    for b in pending:
        dom_methods[b["domain"]].add(b["method"])
    mixed = {d: m for d, m in dom_methods.items()
             if m & {"POST", "PUT", "DELETE"}}
    print(f"\n=== domains with mutations: {len(mixed)} ===")
    for d in sorted(mixed):
        print(f"  {d:28s} {sorted(mixed[d])}")

    pure_get = {d: m for d, m in dom_methods.items() if m == {"GET"}}
    print(f"\n=== pure-GET domains: {len(pure_get)} ===")
    n = sum(1 for b in pending if b["domain"] in pure_get)
    print(f"  (accounts for {n} routes)")
    for d in sorted(pure_get):
        print(f"  {d:28s} {sum(1 for b in pending if b['domain']==d)} routes")


if __name__ == "__main__":
    main()
