"""Phase 2A — Repository / storage inventory.

Read-only discovery of EVERY storage domain the NSE lifecycle touches, built
from source code + runtime inspection, never from documentation alone.

Emits a machine-readable inventory JSON plus a human report.

Usage:
    python scripts/diagnostics/phase2/storage_inventory.py [--repo ROOT] [--out phase2]

Safety: read-only. Uses read-only URI mode for SQLite files and never opens a
live PostgreSQL database for writes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import psycopg  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Domains declared by the provider registry (source of truth for names)
# ---------------------------------------------------------------------------

_DEFAULT_DB_FILES = {
    "audit": "audit.db",
    "news": "news.db",
    "candle_intel": "candle_intel.db",
    "strategies": "strategies.db",
    "models": "models.db",
    "marketplace": "marketplace.db",
    "experiments": "experiments/experiments.db",
}

_KNOWN_SQLITE_NAMES = {
    "audit.db",
    "news.db",
    "candle_intel.db",
    "strategies.db",
    "models.db",
    "marketplace.db",
    "nexus.db",
    "nexus_scalp.db",
    "research.db",
    "settings.db",
    "app_settings.db",
    "test_audit.db",
    "test_pipeline_health.db",
    "learning_cycles.db",
    "experiments.db",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sqlite_tables(path: str) -> list[dict[str, object]]:
    """Table summary for a SQLite file via read-only URI mode."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        out: list[dict[str, object]] = []
        for name in names:
            cols = [r for r in conn.execute(f'PRAGMA table_xinfo("{name}")')]
            pk = [c[1] for c in cols if c[5]]
            uniq: list[str] = []
            for idx in conn.execute(f'PRAGMA index_list("{name}")'):
                if idx[2]:
                    cols_of = [
                        r[2]
                        for r in conn.execute(f'PRAGMA index_info("{idx[1]}")')
                    ]
                    uniq.append("(" + ",".join(cols_of) + ")")
            fks = [
                f"({r[2]} -> {r[3]}.{r[4]})"
                for r in conn.execute(f'PRAGMA foreign_key_list("{name}")')
            ]
            try:
                count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except Exception:
                count = None
            try:
                latest = None
                ts_cols = [c[1] for c in cols if re.search(r"(at|time|date)$", c[1] or "")]
                for tc in ts_cols:
                    try:
                        v = conn.execute(
                            f'SELECT MAX("{tc}") FROM "{name}"'
                        ).fetchone()[0]
                        if v is not None and (latest is None or str(v) > str(latest)):
                            latest = str(v)
                    except Exception:
                        pass
            except Exception:
                latest = None
            out.append(
                {
                    "table": name,
                    "columns": [c[1] for c in cols],
                    "primary_key": pk,
                    "unique_constraints": uniq,
                    "foreign_keys": fks,
                    "row_count": count,
                    "latest_timestamp": latest,
                }
            )
        return out
    finally:
        conn.close()


def _sqlite_sidecars(path: Path) -> dict[str, object]:
    return {
        "wal_exists": (path.parent / (path.name + "-wal")).exists(),
        "shm_exists": (path.parent / (path.name + "-shm")).exists(),
        "wal_bytes": _file_size(path.parent / (path.name + "-wal")),
        "shm_bytes": _file_size(path.parent / (path.name + "-shm")),
    }


def _file_size(p: Path) -> int | None:
    try:
        return p.stat().st_size
    except Exception:
        return None


def _discover_sqlite_files(repo: Path) -> list[Path]:
    """All .db/.sqlite/.sqlite3 files under the repo + the live artifacts tree."""
    root = repo if (repo / "artifacts").exists() else repo
    found: dict[str, Path] = {}
    for pat in ("*.db", "*.sqlite", "*.sqlite3"):
        for p in root.rglob(pat):
            if ".git" in p.parts or "node_modules" in p.parts:
                continue
            found[str(p.resolve())] = p
    # The live engine writes into the MAIN checkout's artifacts (the worktree
    # may be pristine); include it when it is a different directory.
    main_artifacts = repo.parent / "NexusTradingForexBot" / "artifacts"
    if main_artifacts.exists() and (main_artifacts).resolve() != (root / "artifacts").resolve():
        for pat in ("*.db", "*.sqlite", "*.sqlite3"):
            for p in main_artifacts.rglob(pat):
                found[str(p.resolve())] = p
    return sorted(found.values(), key=lambda p: str(p))


def _discover_user_data_sqlite() -> list[Path]:
    """AppData user-data stores the packaged app / settings service owns."""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "NexusScalpEngine"
    if not base.exists():
        return []
    return sorted(
        p
        for pat in ("*.db", "*.sqlite", "*.sqlite3")
        for p in base.rglob(pat)
    )


def _pg_databases(uri: str) -> list[dict[str, object]]:
    if psycopg is None:
        return [{"error": "psycopg not installed"}]
    try:
        conn = psycopg.connect(uri, connect_timeout=5)
    except Exception as exc:
        return [{"error": f"connect failed: {type(exc).__name__}: {exc}"}]
    try:
        dbs = [
            r[0]
            for r in conn.execute(
                "SELECT datname FROM pg_database WHERE datistemplate = false "
                "ORDER BY datname"
            )
        ]
        out: list[dict[str, object]] = []
        for db in dbs:
            try:
                c2 = psycopg.connect(uri.rsplit("/", 1)[0] + "/" + db, connect_timeout=5)
            except Exception as exc:
                out.append({"database": db, "error": str(exc)[:200]})
                continue
            try:
                tabs = [
                    r[0]
                    for r in c2.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' ORDER BY table_name"
                    )
                ]
                counts = {}
                for t in tabs:
                    try:
                        counts[t] = c2.execute(
                            'SELECT COUNT(*) FROM "%s"' % t
                        ).fetchone()[0]
                    except Exception:
                        counts[t] = None
                out.append(
                    {
                        "database": db,
                        "tables": tabs,
                        "table_count": len(tabs),
                        "row_counts": counts,
                    }
                )
            finally:
                c2.close()
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Settings store (which provider the runtime actually selected)
# ---------------------------------------------------------------------------


def _runtime_provider(repo: Path) -> dict[str, object]:
    """Read the live settings store for database.provider.

    The packaged/settings app data dir (`$LOCALAPPDATA/NexusScalpEngine`) holds
    the REAL runtime settings DB (databases/app_settings.db) — the repo's
    artifacts/*.db copies are stale stubs. Probe the user-data store first,
    then fall back to any repo store that actually has rows. The source column
    records WHO persisted the value (USER_SETTINGS / WEB_UI / a wave name).
    """
    out: dict[str, object] = {"resolved": False, "provider": None}

    def _probe(p: Path) -> bool:
        try:
            conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        except Exception:
            return False
        try:
            tabs = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "application_settings" not in tabs:
                return False
            rows = list(
                conn.execute(
                    "SELECT key, value, source, updated_at FROM application_settings "
                    "WHERE key LIKE 'database%'"
                )
            )
            if not rows:
                return False
            out["resolved"] = True
            out["settings_db"] = str(p)
            out["entries"] = [
                {"key": r[0], "value": r[1], "source": r[2], "updated_at": r[3]}
                for r in rows
            ]
            prov = [r for r in rows if r[0] == "database.provider"]
            out["provider"] = prov[0][1] if prov else None
            return True
        except Exception:
            return False
        finally:
            conn.close()

    base = Path(os.environ.get("LOCALAPPDATA", "")) / "NexusScalpEngine"
    if base.exists():
        for p in sorted(base.rglob("*.db")) + sorted(base.rglob("*.sqlite*")):
            if _probe(p):
                return out
    main_artifacts = repo.parent / "NexusTradingForexBot" / "artifacts"
    for root in (repo / "artifacts", main_artifacts):
        if root.exists():
            for p in sorted(root.glob("*.db")):
                if _probe(p):
                    return out
    return out


# ---------------------------------------------------------------------------
# Source-code derived domain map (reader/writer/purpose per table family)
# ---------------------------------------------------------------------------

_DOMAIN_MAP: list[dict[str, object]] = [
    {
        "domain": "audit",
        "provider": "sqlite file audit.db | postgresql (fabric audit plane)",
        "tables": [
            "audit_signals",
            "audit_orders",
            "audit_ledger",
            "audit_experiences",
            "audit_experience_outcomes",
            "audit_executions",
            "audit_account_snapshots",
            "audit_dead_letter",
        ],
        "owner": "adapters/database/audit_repository.py (AuditRepository)",
        "writer": "background write queue / AuditWritePlane (fabric pooled backend under PG)",
        "reader": "provider_store.query_rows / _connect_sqlite seam",
        "purpose": "immutable experience ledger — source of truth for all research",
    },
    {
        "domain": "model lifecycle",
        "provider": "sqlite experience_model_registry | PG table of same name",
        "tables": [
            "experience_model_registry",
            "training_runs",
            "model_comparisons",
            "model_governance_events",
            "model_governance_state",
            "model_promotion_audit",
            "model_rollback_audit",
            "model_runtime_health",
            "model_load_history",
        ],
        "owner": "model_lifecycle/registry.py + experience/provenance.py",
        "writer": "ModelLifecycleRegistry.set_status / register_candidate (SQLite-only path)",
        "reader": "ModelLifecycleRegistry.get_status / list_models / champion",
        "purpose": "champion/challenger lifecycle state + promotion lineage",
    },
    {
        "domain": "research / strategy",
        "provider": "sqlite strategy_registry + research_* | PG tables of same names",
        "tables": [
            "strategy_registry",
            "research_runs",
            "research_gates",
            "research_events",
            "research_evidence",
            "research_run_snapshots",
            "research_worker_state",
            "research_worker_heartbeat",
        ],
        "owner": "research/registry.py + research/pipeline.py + research/observability.py",
        "writer": "research pipeline _record_run; observability gate/evidence inserters",
        "reader": "research/store.py read facade (SQLite-only path)",
        "purpose": "derived strategy validation memory; rebuildable from the ledger",
    },
    {
        "domain": "shadow / robustness",
        "provider": "sqlite shadow_* + shadow70_* | PG tables of same names",
        "tables": [
            "shadow_runs",
            "shadow_decisions",
            "shadow_comparisons",
            "shadow_promotions",
            "shadow70_observations",
            "shadow70_events",
            "shadow70_feature_health",
            "shadow70_drift_alerts",
        ],
        "owner": "shadow/store.py + shadow/shadow70/store.py",
        "writer": "shadow recorders via ops_queue_write(domain=...)",
        "reader": "shadow stores via ops_query_rows",
        "purpose": "challenger shadow evidence; never production authority",
    },
    {
        "domain": "news intelligence",
        "provider": "PG-only on this box (news.db present but empty in live tree)",
        "tables": ["news_articles", "news_analysis", "news_topics", "news_entities"],
        "owner": "news store (news.db domain)",
        "writer": "news worker",
        "reader": "news API",
        "purpose": "news analysis intelligence",
    },
    {
        "domain": "settings",
        "provider": "sqlite application_settings table inside the audit db",
        "tables": ["application_settings", "settings_audit"],
        "owner": "settings/service.py",
        "writer": "settings service (source column records the actor)",
        "reader": "settings service + DB fabric bootstrap",
        "purpose": "persisted provider selection + audit of who changed it",
    },
    {
        "domain": "artifacts / model bundles",
        "provider": "filesystem (gitignored)",
        "tables": [],
        "owner": "application/live/model_bundle_store + model_generation",
        "writer": "trainer (model.pt + sibling scaler + signed manifest.json + model.meta.json)",
        "reader": "champion loader / load gate",
        "purpose": "serving model bytes + governance fingerprints",
    },
]


def build_inventory(repo: Path, pg_uri: str | None) -> dict[str, object]:
    repo = repo.resolve()
    sqlite_files = _discover_sqlite_files(repo)
    user_data = _discover_user_data_sqlite()
    stores: list[dict[str, object]] = []

    for p in sqlite_files + user_data:
        entry: dict[str, object] = {
            "kind": "sqlite",
            "path": str(p),
            "bytes": _file_size(p),
            "modified_utc": datetime.fromtimestamp(
                p.stat().st_mtime, tz=timezone.utc
            ).isoformat()
            if p.exists()
            else None,
        }
        entry.update(_sqlite_sidecars(p))
        entry["tables"] = _sqlite_tables(str(p))
        entry["table_count"] = len([t for t in entry["tables"] if "table" in t])
        stores.append(entry)

    pg = []
    if pg_uri:
        pg = _pg_databases(pg_uri)
    for db in pg:
        if "error" in db:
            continue
        stores.append(
            {
                "kind": "postgresql",
                "database": db["database"],
                "table_count": db.get("table_count"),
                "tables": db.get("tables", []),
                "row_counts": db.get("row_counts", {}),
            }
        )

    return {
        "generated_at": _utc_now(),
        "repo_root": str(repo),
        "sqlite_file_count": len(sqlite_files),
        "user_data_sqlite_count": len(user_data),
        "provider_runtime": _runtime_provider(repo),
        "domain_map": _DOMAIN_MAP,
        "stores": stores,
        "postgresql_databases": pg,
    }


def _render_markdown(inv: dict[str, object]) -> str:
    lines: list[str] = []
    lines.append("# Phase 2A — Storage Inventory")
    lines.append("")
    lines.append(f"generated: {inv['generated_at']}")
    lines.append(f"repo: `{inv['repo_root']}`")
    prov = inv["provider_runtime"]
    lines.append("")
    lines.append("## Runtime provider (from the live settings store)")
    lines.append("")
    if prov.get("resolved"):
        lines.append(f"- resolved provider: **{prov.get('provider')}**")
        lines.append(f"- settings store: `{prov.get('settings_db')}`")
        for e in prov.get("entries", []):  # type: ignore[union-attr]
            lines.append(
                f"  - `{e['key']}` = `{e['value']}` (source `{e['source']}`)"  # type: ignore[index]
            )
    else:
        lines.append("- NOT RESOLVED (no application_settings rows persisted)")
    lines.append("")
    lines.append("## SQLite stores")
    lines.append("")
    lines.append(
        "| path | bytes | tables | WAL | SHM | latest ts |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for s in inv["stores"]:
        if s.get("kind") != "sqlite":
            continue
        tabs = s.get("tables", [])
        latest = None
        for t in tabs:
            if isinstance(t, dict) and t.get("latest_timestamp"):
                v = str(t["latest_timestamp"])
                if latest is None or v > latest:
                    latest = v
        lines.append(
            f"| `{s['path']}` | {s.get('bytes')} | {s.get('table_count')} | "
            f"{'yes' if s.get('wal_exists') else 'no'} | "
            f"{'yes' if s.get('shm_exists') else 'no'} | {latest} |"
        )
    lines.append("")
    lines.append("## PostgreSQL databases")
    lines.append("")
    for db in inv.get("postgresql_databases", []):
        if "error" in db:
            lines.append(f"- `{db.get('database')}` — ERROR: {db['error']}")
            continue
        lines.append(
            f"- `{db['database']}` — {db.get('table_count')} tables"
        )
    lines.append("")
    lines.append("## Domain map (source-derived)")
    lines.append("")
    for d in inv["domain_map"]:
        lines.append(f"### {d['domain']}")
        lines.append(f"- provider: {d['provider']}")
        lines.append(f"- tables: {', '.join(d['tables']) if d['tables'] else '(filesystem)'}")
        lines.append(f"- owner: `{d['owner']}`")
        lines.append(f"- writer: {d['writer']}")
        lines.append(f"- reader: {d['reader']}")
        lines.append(f"- purpose: {d['purpose']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[3]))
    ap.add_argument("--out", default="phase2")
    ap.add_argument("--pg-uri", default=None)
    args = ap.parse_args()

    repo = Path(args.repo)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    inv = build_inventory(repo, args.pg_uri)
    (out_dir / "storage_inventory.json").write_text(
        json.dumps(inv, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "storage_inventory.md").write_text(
        _render_markdown(inv), encoding="utf-8"
    )
    print(f"inventory: {len(inv['stores'])} stores -> {out_dir}")
    prov = inv["provider_runtime"]
    print(f"runtime provider: {prov.get('provider')} (resolved={prov.get('resolved')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
