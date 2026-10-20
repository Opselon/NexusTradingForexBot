#!/usr/bin/env python3
"""Learn the exit geometry from the trade ledger and publish it as an artifact.

This is the operator-facing entry point for the self-learning loop:

    nexus-scalp ledger  ──►  ExitObservation[]  ──►  optimize()  ──►  artifacts/exit_policy.json
                                                                        │
                                          runtime ExitProtectionPolicy ──┘  (load_learned_profile)

Design rules baked into the script (not just documented):

* **Read-only.** It opens the audit DB, reads closed experiences and writes one
  JSON file. It never touches a position, never calls the broker and never
  promotes anything — the runtime picks the artifact up on its next start.
* **Refuses to publish without evidence.** Fewer than ``--min-samples`` closed
  trades per regime keeps the existing artifact untouched (a dry run still
  prints what it *would* have done).
* **Diffable.** The artifact carries the observations, the accepted lifts, the
  per-regime sample sizes and the seed, so two runs on the same ledger produce
  byte-identical files.
* **Inspectable offline.** ``--from-csv`` (columns: regime,mfe_r,final_r) makes
  the whole loop reproducible on a laptop with no database at all.

Usage
-----
    python scripts/learn_exit_policy.py --dry-run
    python scripts/learn_exit_policy.py --out artifacts/exit_policy.json
    python scripts/learn_exit_policy.py --from-csv trades.csv --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from nexus_scalp.experience.exit_optimizer import (  # noqa: E402
    ExitObservation,
    observations_from_records,
    optimize,
    write_profile,
)

DEFAULT_DB = "artifacts/audit.db"


def _read_csv(path: Path) -> list[ExitObservation]:
    rows: list[ExitObservation] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                rows.append(
                    ExitObservation(
                        regime=(row.get("regime") or "DEFAULT").strip().upper(),
                        mfe_r=float(row["mfe_r"]),
                        final_r=float(row["final_r"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                print(f"skip malformed row {row!r}: {error}", file=sys.stderr)
    return rows


def _read_ledger(db_url: str, limit: int) -> list[ExitObservation]:
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.experience.ledger import ExperienceLedger

    repo = AuditRepository(db_url=db_url)
    ledger = ExperienceLedger(audit_repo=repo)
    records = ledger.list_all_experiences(limit=limit)
    return observations_from_records(records)


def _print_table(report: dict) -> None:
    print(f"{'regime':<12}{'n':>7}{'trigger':>10}{'baseline R':>13}{'expected R':>13}{'lift':>9}  accepted")
    print("-" * 80)
    for regime, rec in sorted(report["regimes"].items()):
        print(
            f"{regime:<12}{rec['samples']:>7}{rec['trigger_r']:>10.2f}"
            f"{rec['baseline_r']:>13.3f}{rec['expected_r']:>13.3f}{rec['lift_r']:>+9.3f}"
            f"  {'yes' if rec['accepted'] else 'no'}"
        )
        print(f"{'':<12}{rec['reason']}")
    print("-" * 80)
    print(f"observations: {report['total_observations']} · lock: {report['lock_r']}R · grid: {report['grid']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB, help=f"audit DB url or path (default: {DEFAULT_DB})")
    parser.add_argument("--from-csv", type=Path, default=None, help="offline input: regime,mfe_r,final_r")
    parser.add_argument("--out", type=Path, default=None, help="artifact path to write (default: artifacts/exit_policy.json)")
    parser.add_argument("--min-samples", type=int, default=40, help="closed trades per regime before learning")
    parser.add_argument("--limit", type=int, default=20000, help="ledger rows to read")
    parser.add_argument("--dry-run", action="store_true", help="print the recommendation, write nothing")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    observations = _read_csv(args.from_csv) if args.from_csv else _read_ledger(args.db, args.limit)
    if not observations:
        print("no closed observations found — nothing to learn (artifact untouched)", file=sys.stderr)
        return 2

    result = optimize(observations, min_samples=args.min_samples)
    report = {
        "observations": len(observations),
        "min_samples": args.min_samples,
        "total_observations": result.total_observations,
        "grid": list(result.grid),
        "lock_r": result.lock_r,
        "generated_at": result.generated_at,
        "regimes": {regime: rec.as_dict() for regime, rec in result.recommendations.items()},
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_table(report)

    accepted = [rec for rec in result.recommendations.values() if rec.accepted]
    if args.dry_run:
        print(f"dry run: {len(accepted)} regime(s) would change; nothing written")
        return 0
    if not accepted:
        print("no regime met the evidence bar — artifact untouched (baseline geometry stays live)")
        return 0

    target = args.out or (Path("artifacts") / "exit_policy.json")
    path = write_profile(target, result)
    print(f"wrote {path} ({len(accepted)} learned regime(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
