"""Build docs/testing/test_protection.json — the machine-readable delete-protection ledger.

Answer to the future-agent question "Can I delete this test?" from repository
evidence alone (brief §32). Sources, in precedence order:
  1. docs/testing/critical_regressions.md  — every test named in a CR row is PROTECTED
     (may change tier only with the row edited by the same change).
  2. scripts/testing/verdicts_merged.json  — forensic verdicts (KEEP/DELETE-CHECK etc.).
  3. tests/*_suite.txt manifests            — current tier placement.
Output schema (per file):
  {"file": {"protection": "PROTECTED|REVIEWED|UNREVIEWED",
            "cr": ["CR-0xx",...], "verdict": "...", "category": "...",
            "tier": "T1|T2|T3|T4|none", "cost_s": float|null,
            "rule": "what a future agent may/may not do"}}
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "docs/testing/critical_regressions.md"
VERDICTS = ROOT / "scratch/recon/verdicts_merged.json"
OUT = ROOT / "docs/testing/test_protection.json"

TIER_OF = {
    "tests/critical_suite.txt": "T1",
    "tests/extended_suite.txt": "T2",
    "tests/slow_suite.txt": "T4",
    "tests/windows_skew_suite.txt": "T3",
    "tests/fast_suite.txt": "T0",
}


def cr_map():
    """file -> [CR ids] from the registry table rows."""
    out: dict[str, list[str]] = {}
    for line in REG.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| CR-"):
            continue
        cid = line.split("|")[1].strip()
        for f in re.findall(r"`(test_[\w.]+\.py)`", line):
            # registry cites bare filenames; resolve against tests/ tree
            hits = list((ROOT / "tests").rglob(f))
            for h in hits:
                rel = h.relative_to(ROOT).as_posix()
                out.setdefault(rel, []).append(cid)
    return out


def tiers():
    out: dict[str, str] = {}
    for man, tier in TIER_OF.items():
        p = ROOT / man
        if not p.exists():
            continue
        for l in p.read_text(encoding="utf-8").splitlines():
            f = l.split("#")[0].strip()
            if f:
                cur = out.get(f)
                # lowest tier number wins (fastest lane membership is the claim)
                order = ["T0", "T1", "T2", "T3", "T4"]
                out[f] = min(cur or tier, tier, key=order.index)
    return out


def main():
    crs = cr_map()
    verdicts = json.loads(VERDICTS.read_text(encoding="utf-8")) if VERDICTS.exists() else {}
    tiers_ = tiers()
    ledger = {}
    files = set(crs) | set(verdicts) | set(tiers_)
    # plus every existing test file on disk (UNREVIEWED default)
    for p in (ROOT / "tests").rglob("test_*.py"):
        files.add(p.relative_to(ROOT).as_posix())
    for f in sorted(files):
        v = verdicts.get(f, {})
        entry = {
            "protection": "UNREVIEWED",
            "cr": crs.get(f, []),
            "verdict": v.get("classification"),
            "category": v.get("category"),
            "tier": tiers_.get(f, "none"),
            "cost_s": v.get("cost_s"),
            "rule": "No CR row and no forensic verdict: may be deleted ONLY with new "
            "evidence recorded in test_value_matrix.md (what real defect it "
            "could not catch). Prefer demote-to-T3/T4 over delete.",
        }
        if entry["cr"]:
            entry["protection"] = "PROTECTED"
            entry["rule"] = (
                "Named in critical_regressions.md ("
                + ", ".join(entry["cr"])
                + "). NEVER delete. Tier may change only by editing the CR row "
                "in the same commit (movement must preserve execution: T2+ "
                "lanes run on every main push)."
            )
        elif v:
            entry["protection"] = "REVIEWED"
            c = v.get("classification", "")
            if c == "DELETE":
                entry["rule"] = (
                    "Forensically deleted with replacement recorded; re-adding requires new evidence."
                )
            elif c in ("KEEP", "KEEP-PROMOTE", "PROMOTE-GATE"):
                entry["rule"] = (
                    "Reviewed KEEP: deleting requires proving stronger coverage exists "
                    "(see value_flags.stronger_exists) and recording it in the value matrix."
                )
            elif c == "DELETE-CHECK":
                entry["rule"] = (
                    "Flagged delete candidate BUT replacement coverage is MISSING — resolve the gap first."
                )
            else:
                entry["rule"] = (
                    "Reviewed " + (c or "?") + ": follow the verdict's `reason` before acting."
                )
        ledger[f] = entry
    OUT.write_text(
        json.dumps(
            {
                "generated_by": "scripts/testing/build_protection_ledger.py",
                "counts": {
                    "PROTECTED": sum(1 for e in ledger.values() if e["protection"] == "PROTECTED"),
                    "REVIEWED": sum(1 for e in ledger.values() if e["protection"] == "REVIEWED"),
                    "UNREVIEWED": sum(
                        1 for e in ledger.values() if e["protection"] == "UNREVIEWED"
                    ),
                },
                "files": ledger,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print("wrote", OUT, json.dumps(json.loads(OUT.read_text(encoding="utf-8"))["counts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
