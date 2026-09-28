"""SQLite infrastructure for saved Neural Studio model-builder configurations.

WHY THIS MODULE LIVES HERE (not in ``model_lab/``)
--------------------------------------------------
The database fabric guards (``tests/unit/test_fabric_guards.py``) pin a hard
architectural rule: domain code must not import ``sqlite3`` or open a raw
connection. SQLite-specific behaviour belongs to the ``database/`` layer.

``model_lab/model_builder.py`` owns the CONTRACT (what a saved configuration is,
how it is hashed, what it means for reproducibility). This module owns only the
persistence mechanics — the connection, the schema and the row mapping — and
exposes a typed, sqlite-free surface back to the domain.

The store shares ``artifacts/models.db`` with the model registry so the registry
stays the single authoritative inventory and no competing store appears. Its
table is NEW and additive; the registry's own schema is untouched.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.model_lab.model_builder import (
    ModelBuilderConfig,
    SavedBuilderConfig,
)
from nexus_scalp.model_lab.model_builder import (
    DIMENSION_TO_SCHEMA_ID,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _hash_payload(payload: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class BuilderConfigStore:
    """SQLite store for saved model-builder configurations.

    The domain-facing API is sqlite-free: it speaks ``SavedBuilderConfig`` and
    ``ModelBuilderConfig``, never connections or rows.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = _REPO_ROOT / "artifacts" / "models.db"
        self._db_path = Path(db_path) if str(db_path) != ":memory:" else db_path
        self._lock = threading.RLock()
        self._mem_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        assert isinstance(self._db_path, Path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _close(self, conn: sqlite3.Connection) -> None:
        if conn is not self._mem_conn:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS model_builder_configs (
                            config_id TEXT PRIMARY KEY,
                            config_name TEXT NOT NULL,
                            dimension INTEGER NOT NULL,
                            schema_id TEXT NOT NULL,
                            config_json TEXT NOT NULL,
                            config_sha256 TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        );
                        """
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_builder_cfg_name "
                        "ON model_builder_configs(config_name);"
                    )
            finally:
                self._close(conn)

    def save(self, cfg: ModelBuilderConfig) -> SavedBuilderConfig:
        payload = cfg.to_dict()
        payload_sha = _hash_payload(payload)
        config_id = f"cfg_{int(datetime.now(UTC).timestamp())}_{payload_sha[:8]}"
        row = SavedBuilderConfig(
            config_id=config_id,
            config_name=cfg.config_name,
            dimension=cfg.dimension,
            schema_id=cfg.schema_id or DIMENSION_TO_SCHEMA_ID.get(cfg.dimension, ""),
            config_json=json.dumps(payload, sort_keys=True, default=str),
            config_sha256=payload_sha,
            created_at=_now(),
        )
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO model_builder_configs (
                            config_id, config_name, dimension, schema_id,
                            config_json, config_sha256, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(config_id) DO UPDATE SET
                            config_name=excluded.config_name,
                            dimension=excluded.dimension,
                            schema_id=excluded.schema_id,
                            config_json=excluded.config_json,
                            config_sha256=excluded.config_sha256;
                        """,
                        (
                            row.config_id,
                            row.config_name,
                            row.dimension,
                            row.schema_id,
                            row.config_json,
                            row.config_sha256,
                            row.created_at,
                        ),
                    )
                return row
            finally:
                self._close(conn)

    def list_configs(self, limit: int = 100) -> list[SavedBuilderConfig]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM model_builder_configs
                    ORDER BY created_at DESC LIMIT ?;
                    """,
                    (limit,),
                ).fetchall()
                return [self._row_to_config(r) for r in rows]
            finally:
                self._close(conn)

    def get_config(self, config_id: str) -> SavedBuilderConfig | None:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM model_builder_configs WHERE config_id = ?;",
                    (config_id,),
                ).fetchone()
                return self._row_to_config(row) if row else None
            finally:
                self._close(conn)

    @staticmethod
    def _row_to_config(row: sqlite3.Row) -> SavedBuilderConfig:
        return SavedBuilderConfig(
            config_id=str(row["config_id"]),
            config_name=str(row["config_name"]),
            dimension=int(row["dimension"]),
            schema_id=str(row["schema_id"]),
            config_json=str(row["config_json"]),
            config_sha256=str(row["config_sha256"]),
            created_at=str(row["created_at"]),
        )


_STORE: BuilderConfigStore | None = None
_STORE_LOCK = threading.Lock()


def get_builder_config_store() -> BuilderConfigStore:
    global _STORE  # noqa: PLW0603  # singleton, double-checked under _STORE_LOCK
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = BuilderConfigStore()
    return _STORE
