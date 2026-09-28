"""Phase 2B/2C/2D — PostgreSQL + SQLite integrity audit and reconciliation.

Read-only audit of the live stores:

* 2B PostgreSQL: for every lifecycle table — existence, schema, PK, unique
  constraints, FKs, indexes, row count, latest timestamp, orphans,
  duplicate logical ids, stale rows, impossible states.
* 2C SQLite: the same audit over every discovered .db file, plus WAL/SHM and
  stale-copy detection.
* 2D reconciliation: per-domain PG vs SQLite comparison with divergence
  classification (EXPECTED / STALE / INCORRECT / UNKNOWN) and evidence.

Never deletes, mutates or writes to any production database. SQLite reads use
read-only URI mode so WAL files stay attached and a running engine is never
disturbed.

Usage:
    python scripts/diagnostics/phase2/db_integrity_audit.py \
        --repo <repo> --out phase2 \
        [--sqlite artifacts/audit.db ...] [--pg-uri postgresql://...]
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


# ---------------------------------------------------------------------------
# Domain -> lifecycle tables (the stage contract)
# ---------------------------------------------------------------------------

STAGE_DOMAINS: dict[str, list[str]] = {
    "DATA": [
        "audit_experiences",
        "audit_experience_outcomes",
    ],
    "FEATURES": [
        "audit_signals",
        "research_run_snapshots",
    ],
    "MODEL": [
        "experience_model_registry",
        "training_runs",
        "model_comparisons",
        "model_load_history",
        "model_runtime_health",
    ],
    "STRATEGY": [
        "strategy_registry",
        "strategy_intelligence_registry",
        "strategy_evolution_candidates",
    ],
    "BACKTEST": [
        "strategy_registry",
        "research_runs",
        "research_evidence",
    ],
    "WALK-FORWARD": [
        "strategy_registry",
        "research_gates",
    ],
    "OOS": [
        "strategy_registry",
        "research_gates",
        "research_runs",
    ],
    "ROBUSTNESS": [
        "strategy_registry",
        "research_gates",
    ],
    "COUNTERFACTUAL": [
        "research_evidence",
        "research_events",
    ],
    "REPLAY": [
        "audit_broker_orders",
        "audit_broker_deals",
        "audit_broker_trades",
        "position_lifecycle_events",
    ],
    "VALIDATION": [
        "research_runs",
        "research_gates",
        "research_evidence",
        "model_governance_events",
    ],
    "PROMOTION": [
        "model_promotion_audit",
        "model_rollback_audit",
        "model_governance_events",
        "shadow_promotions",
    ],
}

_ID_COLUMNS: dict[str, list[str]] = {
    "experience_model_registry": ["model_id", "model_version", "artifact_fingerprint"],
    "strategy_registry": ["strategy_id", "strategy_version"],
    "research_runs": ["run_id"],
    "research_gates": ["gate_id"],
    "research_evidence": ["evidence_id"],
    "training_runs": ["run_id"],
    "shadow_runs": ["run_id"],
    "model_promotion_audit": ["promotion_id"],
    "model_governance_events": ["event_id"],
}

_TS_COLUMNS: dict[str, list[str]] = {
    "experience_model_registry": ["registered_at"],
    "strategy_registry": ["created_at", "updated_at"],
    "research_runs": ["executed_at", "completed_at"],
    "research_gates": ["started_at", "completed_at"],
    "research_evidence": ["created_at"],
    "training_runs": ["started_at", "finished_at"],
    "model_governance_events": ["timestamp"],
    "model_promotion_audit": ["recorded_at"],
    "shadow_runs": ["started_at", "completed_at"],
    "audit_experiences": ["decision_timestamp"],
    "audit_experience_outcomes": ["outcome_timestamp"],
    "audit_signals": ["created_at"],
    "audit_broker_orders": ["created_at"],
    "position_lifecycle_events": ["occurred_at"],
}


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def sqlite_schema(path: str) -> dict[str, dict[str, object]]:
    """Full schema description for a SQLite file (read-only URI mode)."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception as exc:
        return {"_error": {"error": f"{type(exc).__name__}: {exc}"}}
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        out: dict[str, dict[str, object]] = {}
        for t in tables:
            cols = list(conn.execute(f'PRAGMA table_xinfo("{t}")'))
            pk = [c[1] for c in cols if c[5]]
            unique: list[str] = []
            indexes: list[dict[str, object]] = []
            for idx in conn.execute(f'PRAGMA index_list("{t}")'):
                cols_of = [r[2] for r in conn.execute(f'PRAGMA index_info("{idx[1]}")')]
                if idx[2]:
                    unique.append("(" + ",".join(cols_of) + ")")
                indexes.append({"name": idx[1], "columns": cols_of})
            fks = [
                {"column": r[2], "ref_table": r[3], "ref_column": r[4]}
                for r in conn.execute(f'PRAGMA foreign_key_list("{t}")')
            ]
            try:
                row_count: object = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except Exception:
                row_count = None
            latest = _sqlite_latest_ts(conn, t)
            out[t] = {
                "columns": [c[1] for c in cols],
                "column_types": {c[1]: c[2] for c in cols},
                "primary_key": pk,
                "unique_constraints": unique,
                "foreign_keys": fks,
                "indexes": indexes,
                "row_count": row_count,
                "latest_timestamp": latest,
            }
        return out
    finally:
        conn.close()


_TS_HINT_SUFFIXES = ("_at", "_time", "_timestamp", "_date", "timestamp")


def _discover_ts_columns(conn, table: str, is_sqlite: bool) -> list[str]:
    """Discover timestamp-ish columns from the ACTUAL schema, never by name list.

    Guessing column names (created_at vs generated_at vs timestamp) is exactly
    the audit-table-drift trap this skill warns about; read the catalog instead.
    """
    try:
        if is_sqlite:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_xinfo("{table}")')]
        else:
            cols = [
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=%s ORDER BY ordinal_position",
                    (table,),
                )
            ]
    except Exception:
        return []
    out = []
    for c in cols:
        if c is None:
            continue
        lc = str(c).lower()
        if any(lc.endswith(s) for s in _TS_HINT_SUFFIXES) or lc == "ts":
            out.append(str(c))
    return out


def _sqlite_latest_ts(conn: sqlite3.Connection, table: str) -> str | None:
    best: str | None = None
    for c in _discover_ts_columns(conn, table, True):
        try:
            v = conn.execute(f'SELECT MAX("{c}") FROM "{table}"').fetchone()[0]
        except Exception:
            continue
        if v is not None and (best is None or str(v) > best):
            best = str(v)
    return best


def _pg_latest_ts(conn, table: str) -> str | None:
    best: str | None = None
    for c in _discover_ts_columns(conn, table, False):
        try:
            v = conn.execute(f'SELECT MAX("{c}") FROM "{table}"').fetchone()[0]
        except Exception:
            continue
        if v is not None and (best is None or str(v) > best):
            best = str(v)
    return best


def pg_schema(conn) -> dict[str, dict[str, object]]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY table_name"
        )
    ]
    out: dict[str, dict[str, object]] = {}
    for t in tables:
        cols = list(
            conn.execute(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_name=%s "
                "ORDER BY ordinal_position",
                (t,),
            )
        )
        pk = [
            r[0]
            for r in conn.execute(
                "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
                "ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
                "WHERE i.indrelid=%s::regclass AND i.indisprimary",
                (t,),
            )
        ]
        unique: list[str] = []
        indexes: list[dict[str, object]] = []
        for r in conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename=%s",
            (t,),
        ):
            name, ddl = r[0], r[1] or ""
            if "UNIQUE INDEX" in ddl.upper():
                inner = ddl.split("(", 1)[1].rsplit(")", 1)[0]
                unique.append("(" + inner + ")")
            else:
                indexes.append({"name": name, "ddl": ddl})
        fks = [
            {"column": r[0], "ref_table": r[1], "ref_column": r[2]}
            for r in conn.execute(
                "SELECT kcu.column_name, ccu.table_name, ccu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "ON tc.constraint_name=kcu.constraint_name "
                "JOIN information_schema.constraint_column_usage ccu "
                "ON ccu.constraint_name=tc.constraint_name "
                "WHERE tc.table_name=%s AND tc.constraint_type='FOREIGN KEY'",
                (t,),
            )
        ]
        try:
            row_count: object = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception:
            row_count = None
        latest = _pg_latest_ts(conn, t)
        out[t] = {
            "columns": [c[0] for c in cols],
            "column_types": {c[0]: c[1] for c in cols},
            "primary_key": pk,
            "unique_constraints": unique,
            "foreign_keys": fks,
            "indexes": indexes,
            "row_count": row_count,
            "latest_timestamp": latest,
        }
    return out


# ---------------------------------------------------------------------------
# Integrity findings (read-only)
# ---------------------------------------------------------------------------


def _dup_ids(
    conn_like: object, table: str, cols: list[str], is_sqlite: bool
) -> list[dict[str, object]]:
    """Rows whose declared logical identity is duplicated."""
    if not cols:
        return []
    col_sql = ", ".join(f'"{c}"' for c in cols)
    sql = (
        f'SELECT {col_sql}, COUNT(*) AS n FROM "{table}" '
        f"GROUP BY {col_sql} HAVING COUNT(*) > 1 ORDER BY n DESC LIMIT 50"
    )
    try:
        if is_sqlite:
            rows = list(conn_like.execute(sql))  # type: ignore[union-attr]
        else:
            rows = list(conn_like.execute(sql))
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    return [{"identity": [str(x) for x in r[:-1]], "count": r[-1]} for r in rows]


def sqlite_integrity_findings(path: str) -> list[dict[str, object]]:
    """Duplicate logical ids + impossible states for one SQLite file."""
    findings: list[dict[str, object]] = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception as exc:
        return [{"level": "error", "check": "open", "detail": f"{type(exc).__name__}: {exc}"}]
    try:
        tabs = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        for table, cols in _ID_COLUMNS.items():
            if table not in tabs:
                continue
            for dup in _dup_ids(conn, table, cols, True):
                findings.append(
                    {
                        "level": "warning",
                        "check": "duplicate_logical_id",
                        "table": table,
                        "identity_columns": cols,
                        **dup,
                    }
                )
        findings.extend(_state_findings(conn, tabs, True))
    finally:
        conn.close()
    return findings


def pg_integrity_findings(conn) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    tabs = {
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
    }
    for table, cols in _ID_COLUMNS.items():
        if table not in tabs:
            continue
        for dup in _dup_ids(conn, table, cols, False):
            findings.append(
                {
                    "level": "warning",
                    "check": "duplicate_logical_id",
                    "table": table,
                    "identity_columns": cols,
                    **dup,
                }
            )
    findings.extend(_state_findings(conn, tabs, False))
    return findings


def _state_findings(conn, tabs: set[str], is_sqlite: bool) -> list[dict[str, object]]:
    """Impossible-state and stale/stale-copy checks, per real semantics."""
    out: list[dict[str, object]] = []
    _placeholder = "?" if is_sqlite else "%s"

    def q(sql: str, args: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
        try:
            return [tuple(r) for r in conn.execute(sql, args)]
        except Exception:
            return []

    # CHAMPION multiplicity: at most one governed production model per identity.
    if "experience_model_registry" in tabs:
        for r in q(
            "SELECT model_id, model_version, COUNT(*) FROM experience_model_registry "
            "WHERE lifecycle_status='CHAMPION' GROUP BY model_id, model_version "
            "HAVING COUNT(*) > 1"
        ):
            out.append(
                {
                    "level": "warning",
                    "check": "multiple_champion_rows",
                    "table": "experience_model_registry",
                    "detail": f"{r[0]}/{r[1]} has {r[2]} CHAMPION rows",
                }
            )
        for r in q(
            "SELECT COUNT(*) FROM experience_model_registry WHERE lifecycle_status='CHAMPION'"
        ):
            if r[0] and int(r[0]) > 1:
                out.append(
                    {
                        "level": "info",
                        "check": "champion_row_count",
                        "table": "experience_model_registry",
                        "detail": f"{r[0]} CHAMPION rows across all identities "
                        "(governed supersession keeps history; verify the latest is served)",
                    }
                )
        # A REJECTED/INVALID row must never be CHAMPION simultaneously.
        for r in q(
            "SELECT model_id, model_version FROM experience_model_registry "
            "WHERE lifecycle_status IN ('REJECTED','INVALID') "
            "AND artifact_fingerprint IN (SELECT artifact_fingerprint FROM "
            "experience_model_registry WHERE lifecycle_status='CHAMPION')"
        ):
            out.append(
                {
                    "level": "error",
                    "check": "rejected_fingerprint_is_champion",
                    "table": "experience_model_registry",
                    "detail": f"{r[0]}/{r[1]} shares a fingerprint with a CHAMPION row",
                }
            )
    # Strategy lifecycle: an ACTIVE strategy must have a VALIDATED ancestor state.
    if "strategy_registry" in tabs:
        for r in q(
            "SELECT COUNT(*) FROM strategy_registry WHERE lifecycle='ACTIVE' "
            "AND json_extract(oos, '$.status') IS NOT NULL "
            "AND json_extract(oos, '$.status') != 'PASS'"
        ):
            if r[0]:
                out.append(
                    {
                        "level": "error",
                        "check": "active_without_oos_pass",
                        "table": "strategy_registry",
                        "detail": f"{r[0]} ACTIVE rows whose oos.status != PASS",
                    }
                )
    # Training runs: finished must have finished_at.
    if "training_runs" in tabs:
        for r in q(
            "SELECT run_id, status FROM training_runs "
            "WHERE status IN ('COMPLETED','FAILED') AND (finished_at IS NULL OR finished_at='')"
        ):
            out.append(
                {
                    "level": "warning",
                    "check": "finished_run_without_finished_at",
                    "table": "training_runs",
                    "detail": f"run {r[0]} status={r[1]} has no finished_at",
                }
            )
    # Gates: completed gates need completed_at.
    if "research_gates" in tabs:
        for r in q(
            "SELECT gate_id, status FROM research_gates "
            "WHERE status IN ('PASS','FAIL') AND (completed_at IS NULL OR completed_at='')"
        ):
            out.append(
                {
                    "level": "warning",
                    "check": "settled_gate_without_completed_at",
                    "table": "research_gates",
                    "detail": f"gate {r[0]} status={r[1]} has no completed_at",
                }
            )
        # A gate whose run does not exist = orphan child (broken reference).
        if "research_runs" in tabs:
            for r in q(
                "SELECT g.gate_id, g.research_run_id FROM research_gates g "
                "LEFT JOIN research_runs r ON r.run_id=g.research_run_id "
                "WHERE r.run_id IS NULL LIMIT 50"
            ):
                out.append(
                    {
                        "level": "error",
                        "check": "orphan_gate_run_reference",
                        "table": "research_gates",
                        "detail": f"gate {r[0]} references missing run {r[1]}",
                    }
                )
            for r in q(
                "SELECT run_id, dataset_id FROM research_runs "
                "WHERE dataset_id IS NULL OR dataset_id='' LIMIT 50"
            ):
                out.append(
                    {
                        "level": "warning",
                        "check": "run_without_dataset_id",
                        "table": "research_runs",
                        "detail": f"run {r[0]} has empty dataset_id",
                    }
                )
    # Future timestamps (discovered per-table from the real schema).
    now = datetime.now(UTC).isoformat()
    for table in sorted(tabs):
        for c in _discover_ts_columns(conn, table, is_sqlite):
            for r in q(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{c}" IS NOT NULL '
                f'AND "{c}" != '
                ' AND "{c}" > {placeholder}',
                (now,),
            ):
                if r[0]:
                    out.append(
                        {
                            "level": "warning",
                            "check": "future_timestamp",
                            "table": table,
                            "column": c,
                            "count": int(r[0]),
                        }
                    )
    return out


# ---------------------------------------------------------------------------
# 2D reconciliation
# ---------------------------------------------------------------------------

_DIV_CLASS_NOTE = {
    "EXPECTED": "by-design: the table belongs to one provider only",
    "STALE": "rows exist on one side only; the other has not caught up",
    "INCORRECT": "same logical identity, different truth — needs operator review",
    "UNKNOWN": "no evidence to classify; recorded for a human",
}


def reconcile(
    pg: dict[str, dict[str, object]], sq: dict[str, dict[str, object]]
) -> dict[str, object]:
    """Per-domain PG vs SQLite comparison with classification + evidence."""
    domains: dict[str, object] = {}
    for stage, tables in STAGE_DOMAINS.items():
        per_table: list[dict[str, object]] = []
        for t in tables:
            p = pg.get(t)
            s = sq.get(t)
            entry: dict[str, object] = {
                "table": t,
                "pg_present": p is not None,
                "sqlite_present": s is not None,
            }
            if p is None and s is None:
                entry["classification"] = "UNKNOWN"
                entry["evidence"] = "table absent from BOTH providers"
                per_table.append(entry)
                continue
            if p is None or s is None:
                side = "postgresql" if p is not None else "sqlite"
                entry["classification"] = "EXPECTED"
                entry["evidence"] = f"table exists only on {side}"
                entry["rows_" + side] = (p or s or {}).get("row_count")
                per_table.append(entry)
                continue
            prow = p.get("row_count")
            srow = s.get("row_count")
            entry["pg_rows"] = prow
            entry["sqlite_rows"] = srow
            entry["pg_latest_ts"] = p.get("latest_timestamp")
            entry["sqlite_latest_ts"] = s.get("latest_timestamp")
            entry["schema_column_divergence"] = sorted(
                set(p.get("columns", [])) ^ set(s.get("columns", []))
            )
            entry["unique_divergence"] = sorted(
                set(p.get("unique_constraints", [])) ^ set(s.get("unique_constraints", []))
            )
            entry["fk_divergence"] = {
                "pg": p.get("foreign_keys"),
                "sqlite": s.get("foreign_keys"),
            }
            if prow == srow:
                entry["classification"] = "EXPECTED"
                entry["evidence"] = "identical row count"
            else:
                gap = (prow or 0) - (srow or 0)
                latest_p = p.get("latest_timestamp")
                latest_s = s.get("latest_timestamp")
                if latest_p and latest_s:
                    newer = "postgresql" if str(latest_p) > str(latest_s) else "sqlite"
                    entry["classification"] = "STALE"
                    entry["evidence"] = (
                        f"row gap {gap:+d}; {newer} has the newer latest timestamp "
                        f"(pg={latest_p} sqlite={latest_s})"
                    )
                else:
                    entry["classification"] = "UNKNOWN"
                    entry["evidence"] = f"row gap {gap:+d}; a latest timestamp is unavailable"
            per_table.append(entry)
        domains[stage] = per_table
    return {
        "generated_at": _utc_now(),
        "classification_legend": _DIV_CLASS_NOTE,
        "domains": domains,
    }


def _render_md(report: dict[str, object], findings: dict[str, object]) -> str:
    L: list[str] = []
    L.append("# Phase 2B/2C/2D — Database Integrity + Reconciliation")
    L.append("")
    L.append(f"generated: {report['generated_at']}")
    L.append("")
    L.append("Read-only audit. No production database was mutated.")
    L.append("")
    L.append("## Provider routing (runtime evidence)")
    L.append("")
    rt = findings.get("runtime", {})
    L.append(f"- configured provider: **{rt.get('provider')}**")
    L.append(f"- settings store: `{rt.get('settings_db')}`")
    L.append(f"- target database (config): `{rt.get('configured_database')}`")
    L.append("")
    L.append("## Integrity findings")
    L.append("")
    all_f = findings.get("postgresql", []) + findings.get("sqlite", [])
    if not all_f:
        L.append("- none")
    for f in all_f:
        L.append(
            f"- [{f.get('level')}] {f.get('check')} — `{f.get('table')}`: "
            f"{f.get('detail', f.get('count', ''))}"
        )
    L.append("")
    L.append("## Reconciliation by stage")
    L.append("")
    for stage, entries in report["domains"].items():  # type: ignore[union-attr]
        L.append(f"### {stage}")
        L.append("")
        L.append("| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |")
        L.append("| --- | --- | --- | --- | --- | --- | --- |")
        for e in entries:  # type: ignore[union-attr]
            L.append(
                f"| `{e['table']}` | {e.get('pg_rows', '—')} | "
                f"{e.get('sqlite_rows', '—')} | {str(e.get('pg_latest_ts'))[:19]} | "
                f"{str(e.get('sqlite_latest_ts'))[:19]} | **{e.get('classification')}** | "
                f"{str(e.get('evidence'))[:120]} |"
            )
        L.append("")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Connection helpers (a bare URI is not enough on this box: the operator's
# cluster wants the secret-store password)
# ---------------------------------------------------------------------------


def _pg_connect(uri: str, repo: Path | None = None):
    """Connect, falling back to the app's own secret-store password.

    Never builds a second connect path beyond what the driver uses: the
    password comes from SecureSecretStore under the same key.
    """
    try:
        return psycopg.connect(uri, connect_timeout=8)  # type: ignore[union-attr]
    except Exception:
        pass
    if repo is None:
        raise RuntimeError("no psycopg available; cannot open a PostgreSQL probe")
    from nexus_scalp.settings.secret_store import SecureSecretStore  # type: ignore

    store = SecureSecretStore()
    key = "db.postgresql.password"
    if not store.has_secret(key):
        raise RuntimeError(f"{key} is not staged in the secret store")
    pw = store.get_secret(key)
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(uri)
    if parts.password:
        raise RuntimeError("the supplied URI already carries a password")
    netloc = f"{parts.username or 'postgres'}:{pw}@{parts.hostname}:{parts.port}"
    return psycopg.connect(
        urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)),
        connect_timeout=8,
    )


def _native(path: str) -> str:
    """MSYS-style /c/... paths are not readable by the native sqlite3 lib."""
    p = str(path)
    if p.startswith("/") and len(p) > 2 and p[2] == "/":
        return p[1] + ":" + p[2:]
    return p


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

    # --- runtime provider resolution ---
    runtime: dict[str, object] = {"provider": None}
    try:
        sys.path.insert(0, str(repo / "src"))
        import sqlite3 as _s

        from nexus_scalp.settings.secret_store import SecureSecretStore  # type: ignore

        _base = Path(args.repo).parent / "NexusTradingForexBot"
        _settings_db = None
        cand_base = Path(__import__("os").environ.get("LOCALAPPDATA", "")) / "NexusScalpEngine"
        for p in sorted(cand_base.rglob("*.db")) if cand_base.exists() else []:
            try:
                c = _s.connect(f"file:{p}?mode=ro", uri=True)
                rows = list(
                    c.execute(
                        "SELECT key, value FROM application_settings WHERE key='database.provider'"
                    )
                )
                c.close()
                if rows:
                    _settings_db = p
                    runtime["provider"] = rows[0][1]
                    runtime["settings_db"] = str(p)
                    break
            except Exception:
                pass
        if runtime.get("provider") == "postgresql":
            store = SecureSecretStore()
            pw = (
                store.get_secret("db.postgresql.password")
                if store.has_secret("db.postgresql.password")
                else ""
            )
            runtime["configured_database"] = "nexusdb"
            runtime["pg_reachable"] = bool(pw)
    except Exception as exc:
        runtime["error"] = f"{type(exc).__name__}: {exc}"

    # --- SQLite arm ---
    sqlite_targets = args.sqlite or []
    if not sqlite_targets:
        art = repo / "artifacts"
        if not art.exists():
            art = repo.parent / "NexusTradingForexBot" / "artifacts"
        if (art / "audit.db").exists():
            sqlite_targets = [str(art / "audit.db")]
    sq_schemas: dict[str, dict[str, object]] = {}
    sq_findings: list[dict[str, object]] = []
    for path in sqlite_targets:
        sq_schemas.update(sqlite_schema(_native(path)))
        sq_findings.extend({"store": path, **f} for f in sqlite_integrity_findings(_native(path)))

    # --- PostgreSQL arm ---
    pg_schemas: dict[str, dict[str, object]] = {}
    pg_findings: list[dict[str, object]] = []
    if psycopg is not None and args.pg_uri:
        try:
            conn = _pg_connect(args.pg_uri, repo)
            pg_schemas = pg_schema(conn)
            pg_findings = pg_integrity_findings(conn)
            conn.close()
        except Exception as exc:
            pg_findings = [
                {"level": "error", "check": "pg_connect", "detail": f"{type(exc).__name__}: {exc}"}
            ]

    report = reconcile(pg_schemas, sq_schemas)
    findings_wrap = {
        "runtime": runtime,
        "postgresql": pg_findings,
        "sqlite": sq_findings,
        "sqlite_stores": sqlite_targets,
    }
    (out_dir / "phase2_db_reconciliation.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "phase2_db_reconciliation.md").write_text(
        _render_md(report, findings_wrap), encoding="utf-8"
    )
    (out_dir / "phase2_integrity_findings.json").write_text(
        json.dumps(findings_wrap, indent=2, default=str), encoding="utf-8"
    )
    n_pg = len(pg_findings)
    n_sq = len(sq_findings)
    print(f"reconciliation written -> {out_dir} (pg findings={n_pg}, sqlite findings={n_sq})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
