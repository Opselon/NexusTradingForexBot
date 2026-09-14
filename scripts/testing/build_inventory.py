"""Build docs/testing/test_inventory.json + test_value_matrix.md.

Merges three evidence sources, per the reconstruction brief section 5:
  1. machine facts   — scripts/testing/inventory_tests.py (AST inventory:
     n_tests, imports, mock/io signals, manifest membership)
  2. measured cost   — junit XMLs from the 2026-09-14 baseline runs
     (aggregate worker-seconds per file; unmeasured files marked unknown)
  3. forensic judgment — salvaged subagent classifications where available,
     plus the lead's applied decisions (promotion/demotion/deletion) from
     scripts/testing/classification_overrides.json

TestValue = ProductionRelevance x DefectDetectionPower x Determinism x
RuntimeEfficiency, each factor explicitly defined and explainable (0..1);
CostEfficiency = TestValue / runtime cost when measured.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_JSON = ROOT / "docs/testing/test_inventory.json"
OUT_MD = ROOT / "docs/testing/test_value_matrix.md"


def measured_costs() -> dict[str, dict]:
    """Per-file aggregate worker-seconds from the 2026-09-14 baseline junit XMLs.

    The unit run measured tests/unit/**; the crit run measured manifest files
    (incl. integration/e2e/release). Keep the MAX per file to avoid counting
    the same tests twice, and record provenance.
    """
    per_run: dict[str, dict[str, dict]] = {}
    merged: dict[str, dict] = {}
    for jf, key in (
        (ROOT / "scratch/recon/junit_unit_before.xml", "unit"),
        (ROOT / "scratch/recon/junit_crit_before.xml", "crit"),
    ):
        if not jf.exists():
            continue
        d = per_run.setdefault(key, {})
        for tc in ET.parse(jf).getroot().iter("testcase"):
            cn = tc.get("classname", "")
            m = re.match(
                r"(tests\.(?:unit|integration|e2e|release|ci|cli|slow|installer)\.[^.:]+)", cn
            )
            if not m:
                continue
            f = "/".join(m.group(1).split(".")[:3]) + ".py"
            t = float(tc.get("time", 0) or 0)
            rec = d.setdefault(f, {"worker_s": 0.0, "n": 0, "fails": 0})
            rec["worker_s"] += t
            rec["n"] += 1
            if tc.find("failure") is not None or tc.find("error") is not None:
                rec["fails"] += 1
    allf = set(per_run.get("unit", {})) | set(per_run.get("crit", {}))
    for f in allf:
        u = per_run.get("unit", {}).get(f)
        c = per_run.get("crit", {}).get(f)
        pick = max((x for x in (u, c) if x), key=lambda x: x["worker_s"], default=None)
        merged[f] = {
            "worker_s": round(pick["worker_s"], 1),
            "tests_measured": pick["n"],
            "baseline_failures": pick["fails"],
            "source": "unit-run"
            if (u and (not c or u["worker_s"] >= c["worker_s"]))
            else "crit-run",
        }
    return merged


def factors(machine: dict, cost: dict | None, cls: dict | None) -> dict:
    """Explainable 0..1 factors (brief section 5 scoring model)."""
    # ProductionRelevance: does the file import/execute production modules?
    prod = len(machine.get("imports_prod", []))
    pr = 0.0 if prod == 0 else min(1.0, 0.4 + 0.1 * prod + (0.2 if prod > 6 else 0))
    # DefectDetectionPower: heuristic blend of judgment + signals.
    verdict = (cls or {}).get("classification", "UNREVIEWED")
    inv = (cls or {}).get("protects_invariant", "") or ""
    if verdict == "PROMOTE-GATE":
        dd = 0.95
    elif verdict == "KEEP":
        dd = 0.8 if inv and inv.upper() != "NONE" else 0.55
    elif verdict in ("MERGE", "REWRITE"):
        dd = 0.6
    elif verdict in ("DELETE", "DELETE-CHECK"):
        dd = 0.25
    elif verdict == "CONVERT-RUNTIME":
        dd = 0.4
    else:
        dd = 0.5  # unreviewed: neutral, flagged
    if machine.get("mock_uses", 0) > 20 and verdict in ("REWRITE", "DELETE-CHECK", "UNREVIEWED"):
        dd = min(dd, 0.4)
    # Determinism
    det = (cls or {}).get(
        "determinism", "deterministic" if not machine.get("sleep_uses") else "timing-sensitive"
    )
    dm = {
        "deterministic": 1.0,
        "clock-dependent": 0.7,
        "timing-sensitive": 0.6,
        "env-dependent": 0.5,
        "random-uncontrolled": 0.3,
    }.get(det, 0.6)
    if machine.get("xfails"):
        dm = min(dm, 0.4)
    # RuntimeEfficiency: measured wall share; unknown -> neutral 0.8
    if cost:
        w = cost["worker_s"]
        re_ = (
            1.0
            if w <= 2
            else 0.9
            if w <= 10
            else 0.7
            if w <= 60
            else 0.5
            if w <= 200
            else 0.2
            if w <= 600
            else 0.1
        )
    else:
        re_ = 0.8
    value = round(pr * dd * dm * re_, 3)
    return {
        "production_relevance": round(pr, 2),
        "defect_detection": round(dd, 2),
        "determinism": round(dm, 2),
        "runtime_efficiency": round(re_, 2),
        "test_value": value,
        "cost_efficiency": round(value / max(cost["worker_s"], 0.1), 3) if cost else None,
    }


def main():
    machine = {
        e["file"]: e
        for e in json.loads((ROOT / "scratch/recon/inventory_raw.json").read_text(encoding="utf-8"))
        if e.get("file")
    }
    costs = measured_costs()
    try:
        classifications = json.loads(
            (ROOT / "scratch/recon/classifications.json").read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        classifications = {}
    overrides = json.loads(
        (ROOT / "scripts/testing/classification_overrides.json").read_text(encoding="utf-8")
    )
    promoted = set(overrides.get("promote_gate_applied", []))
    demoted = {d["file"]: d for d in overrides.get("tier1_demoted_measured_cost", [])}
    deleted = {d["file"] for d in overrides.get("delete_evidence_driven", [])}
    converted = {d["file"] for d in overrides.get("convert_runtime_or_lint", [])}

    entries = []
    tally = Counter()
    for f, m in sorted(machine.items()):
        cls = classifications.get(f)
        cost = costs.get(f)
        if f in deleted:
            final = "DELETED"
        elif f in converted:
            final = "CONVERT-RUNTIME"
        elif cls:
            final = cls.get("classification", "UNREVIEWED")
            if final == "PROMOTE-GATE" and f in promoted:
                final = "PROMOTE-GATE-APPLIED"
        else:
            final = "UNREVIEWED"
        tally[final] += 1
        fac = factors(m, cost, cls)
        entries.append(
            {
                "file": f,
                "dir": m.get("dir"),
                "n_tests": m.get("n_tests"),
                "loc": m.get("loc"),
                "in_critical_suite": m.get("in_critical_suite"),
                "in_slow_suite": m.get("in_slow_suite"),
                "measured": cost,
                "machine_flags": {
                    "mock_uses": m.get("mock_uses"),
                    "subproc": m.get("subproc_uses"),
                    "sleep": m.get("sleep_uses"),
                    "sqlite_real": m.get("sqlite_uses"),
                    "network": m.get("net_uses"),
                    "mt5": m.get("mt5_uses"),
                    "skips": m.get("skips"),
                    "xfails": m.get("xfails"),
                },
                "production_imports": m.get("imports_prod", [])[:12],
                "judgment": cls,
                "value": fac,
                "disposition": final,
            }
        )

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "mission": "NSE radical test reconstruction 2026-09-14",
        "scoring": {
            "TestValue": "ProductionRelevance x DefectDetectionPower x Determinism x RuntimeEfficiency (each 0..1, defined in scripts/testing/build_inventory.py::factors)",
            "CostEfficiency": "TestValue / measured aggregate worker-seconds (null when unmeasured)",
            "measured_from": "scratch/recon/junit_unit_before.xml + junit_crit_before.xml (2026-09-14 local runs, -n auto xdist worker-seconds; NOT CI minutes)",
            "honesty": "judgment present for 110 of 481 files (salvaged forensic arrays); the remainder are UNREVIEWED with machine facts only and MUST NOT be deleted without forensic completion",
        },
        "tally": dict(tally),
        "files": entries,
    }
    OUT_JSON.write_text(json.dumps(doc, indent=1), encoding="utf-8")

    # ---- markdown matrix ------------------------------------------------
    lines = [
        "# NSE Test Value Matrix",
        "",
        f"Generated {doc['generated']} from measured evidence. Machine-readable source: `docs/testing/test_inventory.json`.",
        "",
        "## Scoring model (explainable, not vanity)",
        "",
        "```",
        "TestValue      = ProductionRelevance x DefectDetectionPower x Determinism x RuntimeEfficiency",
        "CostEfficiency = TestValue / measured-worker-seconds",
        "```",
        "",
        "ProductionRelevance = f(count of production modules the file imports/exercises — AST-verified).",
        "DefectDetectionPower = forensic judgment (PROMOTE-GATE .95 / KEEP .8 / MERGE,REWRITE .6 /",
        "CONVERT .4 / DELETE .25), capped to .4 when mock-heavy and unreviewed. UNREVIEWED = .5 neutral.",
        "Determinism = judgment (deterministic 1.0 .. random-uncontrolled .3); xfails cap to .4.",
        "RuntimeEfficiency = measured aggregate worker-seconds (<=2s:1.0, <=10s:.9, <=60s:.7, <=200s:.5, <=600s:.2, >600s:.1).",
        "",
        "## Classification tally (481 test files)",
        "",
    ]
    for k, v in tally.most_common():
        lines.append(f"- **{k}**: {v}")
    lines += [
        "",
        "## Tier-1 cost outliers REMOVED from the PR gate (moved to Tier 2, evidence-driven)",
        "",
        "| file | measured worker-s | what it protects | where it runs now |",
        "|---|---|---|---|",
    ]
    for f, d in demoted.items():
        lines.append(f"| `{f}` | {d['measured_worker_s']} | {d['why']} | {d['to']} (main-push) |")
    lines += [
        "",
        "## Promoted INTO the PR-critical gate (P0 safety domains)",
        "",
        f"{len(promoted)} files classified PROMOTE-GATE by the forensic lanes and added to",
        "`tests/critical_suite.txt` (aggregate measured cost ~20 worker-seconds). Domains:",
        "risk math/circuit breakers, execution write-uncertainty & idempotency, DB economic",
        "integrity, PAPER/LIVE mode isolation, model artifact identity/lineage, market-data",
        "tick integrity. Per-file defect statements: `docs/testing/critical_regressions.md`.",
        "",
        "## New reconstruction-owned Tier-1 batteries (TDD-validated: 7/7 mutations KILLED)",
        "",
        "| file | invariant | mutation evidence |",
        "|---|---|---|",
        "| `test_recon_risk_boundary_battery.py` | margin clamp fraction (default 10% / hard 20%), tier-boundary exclusivity, floor-to-step float eps, hostile risk_pct/free-margin | MUT-RECON-MARGIN/TIER/FLOOR/MARGIN-GATE: KILLED |",
        "| `test_recon_wal_crash_durability.py` | WAL boot, crash durability of flushed financial rows, in-memory no-CWD-leak | MUT-RECON-WAL: KILLED |",
        "| `test_recon_batch_atomicity_real_failure.py` | one natively-failing row never destroys the batch; dead-letter replayable | manual: salvage asserts fail when recovery loop skipped |",
        "| `test_recon_duplicate_deal_across_restart.py` | signal/executions UNIQUE identity survives process restart (INV-006 cross-boot) | DB-level unique identity pinned by redelivery collapse |",
        "| `test_recon_requote_and_reconcile.py` | 30s re-quote lock release legs (time AND drift), pending-cache broker-truth repair | MUT-RECON-REQUOTE/RECONCILE: KILLED |",
        "",
        "## Deleted (evidence per file — brief section 4 answered for each)",
        "",
        "| file | why safe to remove |",
        "|---|---|",
    ]
    for d in overrides.get("delete_evidence_driven", []):
        lines.append(f"| `{d['file']}` | {d['why']} |")
    lines += [
        "",
        "## Moved to explicit manual/nightly lanes (never deleted without replacement proof)",
        "",
        "- `tests/integration/test_playwright_e2e.py` -> `tests/manual/` (superseded by e2e_client journeys on nightly-e2e)",
        "",
        "## Converted to runtime/lint checks",
        "",
    ]
    for d in overrides.get("convert_runtime_or_lint", []):
        lines.append(f"- `{d['file']}` — {d['why']}")
    lines += [
        "",
        "## Rewrites required (kept in suite, defect noted; next wave)",
        "",
    ]
    for d in overrides.get("rewrite_required", []):
        lines.append(f"- `{d['file']}` — {d['defect']} -> {d['fix']} (tier {d['tier']})")
    lines += [
        "",
        "## Deferred merge groups (recorded, NOT executed blind)",
        "",
    ]
    for g in overrides.get("merge_groups", []):
        lines.append(
            f"- **{g['group']}**: anchor `{g['anchor']}`; members {'; '.join(g['members'])} — {g['status']}"
        )
    lines += [
        "",
        "## UNREVIEWED remainder",
        "",
        f"{tally.get('UNREVIEWED', 0)} files carry machine facts only (no forensic verdict survived the",
        "salvage). Policy: an UNREVIEWED file may NOT be deleted; it is tier-assigned by machine",
        "signals (subprocess/sleep/network -> Tier2+, else inherits directory default) and stays in",
        "the appropriate manifest. Completion of their judgment is the next wave's first task.",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", OUT_JSON, "and", OUT_MD)
    print(json.dumps(dict(tally)))


if __name__ == "__main__":
    main()
