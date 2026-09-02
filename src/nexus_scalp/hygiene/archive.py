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
