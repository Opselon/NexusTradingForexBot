"""Flip Phase B route states to PARITY_VERIFIED in inventory.yaml.

Evidence: api/migration/parity/phase_b_parity.json (21/21 paired probes at
parity). Only routes whose base path appears in the PASS set are flipped;
error-surface probes (404/405/422 cases) have no inventory entry.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent
INV = REPO / "api" / "migration" / "inventory.yaml"
PARITY = REPO / "api" / "migration" / "parity" / "phase_b_parity.json"


def main() -> int:
    raw = INV.read_text(encoding="utf-8")
    crlf = "\r\n" in raw
    lines = raw.split("\r\n") if crlf else raw.split("\n")

    evidence = json.loads(PARITY.read_text(encoding="utf-8"))
    passing = {d["path"].split("?")[0] for d in evidence
               if d["verdict"] == "PASS" and d["method"] == "GET"}
    print(f"PASS base paths: {len(passing)}")

    changed = 0
    cur_method = None
    cur_path = None
    for i, line in enumerate(lines):
        mm = re.match(r"\s*- method:\s*(\S+)\s*$", line)
        if mm:
            cur_method = mm.group(1)
            cur_path = None
            continue
        pm = re.match(r"\s*path:\s*(.+?)\s*$", line)
        if not (pm and cur_method):
            continue
        cur_path = pm.group(1).strip().strip('"')
        if cur_path not in passing:
            continue
        j = i + 1
        while j < len(lines):
            if re.match(r"\s*- method:", lines[j]) or lines[j].startswith("domain "):
                break
            sm = re.match(r"(\s*state:\s*)(\S+)", lines[j])
            if sm:
                if sm.group(2) != "PARITY_VERIFIED":
                    lines[j] = f"{sm.group(1)}PARITY_VERIFIED"
                    changed += 1
                break
            j += 1

    out = ("\r\n" if crlf else "\n").join(lines)
    INV.write_text(out, encoding="utf-8", newline="")
    print(f"state fields changed: {changed}")
    print("DISCOVERED remaining:", out.count("state: DISCOVERED"))
    print("PARITY_VERIFIED now :", out.count("state: PARITY_VERIFIED"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
