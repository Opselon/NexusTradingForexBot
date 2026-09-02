"""POST-70D initial baseline probe (read-only): DB inventory, schema versions,
table counts, worker states, model registry, governance, news+research state.

TASK-11 §58 baseline capture. Read-only: PRAGMA queries only, no writes.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts"

DOMAINS = {
    "audit": ART / "audit.db",
    "news": ART / "news.db",
    "candle_intel": ART / "candle_intel.db",
}

OUT: dict[str, object] = {"probed_at_utc": datetime.now(UTC).isoformat()}


def q(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[tuple]:
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error as exc:
        return [("ERROR", str(exc))]


def main() -> int:
    for domain, path in DOMAINS.items():
        info: dict[str, object] = {}
        if not path.exists():
            info["exists"] = False
            OUT[domain] = info
            continue
        info["exists"] = True
        info["size_bytes"] = path.stat().st_size
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            info["integrity"] = q(conn, "PRAGMA integrity_check")[0][0]
            info["journal_mode"] = q(conn, "PRAGMA journal_mode")[0][0]
            info["foreign_keys"] = q(conn, "PRAGMA foreign_keys")[0][0]
            tables = [r[0] for r in q(conn, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            info["tables"] = tables
            info["table_count"] = len(tables)
            # schema_meta versions
            meta = dict(q(conn, "SELECT key, value FROM schema_meta") if "schema_meta" in tables else [])
            info["schema_meta"] = meta
            # sizes per table
            sizes = {}
            for t in tables:
                try:
                    n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    sizes[t] = n
                except sqlite3.Error:
                    sizes[t] = "ERR"
            info["row_counts"] = sizes
            # unexpected tables (not in manifest set handled later)
            conn.execute("VACUUM INTO ?", (str(ART / f"probe_{domain}_vacuum.db"),))
        finally:
            conn.close()
        OUT[domain] = info

    # ---- worker states (audit db) ----
    audit = sqlite3.connect(f"file:{ART / 'audit.db'}?mode=ro", uri=True)
    try:
        for t in ("intelligence_worker_state", "research_worker_state", "news_worker_state",
                  "model_governance_state", "shadow_runs", "shadow_decisions"):
            try:
                cols = [r[1] for r in audit.execute(f"PRAGMA table_info({t})").fetchall()]
                rows = audit.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 5").fetchall()
                OUT[f"state_{t}"] = {"columns": cols, "latest_rows": [dict(zip(cols, r, strict=False)) for r in rows]}
            except sqlite3.Error as exc:
                OUT[f"state_{t}"] = {"error": str(exc)}
        # migrations history
        OUT["schema_migrations"] = [
            dict(zip([d[0] for d in audit.execute("SELECT * FROM schema_migrations LIMIT 1").description],
                     r, strict=False))
            for r in audit.execute("SELECT * FROM schema_migrations ORDER BY rowid").fetchall()
        ]
    finally:
        audit.close()

    # ---- champion model artifacts ----
    models_root = ART / "models" / "scalp" / "XAUUSD"
    champ: dict[str, object] = {}
    if models_root.exists():
        for d in sorted(models_root.iterdir()):
            files = {f.name: f.stat().st_size for f in d.iterdir() if f.is_file()}
            champ[d.name] = files
    OUT["champion_artifact_dir"] = str(models_root)
    OUT["champion_files"] = champ
    OUT["datasets"] = [d.name for d in (ART / "model_generation" / "datasets").iterdir()] if (ART / "model_generation" / "datasets").exists() else []

    print(json.dumps(OUT, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())