"""Flip the 10 Phase A endpoint states to PARITY_VERIFIED in inventory.yaml.

Evidence: api/migration/parity/phase_a_parity.json (14/14 probes at parity
against a live create_app() instance on the same machine/config/dataset).
All other 432 entries stay DISCOVERED — no endpoint is marked past what has
actually been proven.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent
INV = REPO / "api" / "migration" / "inventory.yaml"
PARITY = REPO / "api" / "migration" / "parity" / "phase_a_parity.json"


def main() -> int:
    raw = INV.read_text(encoding="utf-8")
    # Preserve CRLF exactly as found.
    crlf = "\r\n" in raw
    lines = raw.split("\r\n") if crlf else raw.split("\n")

    evidence = json.loads(PARITY.read_text(encoding="utf-8"))
    passing = {(d["method"], d["path"].split("?")[0]) for d in evidence
               if d["verdict"] == "PASS"
               and not d["path"].startswith("/api/v1/system")}
    print(f"evidence: {len(passing)} distinct routes PASS")

    changed = 0
    cur_method = None
    for i, line in enumerate(lines):
        m = re.match(r"\s*- method:\s*(\S+)\s*$", line)
        if m:
            cur_method = m.group(1)
            continue
        m = re.match(r"\s*path:\s*(.+?)\s*$", line)
        if not (m and cur_method):
            continue
        path = m.group(1).strip().strip('"')
        if (cur_method, path) not in passing:
            continue
        # Update the 'state:' line that follows within this route block.
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
    return 0 if changed == len(passing) else 2


if __name__ == "__main__":
    raise SystemExit(main())
