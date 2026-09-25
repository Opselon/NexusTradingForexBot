"""Model Registry & SQLite Persistent Catalog for Neural Models.

Provides ACID persistence and metadata tracking for PyTorch ScalpNet models
and sidecars (weights, scalers, validation metrics, training lineage).

Supports:
  - SQLite metadata store (artifacts/models.db)
  - Active champion & canary tracking with atomic state switching
  - Rollback to previous champion via audited load history
  - Scaler sidecar (.scaler.npz) tracking and association
  - Fine-tune eligibility and mode tracking
  - Automatic filesystem checkpoint synchronization
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.model_registry")

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class ModelRecord:
    """Represents a registered neural model checkpoint and sidecar metadata."""

    id: str
    name: str
    version: str = "1.0.0"
    dimension: int = 50
    architecture: str = "ScalpNet"
    weights_path: str = ""
    scaler_path: str = ""
    manifest_path: str = ""
    sha256: str = ""
    epochs: int = 0
    final_loss: float = 0.0
    final_val_loss: float = 0.0
    accuracy: float = 0.0
    dataset_path: str = ""
    is_active: bool = False
    fine_tune_enabled: bool = False
    stage: str = "STAGING"  # CHAMPION, CANARY, STAGING, ARCHIVED
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    loaded_at: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_active"] = bool(self.is_active)
        d["fine_tune_enabled"] = bool(self.fine_tune_enabled)
        return d


class ModelRegistry:
    """Thread-safe SQLite persistent registry for Model Studio checkpoints."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is not None:
            self._db_path = Path(db_path) if str(db_path) != ":memory:" else db_path
        else:
            cfg = DatabaseConfig.for_sqlite("models")
            self._db_path = Path(cfg.sqlite_connect_path)

        self._lock = threading.RLock()
        self._mem_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", timeout=15.0, check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _get_connection(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        assert isinstance(self._db_path, Path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _close_conn(self, conn: sqlite3.Connection) -> None:
        if conn is not self._mem_conn:
            conn.close()

    def _ensure_schema(self) -> None:
        """Initializes tables for checkpoints and load audit log."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS model_checkpoints (
                            id TEXT PRIMARY KEY,
                            name TEXT NOT NULL,
                            version TEXT NOT NULL,
                            dimension INTEGER NOT NULL,
                            architecture TEXT NOT NULL,
                            weights_path TEXT NOT NULL,
                            scaler_path TEXT DEFAULT '',
                            manifest_path TEXT DEFAULT '',
                            sha256 TEXT NOT NULL,
                            epochs INTEGER DEFAULT 0,
                            final_loss REAL DEFAULT 0.0,
                            final_val_loss REAL DEFAULT 0.0,
                            accuracy REAL DEFAULT 0.0,
                            dataset_path TEXT DEFAULT '',
                            is_active INTEGER DEFAULT 0,
                            fine_tune_enabled INTEGER DEFAULT 0,
                            stage TEXT DEFAULT 'STAGING',
                            created_at TEXT NOT NULL,
                            loaded_at TEXT,
                            metrics_json TEXT DEFAULT '{}'
                        );
                        """
                    )
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS model_load_history (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            model_id TEXT NOT NULL,
                            action TEXT NOT NULL,
                            source TEXT DEFAULT 'WEB_UI',
                            dimension INTEGER NOT NULL,
                            scaler_attached INTEGER DEFAULT 0,
                            fine_tune_enabled INTEGER DEFAULT 0,
                            latency_p50_us REAL DEFAULT 0.0,
                            operator TEXT DEFAULT 'SYSTEM',
                            details_json TEXT DEFAULT '{}',
                            created_at TEXT NOT NULL
                        );
                        """
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_model_active ON model_checkpoints(is_active);"
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_model_created ON model_checkpoints(created_at);"
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_history_created ON model_load_history(created_at);"
                    )
            finally:
                self._close_conn(conn)

    def register_model(self, record: ModelRecord) -> ModelRecord:
        """Inserts or updates a model record in SQLite."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO model_checkpoints (
                            id, name, version, dimension, architecture, weights_path,
                            scaler_path, manifest_path, sha256, epochs, final_loss,
                            final_val_loss, accuracy, dataset_path, is_active,
                            fine_tune_enabled, stage, created_at, loaded_at, metrics_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            name=excluded.name,
                            version=excluded.version,
                            dimension=excluded.dimension,
                            weights_path=excluded.weights_path,
                            scaler_path=excluded.scaler_path,
                            manifest_path=excluded.manifest_path,
                            sha256=excluded.sha256,
                            epochs=excluded.epochs,
                            final_loss=excluded.final_loss,
                            final_val_loss=excluded.final_val_loss,
                            accuracy=excluded.accuracy,
                            dataset_path=excluded.dataset_path,
                            fine_tune_enabled=excluded.fine_tune_enabled,
                            stage=excluded.stage,
                            metrics_json=excluded.metrics_json;
                        """,
                        (
                            record.id,
                            record.name,
                            record.version,
                            record.dimension,
                            record.architecture,
                            record.weights_path,
                            record.scaler_path,
                            record.manifest_path,
                            record.sha256,
                            record.epochs,
                            record.final_loss,
                            record.final_val_loss,
                            record.accuracy,
                            record.dataset_path,
                            1 if record.is_active else 0,
                            1 if record.fine_tune_enabled else 0,
                            record.stage,
                            record.created_at,
                            record.loaded_at,
                            json.dumps(record.metrics, default=str),
                        ),
                    )
                return record
            finally:
                self._close_conn(conn)

    def list_models(self, include_archived: bool = True) -> list[ModelRecord]:
        """Lists all registered models sorted newest first."""
        with self._lock:
            conn = self._get_connection()
            try:
                query = "SELECT * FROM model_checkpoints"
                if not include_archived:
                    query += " WHERE stage != 'ARCHIVED'"
                query += " ORDER BY is_active DESC, created_at DESC;"
                cursor = conn.execute(query)
                rows = cursor.fetchall()
                return [self._row_to_record(r) for r in rows]
            finally:
                self._close_conn(conn)

    def get_model(self, model_id: str) -> ModelRecord | None:
        """Retrieves a single model by identifier."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute("SELECT * FROM model_checkpoints WHERE id = ?;", (model_id,))
                row = cursor.fetchone()
                return self._row_to_record(row) if row else None
            finally:
                self._close_conn(conn)

    def get_active_model(self) -> ModelRecord | None:
        """Returns the currently active champion model, or None."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute(
                    "SELECT * FROM model_checkpoints WHERE is_active = 1 LIMIT 1;"
                )
                row = cursor.fetchone()
                return self._row_to_record(row) if row else None
            finally:
                self._close_conn(conn)

    def set_active_champion(
        self,
        model_id: str,
        *,
        operator: str = "SYSTEM",
        source: str = "WEB_UI",
        fine_tune_enabled: bool | None = None,
        latency_p50_us: float = 0.0,
    ) -> ModelRecord:
        """Sets the specified model as the active champion, deactivating all others."""
        with self._lock:
            conn = self._get_connection()
            try:
                now = datetime.now(UTC).isoformat()
                with conn:
                    # Deactivate existing
                    conn.execute("UPDATE model_checkpoints SET is_active = 0 WHERE is_active = 1;")
                    # Update target
                    if fine_tune_enabled is not None:
                        conn.execute(
                            """
                            UPDATE model_checkpoints
                            SET is_active = 1, stage = 'CHAMPION', loaded_at = ?, fine_tune_enabled = ?
                            WHERE id = ?;
                            """,
                            (now, 1 if fine_tune_enabled else 0, model_id),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE model_checkpoints
                            SET is_active = 1, stage = 'CHAMPION', loaded_at = ?
                            WHERE id = ?;
                            """,
                            (now, model_id),
                        )

                    # Fetch updated record
                    cursor = conn.execute(
                        "SELECT * FROM model_checkpoints WHERE id = ?;", (model_id,)
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise ValueError(f"Model ID {model_id} not found in registry")
                    rec = self._row_to_record(row)

                    # Log to history
                    conn.execute(
                        """
                        INSERT INTO model_load_history (
                            model_id, action, source, dimension, scaler_attached,
                            fine_tune_enabled, latency_p50_us, operator, details_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            model_id,
                            "HOT_LOAD",
                            source,
                            rec.dimension,
                            1 if bool(rec.scaler_path) else 0,
                            1 if rec.fine_tune_enabled else 0,
                            latency_p50_us,
                            operator,
                            json.dumps({"sha256": rec.sha256, "weights_path": rec.weights_path}),
                            now,
                        ),
                    )
                    return rec
            finally:
                self._close_conn(conn)

    def get_previous_active_model(self) -> ModelRecord | None:
        """Identifies the model active prior to the current active one from history."""
        with self._lock:
            conn = self._get_connection()
            try:
                # Find distinct previous hot_loaded models in history
                cursor = conn.execute(
                    """
                    SELECT model_id FROM model_load_history
                    WHERE action IN ('HOT_LOAD', 'ROLLBACK')
                    ORDER BY id DESC
                    LIMIT 20;
                    """
                )
                rows = cursor.fetchall()
                history_ids = [r["model_id"] for r in rows]
                if len(history_ids) < 2:
                    return None
                current = history_ids[0]
                prev_id = next((mid for mid in history_ids[1:] if mid != current), None)
                if not prev_id:
                    return None
                return self.get_model(prev_id)
            finally:
                self._close_conn(conn)

    def update_model_stage(
        self,
        model_id: str,
        stage: str,
        fine_tune_enabled: bool | None = None,
    ) -> ModelRecord | None:
        """Updates model stage (CHAMPION, CANARY, STAGING, ARCHIVED)."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    if fine_tune_enabled is not None:
                        conn.execute(
                            "UPDATE model_checkpoints SET stage = ?, fine_tune_enabled = ? WHERE id = ?;",
                            (stage, 1 if fine_tune_enabled else 0, model_id),
                        )
                    else:
                        conn.execute(
                            "UPDATE model_checkpoints SET stage = ? WHERE id = ?;",
                            (stage, model_id),
                        )
                return self.get_model(model_id)
            finally:
                self._close_conn(conn)

    def record_action(
        self,
        model_id: str,
        action: str,
        *,
        source: str = "WEB_UI",
        dimension: int = 50,
        scaler_attached: bool = False,
        fine_tune_enabled: bool = False,
        latency_p50_us: float = 0.0,
        operator: str = "SYSTEM",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Logs an event to the model_load_history audit ledger."""
        with self._lock:
            conn = self._get_connection()
            try:
                now = datetime.now(UTC).isoformat()
                with conn:
                    conn.execute(
                        """
                        INSERT INTO model_load_history (
                            model_id, action, source, dimension, scaler_attached,
                            fine_tune_enabled, latency_p50_us, operator, details_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            model_id,
                            action,
                            source,
                            dimension,
                            1 if scaler_attached else 0,
                            1 if fine_tune_enabled else 0,
                            latency_p50_us,
                            operator,
                            json.dumps(details or {}, default=str),
                            now,
                        ),
                    )
            finally:
                self._close_conn(conn)

    def get_load_history(self, limit: int = 30) -> list[dict[str, Any]]:
        """Returns the recent model lifecycle history."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute(
                    """
                    SELECT h.*, m.name as model_name, m.sha256 as model_sha256
                    FROM model_load_history h
                    LEFT JOIN model_checkpoints m ON h.model_id = m.id
                    ORDER BY h.id DESC
                    LIMIT ?;
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()
                out = []
                for r in rows:
                    item = dict(r)
                    if "details_json" in item:
                        try:
                            item["details"] = json.loads(item["details_json"])
                        except Exception:
                            item["details"] = {}
                    out.append(item)
                return out
            finally:
                self._close_conn(conn)

    def delete_model(self, model_id: str) -> bool:
        """Deletes a model record. Refuses if model is active champion."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cursor = conn.execute(
                        "SELECT is_active, stage FROM model_checkpoints WHERE id = ?;", (model_id,)
                    )
                    row = cursor.fetchone()
                    if not row:
                        return False
                    if row["is_active"] == 1 or row["stage"] == "CHAMPION":
                        raise ValueError(
                            f"Cannot delete active champion model {model_id}. Hot-load another model first."
                        )
                    conn.execute("DELETE FROM model_checkpoints WHERE id = ?;", (model_id,))
                    return True
            finally:
                self._close_conn(conn)

    def sync_filesystem_checkpoints(self) -> int:
        """Discovers unindexed .pt checkpoints from filesystem and registers them."""
        candidate_dirs = [
            REPO_ROOT / "artifacts" / "model_generation" / "checkpoints",
            REPO_ROOT / "artifacts" / "models",
            REPO_ROOT / "models",
        ]
        registered_count = 0

        for d in candidate_dirs:
            if not d.exists():
                continue
            for pt_file in sorted(d.rglob("*.pt")):
                if pt_file.name.startswith(".") or ".tmp" in pt_file.name:
                    continue
                model_id = pt_file.stem
                if self.get_model(model_id) is not None:
                    continue

                # Inspect file without loading malicious pickle
                try:
                    import torch

                    state = torch.load(pt_file, map_location="cpu", weights_only=True)
                    dim = 50
                    if isinstance(state, dict):
                        ip = state.get("input_projection.weight")
                        if ip is not None and hasattr(ip, "shape") and ip.ndim == 2:
                            dim = int(ip.shape[1])
                except Exception:
                    dim = 50

                hasher = hashlib.sha256()
                with open(pt_file, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        hasher.update(chunk)
                file_sha256 = hasher.hexdigest()

                # Check for sidecar scaler
                scaler_candidates = [
                    pt_file.with_suffix(".scaler.npz"),
                    pt_file.parent / "scaler.npz",
                    pt_file.with_name(f"{pt_file.stem}.scaler.npz"),
                ]
                scaler_path_str = ""
                for sc in scaler_candidates:
                    if sc.is_file():
                        try:
                            scaler_path_str = str(sc.relative_to(REPO_ROOT))
                        except ValueError:
                            scaler_path_str = str(sc)
                        break

                try:
                    rel_weights = str(pt_file.relative_to(REPO_ROOT))
                except ValueError:
                    rel_weights = str(pt_file)

                rec = ModelRecord(
                    id=model_id,
                    name=pt_file.name,
                    version="1.0.0",
                    dimension=dim,
                    architecture="ScalpNet",
                    weights_path=rel_weights,
                    scaler_path=scaler_path_str,
                    sha256=file_sha256,
                    stage="STAGING",
                    fine_tune_enabled=False,
                )
                self.register_model(rec)
                registered_count += 1

        return registered_count

    def _row_to_record(self, row: sqlite3.Row) -> ModelRecord:
        metrics = {}
        if row["metrics_json"]:
            try:
                metrics = json.loads(row["metrics_json"])
            except Exception:
                metrics = {}

        return ModelRecord(
            id=row["id"],
            name=row["name"],
            version=row["version"],
            dimension=row["dimension"],
            architecture=row["architecture"],
            weights_path=row["weights_path"],
            scaler_path=row["scaler_path"],
            manifest_path=row["manifest_path"],
            sha256=row["sha256"],
            epochs=row["epochs"],
            final_loss=row["final_loss"],
            final_val_loss=row["final_val_loss"],
            accuracy=row["accuracy"],
            dataset_path=row["dataset_path"],
            is_active=bool(row["is_active"]),
            fine_tune_enabled=bool(row["fine_tune_enabled"]),
            stage=row["stage"],
            created_at=row["created_at"],
            loaded_at=row["loaded_at"],
            metrics=metrics,
        )


class _RegistryHolder:
    instance: ModelRegistry | None = None


_REGISTRY_INIT_LOCK = threading.Lock()


def get_model_registry() -> ModelRegistry:
    """Returns the singleton instance of ModelRegistry."""
    with _REGISTRY_INIT_LOCK:
        if _RegistryHolder.instance is None:
            _RegistryHolder.instance = ModelRegistry()
        return _RegistryHolder.instance
