"""TDD RED-evidence harness for the reconstruction's critical invariants.

Proves each new Tier-1 test can actually DETECT the defect it claims to
protect: apply a precise, temporary source mutation to the production guard,
run the owning test, require RED, then restore. Mirrors scripts/qa/
run_mutations.py's contract (anchor must occur exactly once; the battery must
go RED when the guard breaks — a surviving mutation = test blind spot).

This harness is for the reconstruction branch's validation runs; it never
ships broken code (restore is in a finally-block and verified by git status).

Usage:
    python scripts/testing/mutation_check.py            # all batteries
    python scripts/testing/mutation_check.py --id MUT-RECON-MARGIN
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

BATTERIES: list[dict[str, str]] = [
    {
        "id": "MUT-RECON-MARGIN",
        "desc": "margin hard clamp fraction 20% -> 90% (must break the "
        "hard-ceiling test AND the default-fraction test)",
        "target": "src/nexus_scalp/risk/risk_engine.py",
        "anchor": "        clamp_fraction = 0.20",
        "replacement": "        clamp_fraction = 0.90",
        "battery": "tests/unit/test_recon_risk_boundary_battery.py",
    },
    {
        "id": "MUT-RECON-TIER",
        "desc": "equity tier boundary < -> <= at the 100.0 line (tier-cap boundary test must fail)",
        "target": "src/nexus_scalp/risk/risk_engine.py",
        "anchor": "        if account.equity < 100.0:\n            tier_max = 0.02",
        "replacement": "        if account.equity <= 100.0:\n            tier_max = 0.02",
        "battery": "tests/unit/test_recon_risk_boundary_battery.py",
    },
    {
        "id": "MUT-RECON-FLOOR",
        "desc": "_floor_to_step eps removed (float-boundary under-size test must fail)",
        "target": "src/nexus_scalp/risk/risk_engine.py",
        "anchor": "        eps = 1e-9\n        steps = math.floor((val + eps) / step)",
        "replacement": "        eps = 0.0\n        steps = math.floor((val + eps) / step)",
        "battery": "tests/unit/test_recon_risk_boundary_battery.py",
    },
    {
        "id": "MUT-RECON-MARGIN-GATE",
        "desc": "INVALID_FREE_MARGIN gate removed (negative-margin test must fail)",
        "target": "src/nexus_scalp/risk/risk_engine.py",
        "anchor": '        if account.margin_free <= 0.0:\n            return 0.0, "INVALID_FREE_MARGIN"',
        "replacement": "        if False:\n            pass",
        "battery": "tests/unit/test_recon_risk_boundary_battery.py",
    },
    {
        "id": "MUT-RECON-WAL",
        "desc": "WAL journal mode downgraded to DELETE (WAL boot test must fail)",
        "target": "src/nexus_scalp/adapters/database/audit_repository.py",
        "anchor": '                    conn.execute("PRAGMA journal_mode = WAL;")',
        "replacement": '                    conn.execute("PRAGMA journal_mode = DELETE;")',
        "battery": "tests/unit/test_recon_wal_crash_durability.py",
    },
    {
        "id": "MUT-RECON-REQUOTE",
        "desc": "30s churn lock inverted (<= -> >=) (re-quote lock test must fail)",
        "target": "src/nexus_scalp/execution/lifecycle/pending_orders.py",
        "anchor": "            if time_delta <= PENDING_ORDER_LOCK_SECONDS:",
        "replacement": "            if time_delta >= PENDING_ORDER_LOCK_SECONDS:",
        "battery": "tests/unit/test_recon_requote_and_reconcile.py",
    },
    {
        "id": "MUT-RECON-RECONCILE",
        "desc": "reconcile repair never fires (mismatch report lies repaired=False)",
        "target": "src/nexus_scalp/execution/lifecycle/pending_orders.py",
        "anchor": "            self._refresh_cache(symbol=symbol, current_tick=current_tick)\n            repaired = True",
        "replacement": "            repaired = False",
        "battery": "tests/unit/test_recon_requote_and_reconcile.py",
    },
    {
        "id": "MUT-RECON-SALVAGE",
        "desc": "audit batch recovery: salvage loop body dead (good rows lost with the batch)",
        "target": "src/nexus_scalp/adapters/database/audit_repository.py",
        "anchor": "                    for failed_query, failed_args in batch:\n                        try:\n                            with conn:\n                                conn.execute(failed_query, failed_args)\n                            salvaged += 1",
        "replacement": "                    for failed_query, failed_args in []:\n                        try:\n                            with conn:\n                                conn.execute(failed_query, failed_args)\n                            salvaged += 1",
        "battery": "tests/unit/test_recon_batch_atomicity_real_failure.py",
    },
    {
        "id": "MUT-RECON-DEDUPKEY",
        "desc": "signal dedup identity polluted with request_id (restart redelivery creates second decision row)",
        "target": "src/nexus_scalp/adapters/database/audit_repository.py",
        "anchor": '        raw = "|".join([proposal.symbol, candle, model_action, stage, mode, reason])',
        "replacement": '        raw = "|".join([proposal.symbol, candle, model_action, stage, mode, reason, proposal.request_id])',
        "battery": "tests/unit/test_recon_duplicate_deal_across_restart.py",
    },
]


def _run_pytest(battery: str) -> int:
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", battery],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    return res.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default="")
    args = ap.parse_args()
    selected = [b for b in BATTERIES if not args.id or b["id"] == args.id]

    results = []
    for b in selected:
        target = REPO / b["target"]
        original = target.read_text(encoding="utf-8")
        count = original.count(b["anchor"])
        if count != 1:
            results.append((b["id"], "INVALID_ANCHOR", f"anchor occurs {count}x"))
            continue
        try:
            target.write_text(original.replace(b["anchor"], b["replacement"], 1), encoding="utf-8")
            rc = _run_pytest(b["battery"])
        finally:
            # byte-exact restore, verified
            target.write_text(original, encoding="utf-8")
            assert target.read_text(encoding="utf-8") == original, "restore drift!"
        verdict = "KILLED" if rc != 0 else "SURVIVED"
        results.append((b["id"], verdict, f"{b['battery']} rc={rc}"))
        print(f"{b['id']:22} {verdict:9} {b['battery']} rc={rc}")

    survivors = [r for r in results if r[1] != "KILLED"]
    print(
        f"\n{len(results) - len(survivors)}/{len(results)} mutations KILLED "
        f"(survivors/invalid = test blind spots)"
    )
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
