"""Phase 2T/2U — Orphan and duplicate/conflict scanners.

Read-only. Reports only; never deletes, repairs or mutates any row in any
database.

2T orphan classes detected:
    model without feature lineage        experience_model_registry.feature_schema_id empty
    strategy without model                strategy_registry row with no referenced model
    backtest without strategy             research_runs row whose strategy_id has no
                                         strategy_registry row
    walkforward / oos / robustness        research_gates rows whose run is missing
                                         gate without a run
    OOS without source                    OOS gate rows whose research_run is missing
    robustness without baseline           robustness gate with no prior baseline evidence
    counterfactual w/o baseline           counterfactual evidence with no baseline evidence
    replay without source                 replay evidence without a run
    validation without evidence           a settled research run with no research_evidence row
    promotion without validation          model_promotion_audit / promotion event without a
                                         matching validation lineage record

2U duplicate classes:
    duplicate dataset ids, model identities, strategy ids, conflicting state
    records, the same logical run twice, PG/SQLite identity collisions,
    repeated promotion records, contradictory lifecycle state.

Usage:
    python scripts/diagnostics/phase2/orphan_scanner.py --repo <repo> --out phase2
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

try:
    import psycopg  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _native(path: str) -> str:
    p = str(path)
    if p.startswith("/") and len(p) > 2 and p[2] == "/":
        return p[1] + ":" + p[2:]
    return p


# ---------------------------------------------------------------------------
# Probe interface: one object that can answer SQL questions for a provider
# ---------------------------------------------------------------------------


class _Probe:
    def __init__(self, name: str, kind: str):
        self.name = name
        self.kind = kind  # "sqlite" | "postgresql"
        self._tables: set[str] = set()

    def has(self, table: str) -> bool:
        return table in self._tables

    def q(self, sql: str, args: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
        raise NotImplementedError


class _SQLiteProbe(_Probe):
    def __init__(self, name: str, path: str):
        super().__init__(name, "sqlite")
        self.path = _native(path)
        self._conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self._tables = {
            r[0]
            for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    def q(self, sql: str, args: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
        try:
            return [tuple(r) for r in self._conn.execute(sql, args)]
        except Exception:
            return []

    def close(self) -> None:
        self._conn.close()


class _PgProbe(_Probe):
    def __init__(self, name: str, conn):
        super().__init__(name, "postgresql")
        self._conn = conn
        self._conn.autocommit = True
        self._tables = {
            r[0]
            for r in self._conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
        }

    def q(self, sql: str, args: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
        try:
            return [tuple(r) for r in self._conn.execute(sql, args)]
        except Exception:
            self._conn.rollback()
            return []


# ---------------------------------------------------------------------------
# Orphan checks — each returns a list of findings
# ---------------------------------------------------------------------------


def scan_orphans(p: _Probe) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    _ph = "?" if p.kind == "sqlite" else "%s"

    def add(check: str, detail: str, **extra: object) -> None:
        out.append({"check": check, "detail": detail, **extra})

    # model without feature lineage
    if p.has("experience_model_registry"):
        for r in p.q(
            "SELECT model_id, model_version FROM experience_model_registry "
            "WHERE feature_schema_id IS NULL OR feature_schema_id='' "
            "OR feature_dimension IS NULL OR feature_dimension < 1 LIMIT 200"
        ):
            add(
                "model_without_feature_lineage",
                f"{r[0]}/{r[1]} has no feature_schema_id/dimension",
            )
    # strategy without model (the registry row references no model identity)
    if p.has("strategy_registry"):
        for r in p.q(
            "SELECT strategy_id, strategy_version, lifecycle FROM strategy_registry "
            "WHERE (model_id IS NULL OR model_id='') AND lifecycle NOT IN ('RETIRED','REJECTED') "
            "LIMIT 200"
        ):
            add(
                "strategy_without_model",
                f"{r[0]}/{r[1]} (lifecycle={r[2]}) has no model_id",
            )
    # backtest / run without strategy
    if p.has("research_runs") and p.has("strategy_registry"):
        for r in p.q(
            "SELECT run_id, strategy_id FROM research_runs r "
            "WHERE NOT EXISTS (SELECT 1 FROM strategy_registry s "
            "WHERE s.strategy_id=r.strategy_id) LIMIT 200"
        ):
            add(
                "backtest_without_strategy",
                f"run {r[0]} references missing strategy {r[1]}",
            )
    # gate without parent run (covers WF / OOS / robustness without source)
    if p.has("research_gates") and p.has("research_runs"):
        for r in p.q(
            "SELECT g.gate_id, g.gate_type, g.research_run_id FROM research_gates g "
            "WHERE NOT EXISTS (SELECT 1 FROM research_runs r "
            "WHERE r.run_id=g.research_run_id) LIMIT 200"
        ):
            add(
                "gate_without_parent_run",
                f"{r[0]} (type={r[1]}) references missing run {r[2]}",
            )
    # OOS / robustness gate rows without their own settled evidence. Evidence
    # is keyed per GATE (evidence.gate_id), not per run, so the correct orphan
    # test is "a settled OOS/ROBUSTNESS gate with no evidence row for that gate".
    if p.has("research_gates") and p.has("research_evidence"):
        for r in p.q(
            "SELECT g.gate_id, g.gate_type FROM research_gates g "
            "WHERE g.gate_type IN ('OOS','ROBUSTNESS') "
            "AND g.status IN ('PASS','FAIL') "
            "AND NOT EXISTS (SELECT 1 FROM research_evidence e "
            "WHERE e.gate_id=g.gate_id) LIMIT 200"
        ):
            add(
                "robustness_or_oos_without_baseline",
                f"{r[0]} (type={r[1]}) settled with no evidence row",
            )
    # counterfactual evidence without a baseline: an OOS_RESULT/BACKTEST_RESULT
    # (the baseline) must exist for the same run as any other evidence kind.
    if p.has("research_evidence"):
        for r in p.q(
            "SELECT DISTINCT e.kind, e.research_run_id FROM research_evidence e "
            "WHERE e.kind NOT IN ('OOS_RESULT','BACKTEST_RESULT','WALK_FORWARD_RESULT') "
            "AND NOT EXISTS (SELECT 1 FROM research_evidence b "
            "WHERE b.research_run_id=e.research_run_id "
            "AND b.kind IN ('BACKTEST_RESULT','OOS_RESULT')) LIMIT 200"
        ):
            add(
                "counterfactual_without_baseline",
                f"evidence kind {r[0]} for run {r[1]} has no BACKTEST/OOS baseline evidence",
            )
    # validation without evidence: a COMPLETED run carrying no evidence row
    if p.has("research_runs") and p.has("research_evidence"):
        for r in p.q(
            "SELECT run_id FROM research_runs r WHERE r.status='COMPLETED' "
            "AND NOT EXISTS (SELECT 1 FROM research_evidence e "
            "WHERE e.research_run_id=r.run_id) LIMIT 200"
        ):
            add(
                "validation_without_evidence",
                f"COMPLETED run {r[0]} has no research_evidence row",
            )
    # promotion without validation: a promotion event with no validation record
    if p.has("model_governance_events") and p.has("research_runs"):
        for r in p.q(
            "SELECT event_id, model_id, model_version FROM model_governance_events "
            "WHERE event IN ('PROMOTED','PROMOTION','CHAMPION_PROMOTED') "
            "AND NOT EXISTS (SELECT 1 FROM research_runs x WHERE x.model_id IS NOT NULL "
            "AND x.model_id=model_governance_events.model_id) LIMIT 200"
        ):
            add(
                "promotion_without_validation",
                f"governance event {r[0]} promotes {r[1]}/{r[2]} with no validation run",
            )
    if p.has("model_promotion_audit"):
        for r in p.q(
            "SELECT promotion_id, new_champion_model_id FROM model_promotion_audit "
            "WHERE status='ACTIVE' LIMIT 200"
        ):
            add(
                "promotion_record_without_validation",
                f"promotion {r[0]} for {r[1]} recorded (verify its validation lineage)",
                level="info",
            )
    # replay without source
    if p.has("research_evidence") and p.has("research_runs"):
        for r in p.q(
            "SELECT evidence_id, research_run_id FROM research_evidence "
            "WHERE kind LIKE '%replay%' AND NOT EXISTS (SELECT 1 FROM research_runs "
            "WHERE run_id=research_evidence.research_run_id) LIMIT 200"
        ):
            add(
                "replay_without_source",
                f"replay evidence {r[0]} references missing run {r[1]}",
            )
    return out


# ---------------------------------------------------------------------------
# Duplicate / conflict checks
# ---------------------------------------------------------------------------


def scan_duplicates(p: _Probe) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []

    def add(check: str, detail: str, **extra: object) -> None:
        out.append({"check": check, "detail": detail, **extra})

    if p.has("experience_model_registry"):
        # duplicate model identity (same id+version) — the table allows many
        # fingerprint rows per identity, so count identities with >1 row.
        for r in p.q(
            "SELECT model_id, model_version, COUNT(*) FROM experience_model_registry "
            "GROUP BY model_id, model_version HAVING COUNT(*) > 1 "
            "ORDER BY 3 DESC LIMIT 100"
        ):
            add(
                "duplicate_model_identity",
                f"{r[0]}/{r[1]} stored {r[2]} times (fingerprint history)",
                level="info",
            )
        # contradictory lifecycle state: same fingerprint both CHAMPION and REJECTED
        for r in p.q(
            "SELECT artifact_fingerprint, COUNT(DISTINCT lifecycle_status) FROM "
            "experience_model_registry WHERE lifecycle_status IN ('CHAMPION','REJECTED') "
            "GROUP BY artifact_fingerprint HAVING COUNT(DISTINCT lifecycle_status) > 1 LIMIT 100"
        ):
            add(
                "contradictory_lifecycle_state",
                f"fingerprint {r[0]} is both CHAMPION and REJECTED",
                level="error",
            )
        # regenerated identity: same model_id+version+fingerprint appearing twice
        for r in p.q(
            "SELECT model_id, model_version, artifact_fingerprint, COUNT(*) FROM "
            "experience_model_registry GROUP BY model_id, model_version, "
            "artifact_fingerprint HAVING COUNT(*) > 1 LIMIT 100"
        ):
            add(
                "regenerated_identity",
                f"{r[0]}/{r[1]} fp={r[2]} has {r[3]} identical rows",
                level="warning",
            )
    if p.has("strategy_registry"):
        for r in p.q(
            "SELECT strategy_id, COUNT(*) FROM strategy_registry "
            "GROUP BY strategy_id HAVING COUNT(*) > 1 LIMIT 100"
        ):
            add(
                "duplicate_strategy_id",
                f"{r[0]} has {r[1]} rows (version history)",
                level="info",
            )
        # conflicting state records: same strategy both ACTIVE and REJECTED
        for r in p.q(
            "SELECT strategy_id, COUNT(DISTINCT lifecycle) FROM strategy_registry "
            "WHERE lifecycle IN ('ACTIVE','REJECTED','RETIRED') GROUP BY strategy_id "
            "HAVING COUNT(DISTINCT lifecycle) > 1 LIMIT 100"
        ):
            add(
                "conflicting_strategy_state",
                f"strategy {r[0]} carries mutually exclusive lifecycle values",
                level="error",
            )
    if p.has("research_runs"):
        for r in p.q(
            "SELECT run_id, COUNT(*) FROM research_runs GROUP BY run_id "
            "HAVING COUNT(*) > 1 LIMIT 100"
        ):
            add(
                "same_logical_run_twice",
                f"run_id {r[0]} stored {r[1]} times",
                level="error",
            )
    if p.has("model_promotion_audit"):
        for r in p.q(
            "SELECT new_champion_model_id, new_champion_version, COUNT(*) FROM "
            "model_promotion_audit WHERE status='ACTIVE' GROUP BY "
            "new_champion_model_id, new_champion_version HAVING COUNT(*) > 1 LIMIT 100"
        ):
            add(
                "repeated_promotion_record",
                f"{r[0]}/{r[1]} has {r[2]} ACTIVE promotion records",
                level="error",
            )
    if p.has("model_governance_events"):
        for r in p.q(
            "SELECT model_id, model_version, previous_state, new_state, COUNT(*) FROM "
            "model_governance_events WHERE previous_state='CANDIDATE' "
            "AND new_state='CHAMPION' GROUP BY model_id, model_version, previous_state, "
            "new_state HAVING COUNT(*) > 1 LIMIT 100"
        ):
            add(
                "repeated_promotion_transition",
                f"{r[0]}/{r[1]} CANDIDATE->CHAMPION recorded {r[4]} times",
                level="warning",
            )
    return out


def _classify_collision(pg_ids: set[str], sq_ids: set[str]) -> list[dict[str, object]]:
    """PG/SQLite identity collisions for one logical-id set."""
    out: list[dict[str, object]] = []
    shared = pg_ids & sq_ids
    if not shared:
        return out
    return [
        {
            "check": "pg_sqlite_identity_collision",
            "detail": f"{len(shared)} shared logical ids (e.g. {sorted(shared)[:5]})",
            "count": len(shared),
            "level": "info",
        }
    ]


def _render_md(report: dict[str, object]) -> str:
    L: list[str] = []
    L.append("# Phase 2T/2U — Orphan + Duplicate / Conflict Scan")
    L.append("")
    L.append(f"generated: {report['generated_at']}")
    L.append("")
    L.append("Read-only: this scanner reports only. It never deletes or repairs.")
    L.append("")
    for store in report["stores"]:  # type: ignore[union-attr]
        L.append(f"## `{store['name']}` ({store['kind']})")
        L.append("")
        L.append("### Orphans")
        L.append("")
        if not store.get("orphans"):
            L.append("- none detected")
        for f in store.get("orphans", []):  # type: ignore[union-attr]
            L.append(f"- [{f.get('level', 'warning')}] **{f['check']}** — {f['detail']}")
        L.append("")
        L.append("### Duplicates / conflicts")
        L.append("")
        if not store.get("duplicates"):
            L.append("- none detected")
        for f in store.get("duplicates", []):  # type: ignore[union-attr]
            L.append(f"- [{f.get('level', 'warning')}] **{f['check']}** — {f['detail']}")
        L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[3]))
    ap.add_argument("--out", default="phase2")
    ap.add_argument("--sqlite", action="append", default=None)
    ap.add_argument("--pg-uri", default=None)
    args = ap.parse_args()

    repo = Path(args.repo)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    sqlite_targets = args.sqlite
    if not sqlite_targets:
        art = repo / "artifacts"
        if not art.exists():
            art = repo.parent / "NexusTradingForexBot" / "artifacts"
        if (art / "audit.db").exists():
            sqlite_targets = [str(art / "audit.db")]

    stores: list[dict[str, object]] = []
    probes: list[_Probe] = []
    for path in sqlite_targets or []:
        try:
            probes.append(_SQLiteProbe(path, path))
        except Exception as exc:
            stores.append({"name": path, "kind": "sqlite", "error": f"{type(exc).__name__}: {exc}"})
    if psycopg is not None and args.pg_uri:
        try:
            conn = psycopg.connect(args.pg_uri, connect_timeout=8)
            probes.append(_PgProbe(args.pg_uri, conn))
        except Exception as exc:
            stores.append(
                {
                    "name": args.pg_uri,
                    "kind": "postgresql",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    pg_model_ids: set[str] = set()
    sq_model_ids: set[str] = set()
    for probe in probes:
        orphans = scan_orphans(probe)
        dups = scan_duplicates(probe)
        stores.append(
            {
                "name": probe.name,
                "kind": probe.kind,
                "orphans": orphans,
                "duplicates": dups,
            }
        )
        if probe.has("experience_model_registry"):
            ids = {
                str(r[0])
                for r in probe.q("SELECT DISTINCT model_id FROM experience_model_registry")
            }
            if probe.kind == "postgresql":
                pg_model_ids |= ids
            else:
                sq_model_ids |= ids
        if probe.kind == "postgresql":
            probe._conn.close()  # type: ignore[attr-defined]

    collisions = _classify_collision(pg_model_ids, sq_model_ids)
    report = {
        "generated_at": _utc_now(),
        "stores": stores,
        "pg_sqlite_identity_collisions": collisions,
    }
    (out_dir / "phase2_orphan_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "phase2_orphan_report.md").write_text(_render_md(report), encoding="utf-8")
    total_o = sum(len(s.get("orphans", [])) for s in stores if isinstance(s.get("orphans"), list))
    total_d = sum(
        len(s.get("duplicates", [])) for s in stores if isinstance(s.get("duplicates"), list)
    )
    print(
        f"orphan scan -> {out_dir} (orphans={total_o}, duplicates={total_d}, collisions={len(collisions)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
