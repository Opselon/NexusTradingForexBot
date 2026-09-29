"""
Archive Manager + Cleanup Journal (TASK-11)
===========================================
Archive-before-delete with checksums (spec §14-16, §44):

  ACTIVE DB -> ARCHIVE (JSONL, immutable, checksummed, versioned)
            -> VERIFY ARCHIVE (sha256 matches)
            -> MARK ARCHIVED (journal)
            -> REMOVE FROM HOT STORE

Archive layout: artifacts/archive/<database>/<table>/<archive_id>.jsonl
Never inside active query paths; never auto-loaded by runtime discovery.

The journal is a per-run append-only JSONL at
artifacts/archive/_journal/hygiene_<run_id>.jsonl and is the audit trail
for every destructive action (spec §44).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ARCHIVE_ROOT_NAME = "archive"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ArchiveManager:
    """Checksummed, versioned archive writer. Never touches active DBs."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.archive_root = self.root / ARCHIVE_ROOT_NAME

    def _ensure_dir(self, database: str, table: str) -> Path:
        d = self.archive_root / database / table
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _sha256_hex(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def archive_rows(
        self,
        database: str,
        table: str,
        rows: list[dict[str, Any]],
        *,
        retention_reason: str,
        software_version: str,
        source_schema_version: str = "1",
    ) -> dict[str, Any]:
        """
        Writes rows as one checksummed JSONL archive file.

        Returns manifest: archive_id, path, row_count, sha256, time_range,
        created_at. VERIFY by re-reading the file and re-hashing.
        """
        if not rows:
            return {}
        d = self._ensure_dir(database, table)
        archive_id = f"HYG-{uuid.uuid4().hex[:12]}"
        out_path = d / f"{archive_id}.jsonl"
        lines: list[str] = []
        for row in rows:
            lines.append(json.dumps(row, default=str, sort_keys=True))
        blob = ("\n".join(lines) + "\n").encode("utf-8")
        out_path.write_bytes(blob)
        digest = self._sha256_hex(blob)

        # Read back and verify.
        read_back = out_path.read_bytes()
        verified = self._sha256_hex(read_back) == digest
        if not verified:
            raise RuntimeError(f"[DB_HYGIENE] archive verification FAILED for {out_path.name}")

        timestamps = [
            r.get("ts")
            or r.get("timestamp")
            or r.get("created_at")
            or r.get("published_at")
            or r.get("analyzed_at")
            or r.get("event_timestamp")
            for r in rows
        ]
        timestamps = [t for t in timestamps if t]
        manifest = {
            "archive_id": archive_id,
            "database": database,
            "table": table,
            "source_schema": source_schema_version,
            "row_count": len(rows),
            "time_range": [min(timestamps), max(timestamps)] if timestamps else [],
            "created_at": _now_iso(),
            "sha256": digest,
            "retention_reason": retention_reason,
            "software_version": software_version,
            "path": str(out_path.relative_to(self.root)),
        }
        return manifest

    def verify_archive(self, manifest: dict[str, Any]) -> bool:
        """Re-hashes the archived file and compares with the manifest sha256."""
        rel = manifest.get("path", "")
        if not rel:
            return False
        p = self.root / rel
        if not p.exists():
            return False
        digest = self._sha256_hex(p.read_bytes())
        return digest == manifest.get("sha256")

    def read_archive(
        self, manifest: dict[str, Any], *, missing_ok: bool = False
    ) -> list[dict[str, Any]]:
        """Read an archive back into rows — the RESTORE half of the contract.

        ``archive_rows`` + ``verify_archive`` prove an archive was written
        correctly; neither can prove it can be READ back, which is the only
        thing that makes "archive before delete" a recoverable action rather
        than a deferred delete. A retirement or purge whose archive has no
        reader is an unverifiable deletion, so this method exists to close
        that loop.

        Verifies the checksum BEFORE parsing (never trust a corrupt file) and
        raises ``RuntimeError`` on a mismatch rather than returning partial
        rows. ``missing_ok=True`` returns ``[]`` for an absent archive, so a
        caller can distinguish "nothing was archived" from "the archive is
        corrupt" — those must not collapse into the same result.
        """
        rel = manifest.get("path", "")
        if not rel:
            if missing_ok:
                return []
            raise RuntimeError("[DB_HYGIENE] archive manifest has no path")
        p = self.root / rel
        if not p.exists():
            if missing_ok:
                return []
            raise RuntimeError(f"[DB_HYGIENE] archive missing: {rel}")
        # FAIL CLOSED on an absent checksum: a manifest without a sha256
        # cannot be verified, and "cannot verify" must never read as
        # "verified".
        if not manifest.get("sha256"):
            raise RuntimeError(
                f"[DB_HYGIENE] archive manifest for {rel} has no sha256 - refusing "
                "to parse unverifiable bytes (pass missing_ok=True to opt out)"
            )
        if not self.verify_archive(manifest):
            raise RuntimeError(f"[DB_HYGIENE] archive checksum MISMATCH: {rel}")
        rows: list[dict[str, Any]] = []
        for raw in p.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            rows.append(json.loads(stripped))
        return rows

    def row_count_on_disk(self, manifest: dict[str, Any]) -> int:
        """Non-empty LINE count of an archive, independent of the manifest.

        A manifest's ``row_count`` is a claim made by the writer. Counting the
        lines actually present is the independent measurement that catches a
        truncated write, so verification compares the two rather than trusting
        either alone.
        """
        rel = manifest.get("path", "")
        if not rel:
            return 0
        p = self.root / rel
        if not p.exists():
            return 0
        return sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())


# ---------------------------------------------------------------------------
# R-11 — the managed-database decision (which SQLite files are still walked)
# ---------------------------------------------------------------------------
#
# The worker's allowlist was a literal dict, so a dataset whose writes had moved
# to PostgreSQL stayed on the walked set forever: the cycle kept scanning a
# frozen file, found the same duplicates every time, and produced no delete
# candidates. `artifacts/news.db` measured exactly that (1,551 ms of a 1,627 ms
# cycle for 0 actionable rows), so the set is now DERIVED rather than literal.
#
# Retirement is deliberately provider-aware, not a deletion: under PostgreSQL
# the file is frozen and the domain lives in PG, so walking it is pure cost;
# under SQLite it is the live store and MUST stay managed. A static removal
# would silently un-manage the default install.

#: SQLite datasets retired from the walked set, with the evidence that retired
#: them. The entry is kept (not deleted) so the decision is auditable and
#: reversible: ``include_retired=True`` restores the pre-retirement set exactly.
RETIRED_MANAGED_DATABASES: dict[str, dict[str, str]] = {
    "news": {
        "path": "artifacts/news.db",
        "retired_by": "R-11 (db-lifecycle-2 / lane L7)",
        "reason": (
            "domain writes moved to PostgreSQL; the SQLite file is frozen (newest row "
            "2026-09-25 vs PG 2026-09-29) and every cycle re-derived the same 21,553 "
            "duplicates with delete_candidates=0"
        ),
        "remains_valid_under": "sqlite",
    },
}

#: The live managed set as it stood before R-11 (spec §4).
_MANAGED_DATABASES_BASE: dict[str, str] = {
    "audit": "artifacts/audit.db",
    "news": "artifacts/news.db",
    "candle_intel": "artifacts/candle_intel.db",
}


def managed_sqlite_databases(
    *,
    provider: str | None = None,
    include_retired: bool = False,
) -> dict[str, str]:
    """The hygiene worker's managed SQLite set, honoring R-11 retirements.

    ``provider`` is the ACTIVE database provider (``"sqlite"`` / ``"postgresql"``).
    When omitted the persisted setting is resolved through the same
    ``load_database_config`` path the rest of the runtime uses, and a resolution
    failure falls back to ``"sqlite"`` — the fail-safe direction, because
    keeping a dataset managed costs only scan time while un-managing a live
    store silently stops its maintenance.

    A retired dataset is EXCLUDED only while the provider that retired it is
    still active; flipping the box back to SQLite restores it automatically, so
    this is a provider-aware gate rather than a one-way removal.
    """
    if provider is None:
        try:
            from nexus_scalp.database.config import load_database_config

            cfg = load_database_config("audit")
            provider = "postgresql" if cfg.is_postgresql else "sqlite"
        except Exception:  # pragma: no cover - settings DB unavailable
            provider = "sqlite"
    normalized = str(provider or "sqlite").strip().lower()
    out = dict(_MANAGED_DATABASES_BASE)
    if include_retired:
        return out
    # Only a RECOGNIZED provider may retire a dataset. Comparing for
    # "not sqlite" instead would let a typo or a provider this module has never
    # heard of silently un-manage a live store, which is the failure mode the
    # whole function exists to prevent.
    if normalized not in ("sqlite", "postgresql"):
        return out
    for key, meta in RETIRED_MANAGED_DATABASES.items():
        if meta.get("remains_valid_under", "sqlite") != normalized and key in out:
            out.pop(key, None)
    return out


DEFAULT_PAGE_SIZE = 500

#: Reproducible chunked export of a news table (R-11 retirement).
#: Chunking is on the PRIMARY KEY ordering with a keyset cursor (``WHERE
#: key > ?``), never LIMIT/OFFSET: an OFFSET scan re-walks every preceding row,
#: so a 25k-row export would cost O(n^2) reads. One JSONL chunk per page also
#: bounds memory to a single chunk.
DEFAULT_CHUNK_ROWS = 2_000


def _sqlite_ro(db_path: str | Path) -> sqlite3.Connection:
    """Read-only connection; NEVER creates the file (a missing DB must raise)."""
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def table_and_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Real (non-generated) column names for a table, in declaration order."""
    return [
        str(r[1])
        for r in conn.execute(f'PRAGMA table_info("{table}")')
        # hidden=2/3 rows are generated columns; they cannot be inserted back.
        if len(r) < 7 or int(r[6] or 0) in (0, 1)
    ]


def sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    """User tables of a SQLite database, excluding FTS/shadow internals."""
    return sorted(
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts_%' ORDER BY name"
        )
    )


def _paging_key(conn: sqlite3.Connection, table: str) -> str:
    """Column to page on: the single-column primary key, else ``rowid``."""
    pk = [str(r[1]) for r in conn.execute(f'PRAGMA table_info("{table}")') if r[5]]
    return pk[0] if len(pk) == 1 else "rowid"


def enumerate_dataset(
    db_path: str | Path,
    *,
    tables: list[str] | None = None,
) -> dict[str, Any]:
    """Count every table without loading a row into memory.

    The purpose is a *transcript* of what a retirement would move: per table,
    the exact row count, column count and primary-key span, measured read-only,
    so a later "0 rows lost" claim has a number to compare against.
    """
    conn = _sqlite_ro(db_path)
    try:
        names = tables if tables is not None else sqlite_tables(conn)
        out: dict[str, Any] = {}
        for t in names:
            try:
                count = int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
            except sqlite3.Error as exc:
                out[t] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
            pk = [str(r[1]) for r in conn.execute(f'PRAGMA table_info("{t}")') if r[5]]
            entry: dict[str, Any] = {"rows": count, "columns": len(table_and_columns(conn, t))}
            if pk:
                entry["pk_columns"] = pk
                try:
                    lo, hi = conn.execute(
                        f'SELECT MIN("{pk[0]}"), MAX("{pk[0]}") FROM "{t}"'
                    ).fetchone()
                    entry["pk_min"], entry["pk_max"] = lo, hi
                except sqlite3.Error:
                    pass
            out[t] = entry
        return out
    finally:
        conn.close()


def archive_news_dataset(
    db_path: str | Path,
    archive_root: str | Path,
    *,
    software_version: str,
    retention_reason: str,
    tables: list[str] | None = None,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
) -> dict[str, Any]:
    """Archive every row of a news SQLite dataset as verified JSONL chunks.

    Writes ``archive/news/<table>/<archive_id>.jsonl`` through the shipped
    :class:`ArchiveManager`, so each chunk carries a sha256 that
    ``read_archive`` re-verifies before parsing. **Nothing is deleted here** —
    this produces the artifact a retirement needs in order to be reversible,
    and returns a manifest that can be verified and re-read independently.

    Re-runnable: an existing ``_complete.json`` marker for a table is reported
    as ``SKIPPED``, so an interrupted run resumes instead of duplicating the
    tree. A table whose archived line count does not equal the row count it
    read raises rather than reporting a partial success.
    """
    db = Path(db_path)
    root = Path(archive_root)
    conn = _sqlite_ro(db)
    am = ArchiveManager(root)
    try:
        names = tables if tables is not None else sqlite_tables(conn)
        result: dict[str, Any] = {"source": str(db), "tables": {}}
        for t in names:
            marker = root / ARCHIVE_ROOT_NAME / "news" / t / "_complete.json"
            if marker.exists():
                result["tables"][t] = {"status": "SKIPPED", "marker": str(marker)}
                continue
            cols = table_and_columns(conn, t)
            if not cols:
                result["tables"][t] = {"status": "SKIPPED", "reason": "NO_COLUMNS"}
                continue
            key = _paging_key(conn, t)
            quoted = ", ".join(f'"{c}"' for c in cols)
            select_cols = quoted if key != "rowid" else f"rowid AS _rowid_, {quoted}"
            manifests: list[dict[str, Any]] = []
            total = 0
            cursor: Any = None
            while True:
                sql = f'SELECT {select_cols} FROM "{t}"'
                params: tuple[Any, ...] = ()
                if cursor is not None:
                    sql += f' WHERE "{key}" > ?'
                    params = (cursor,)
                sql += f' ORDER BY "{key}" LIMIT ?'
                rows = [dict(r) for r in conn.execute(sql, (*params, chunk_rows)).fetchall()]
                if not rows:
                    break
                cursor = rows[-1]["_rowid_" if key == "rowid" else key]
                for r in rows:
                    r.pop("_rowid_", None)
                manifests.append(
                    am.archive_rows(
                        "news",
                        t,
                        rows,
                        retention_reason=retention_reason,
                        software_version=software_version,
                    )
                )
                total += len(rows)
                if len(rows) < chunk_rows:
                    break
            verified = all(am.verify_archive(m) for m in manifests if m)
            counted = sum(am.row_count_on_disk(m) for m in manifests if m)
            if not verified or counted != total:
                raise RuntimeError(
                    f"[DB_HYGIENE] archive verify FAILED for {t}: "
                    f"rows={total} lines={counted} verified={verified}"
                )
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                json.dumps(
                    {
                        "table": t,
                        "source": str(db),
                        "row_count": total,
                        "lines_on_disk": counted,
                        "chunks": [
                            {
                                "path": m.get("path"),
                                "sha256": m.get("sha256"),
                                "rows": m.get("row_count"),
                            }
                            for m in manifests
                            if m
                        ],
                        "created_at": _now_iso(),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            result["tables"][t] = {
                "status": "ARCHIVED",
                "row_count": total,
                "lines_on_disk": counted,
                "chunks": len(manifests),
                "verified": verified,
            }
        return result
    finally:
        conn.close()


class CleanupJournal:
    """Append-only JSONL journal of every destructive action (spec §44)."""

    def __init__(self, root: Path, run_id: str) -> None:
        # `root` must be the archive root (repo/archive); the journal lives
        # at repo/archive/_journal — never inside an active query path.
        d = Path(root) / "_journal"
        d.mkdir(parents=True, exist_ok=True)
        self._path = d / f"hygiene_{run_id}.jsonl"
        self.run_id = run_id

    def record(
        self,
        *,
        database: str,
        table: str,
        candidate_id: Any,
        canonical_row_id: Any,
        reason: str,
        action: str,
        archive_id: str = "",
        verification: str = "PENDING",
        confidence: str = "",
    ) -> None:
        entry = {
            "run_id": self.run_id,
            "database": database,
            "table": table,
            "candidate_id": str(candidate_id),
            "canonical_row_id": str(canonical_row_id) if canonical_row_id is not None else "",
            "reason": reason,
            "action": action,
            "archive_id": archive_id,
            "verification": verification,
            "confidence": confidence,
            "at": _now_iso(),
        }
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str, sort_keys=True) + "\n")

    @property
    def path(self) -> Path:
        return self._path


def read_only_connect(db_path: str) -> sqlite3.Connection:
    """Opens a SQLite DB read-only. Never creates, never writes."""
    if db_path.endswith(".db") or "?" not in db_path:
        uri = f"file:{db_path}?mode=ro"
    else:
        uri = db_path
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn
