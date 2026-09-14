#!/usr/bin/env python3
"""BUG-276 quarantine migration: test-harness-contaminated CHAMPION rows.

The repo-root pytest incident (2026-09-14) stamped the live model lifecycle
registry with CHAMPION rows that are NOT governed production identity:

  * rows bound to pytest ``tmp_path`` artifact files (registry row 4073:
    ``.../Temp/pytest-of-*/test_live_tick_present0/model.pt``), written by
    boot-time champion_sync that ran during a test-engine construction;
  * rows bound to the serving production path whose fingerprint no longer
    matches the CURRENT on-disk governed bytes because the bytes were
    clobbered mid-window by force_fresh minting (row 4080: bb1f0afe while the
    restore made the served artifact c9982ddd again).

The P0-2 trust anchor (application/live/model_bundle_store) reads the LATEST
CHAMPION row by registered_at — a contaminated row poisons the boot gate in
both directions (refuses the governed artifact / accepts drift-adjacent bytes
if the drift row is newest).

Semantics (evidence-preserving; this is an AUDIT TRAIL, not a cleanup):
  * rows are never deleted; status flips CHAMPION -> QUARANTINED with a
    promotion_reason documenting WHY (BUG-276 + window + rule);
  * only rows in the incident window (default: 2026-09-14T00:00Z .. now) that
    match one of two rules qualify:
      R1 artifact_path resolves OUTSIDE the production model tree but the row
         carries CHAMPION status with same-window REGISTRY_RECONCILED evidence
         naming the same tmp dir (test-engine boot wrote it);
      R2 artifact_path (normalized) == the configured serving path AND its
         fingerprint != sha256[:16] of the CURRENT bytes at that path (i.e.
         the row was governing clobbered bytes; the restored/governed row is
         distinguished by matching the current bytes and SURVIVES as CHAMPION).
  * legacy rows outside the window (older fingerprint churn at the same path,
    e.g. the historical pre-restore champion lineage) are NEVER touched here —
    that history is evidence;
  * dry-run by default; --apply writes; --evidence-out dumps a JSON report.

Exit codes: 0 = ok (including zero candidates), 2 = preconditions failed,
3 = PG/non-sqlite db (unsupported).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

BUG_ID = "BUG-276"
DEFAULT_WINDOW_START = "2026-09-14T00:00:00+00:00"
DEFAULT_SERVING = "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"


def _norm(p: str | None) -> str:
    return str(p or "").replace("\\", "/").replace("\\", "/").strip().lower()


def _fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _parse_ts(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def collect_candidates(
    con: sqlite3.Connection,
    *,
    workspace: Path,
    serving_rel: str,
    window_start: datetime,
    now: datetime,
) -> tuple[list[dict], dict]:
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM experience_model_registry WHERE lifecycle_status='CHAMPION' "
        "ORDER BY registered_at DESC"
    ).fetchall()

    reconciled: list[sqlite3.Row] = []
    try:
        reconciled = con.execute(
            "SELECT * FROM model_governance_events WHERE event='REGISTRY_RECONCILED' "
            "ORDER BY timestamp DESC"
        ).fetchall()
    except sqlite3.Error:
        pass  # table absent in trimmed copies: R1 falls back to path heuristic

    # Rows may bind EITHER the workspace-absolute path or the config-relative
    # path (both appear in the live registry, and hot-swap writes absolute
    # paths): accept both spellings as the serving identity.
    serving_abs = _norm(str(workspace / serving_rel))
    serving_rel_norm = _norm(serving_rel)
    serving_identities = {serving_abs, serving_rel_norm}
    serving_bytes_fp: str | None = None
    spath = workspace / serving_rel
    if spath.exists():
        serving_bytes_fp = _fingerprint(spath)

    tmp_roots = {_norm(str(p)) for p in (Path(serving_abs).anchor,)}
    _ = tmp_roots  # (anchor bookkeeping kept simple; rules below are explicit)

    candidates: list[dict] = []
    for r in rows:
        rid = int(r["id"])
        reg_at = _parse_ts(r["registered_at"])
        path_norm = _norm(r["artifact_path"])
        fp = str(r["artifact_fingerprint"] or "").strip().lower()
        in_window = reg_at is not None and reg_at >= window_start
        if not in_window:
            continue

        reason = ""
        # R1: CHAMPION row bound to an artifact OUTSIDE the production model
        # tree (pytest tmp dirs live there) — corroborated by same-window
        # RECONCILED evidence naming the same temp root when the events table
        # exists; the 'pytest-of' path signature alone is accepted because the
        # governed registry never binds champion rows to a temp harness dir.
        in_model_tree = path_norm.startswith(_norm(str(workspace)) + "/artifacts/models/") or (
            not path_norm.startswith("/")
            and ":" not in path_norm[:3]
            and path_norm.startswith("artifacts/models/")
        )
        if path_norm and not in_model_tree:
            # corroborate with same-window RECONCILED evidence pointing at the
            # same temp root when the events table exists
            corroborated = False
            for ev in reconciled:
                ev_ts = _parse_ts(ev["timestamp"])
                if ev_ts is None or not (window_start <= ev_ts <= now):
                    continue
                payload = str(ev["payload"] or "")
                try:
                    ap = json.loads(payload).get("artifact_path", "")
                except (ValueError, AttributeError):
                    ap = ""
                if ap and _norm(ap) and _norm(ap) in path_norm:
                    corroborated = True
                    break
                if "pytest" in _norm(payload) and path_norm.startswith(
                    _norm(str(Path.home() / "AppData" / "Local" / "Temp"))
                ):
                    corroborated = True
                    break
            if corroborated or "pytest" in path_norm:
                reason = f"{BUG_ID} R1: CHAMPION row bound to a test-harness artifact path"
        # R2: row governs the serving PATH but its fingerprint is not the
        # current bytes, AND a surviving (non-candidate) CHAMPION row at the
        # same path DOES match the current bytes (restore happened).
        if (
            not reason
            and path_norm in serving_identities
            and serving_bytes_fp
            and fp
            and fp != serving_bytes_fp
        ):
            has_matching = any(
                _parse_ts(x["registered_at"]) is not None
                and _norm(x["artifact_path"]) in serving_identities
                and str(x["artifact_fingerprint"] or "").strip().lower() == serving_bytes_fp
                and int(x["id"]) != rid
                for x in rows
            )
            if has_matching:
                reason = (
                    f"{BUG_ID} R2: row governed bytes that were clobbered by test-harness "
                    "minting; the governed artifact now on disk is a different fingerprint"
                )

        if reason:
            candidates.append(
                {
                    "id": rid,
                    "model_id": r["model_id"],
                    "artifact_path": r["artifact_path"],
                    "fingerprint": fp,
                    "registered_at": str(r["registered_at"]),
                    "reason": reason,
                }
            )
    context = {
        "serving_path_normalized": serving_abs,
        "serving_current_fingerprint": serving_bytes_fp,
        "champion_rows_scanned": len(rows),
        "window_start": window_start.isoformat(),
    }
    return candidates, context


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=f"{BUG_ID} contaminated-CHAMPION quarantine")
    ap.add_argument("--db", required=True, help="path to audit.db")
    ap.add_argument("--workspace", default=".", help="production repo root (default cwd)")
    ap.add_argument("--serving-path", default=DEFAULT_SERVING)
    ap.add_argument("--window-start", default=DEFAULT_WINDOW_START)
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    ap.add_argument("--evidence-out", default="", help="write JSON evidence report here")
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"PRECONDITION FAIL: db not found: {db}", file=sys.stderr)
        return 2
    workspace = Path(args.workspace).resolve()
    # BUG-276 precondition: the serving artifact MUST exist at
    # workspace/serving-path (R2 compares rows against its CURRENT bytes).
    # --workspace pointing at the production root is the operator trust
    # boundary; no path-name heuristic (it would make the tool untestable).
    if not (workspace / args.serving_path).exists():
        print(
            f"PRECONDITION FAIL: serving artifact absent: {workspace / args.serving_path}",
            file=sys.stderr,
        )
        return 2

    con = sqlite3.connect(str(db))
    try:
        now = datetime.now(UTC)
        window_start = datetime.fromisoformat(str(args.window_start).replace("Z", "+00:00"))
        try:
            candidates, context = collect_candidates(
                con,
                workspace=workspace,
                serving_rel=args.serving_path,
                window_start=window_start,
                now=now,
            )
        except sqlite3.Error as e:
            print(f"PRECONDITION FAIL: registry unreadable: {e}", file=sys.stderr)
            return 3

        print(
            json.dumps({"mode": "APPLY" if args.apply else "DRY-RUN", "context": context}, indent=1)
        )
        for c in candidates:
            print(f"  id={c['id']} {c['reason']} <- {c['artifact_path']} ({c['fingerprint'][:16]})")
            if args.apply:
                con.execute(
                    "UPDATE experience_model_registry SET lifecycle_status='QUARANTINED', "
                    "promotion_reason=? WHERE id=? AND lifecycle_status='CHAMPION'",
                    (
                        f"{c['reason']} (quarantined {now.isoformat()} by "
                        "scripts/maintenance/quarantine_test_champion_rows.py)",
                        c["id"],
                    ),
                )
        con.commit()

        if args.evidence_out:
            Path(args.evidence_out).write_text(
                json.dumps(
                    {
                        "bug": BUG_ID,
                        "applied": bool(args.apply),
                        "run_at": now.isoformat(),
                        "context": context,
                        "quarantined": candidates,
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
        print(f"{'QUARANTINED' if args.apply else 'CANDIDATES'}: {len(candidates)}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
