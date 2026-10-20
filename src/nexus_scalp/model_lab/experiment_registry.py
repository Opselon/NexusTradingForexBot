"""Immutable experiment registry — unifies model_lab + model_generation (ML-EXP-001).

One schema, two write-once stores, zero ambiguity:

  * a SQLite database (``artifacts/experiments/experiments.db``) for
    bounded queries (``get_best_experiment``, ``list_experiments``);
  * a per-experiment immutable JSON manifest written next to the artifact
    bytes (``experiment_manifest.json``) for provenance + tamper detection.

Every record captures the SEVEN mandatory fields required to reproduce a
run offline: ``experiment_id``, ``git_sha``, ``dataset_hash``,
``model_config``, ``seed``, ``metrics`` and ``artifact_hash``.  An eighth
optional field (``params``) records hyperparameters that are not part of
the model architecture.

Immutability is a *contract*, not a hope:

  * the SQLite row is write-once — ``record_result`` may only fill the
    ``metrics``/``artifact_hash`` of a PENDING row once; every subsequent
    write raises :class:`ExperimentImmutabilityError`;
  * the JSON manifest is written atomically (tmp + ``os.replace``) and
    never rewritten — a second ``write_manifest`` on the same id raises;
  * the manifest carries ``manifest_sha256`` (over the canonical sorted
    payload WITHOUT the hash itself), so any later byte edit of the file
    is *detectable* by :meth:`ExperimentRegistry.verify_manifest`.

The registry is deliberately SQLite-only (NON_GOALS: no external SaaS).
WAL is enabled so a reader never blocks a writer agent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Default root: artifacts/experiments (sibling of model_generation's tree
# so both producers stay inside the repo's ignored artifacts/ canopy).
DEFAULT_ROOT = Path("artifacts") / "experiments"
DB_FILENAME = "experiments.db"
MANIFEST_FILENAME = "experiment_manifest.json"
SCHEMA_VERSION = 1

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id      TEXT    PRIMARY KEY,
    status             TEXT    NOT NULL,
    git_sha            TEXT    NOT NULL,
    git_dirty          INTEGER NOT NULL,
    working_tree_hash  TEXT    NOT NULL,
    dataset_hash       TEXT    NOT NULL,
    dataset_id         TEXT    NOT NULL,
    model_config       TEXT    NOT NULL,
    params             TEXT    NOT NULL,
    seed               INTEGER NOT NULL,
    metrics            TEXT    NOT NULL,
    artifact_hash      TEXT    NOT NULL,
    manifest_sha256    TEXT    NOT NULL,
    created_at         TEXT    NOT NULL,
    finalized_at       TEXT
);
CREATE INDEX IF NOT EXISTS ix_experiments_status ON experiments(status);
CREATE INDEX IF NOT EXISTS ix_experiments_dataset ON experiments(dataset_hash);
"""

_MANDATORY = (
    "experiment_id",
    "git_sha",
    "dataset_hash",
    "model_config",
    "seed",
    "metrics",
    "artifact_hash",
)


class ExperimentImmutabilityError(RuntimeError):
    """A finalized/recorded experiment is immutable — no second write."""


class ExperimentUnknownError(KeyError):
    """No record exists under this experiment_id."""


class ExperimentImmutableError(ValueError):
    """A manifest that was already written cannot be rewritten."""


#: Allowed characters for experiment ids. The registry writes files under
#: ``<root>/<experiment_id>/`` (the immutable manifest), so an id must never
#: contain a path separator or a traversal sequence (same guard as
#: ArtifactStore.validate_artifact_id; kept local to avoid importing the
#: torch-backed model_generation package from this slim module).
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def validate_experiment_id(experiment_id: str) -> str:
    """Rejects ids that could escape the registry root."""
    if not experiment_id or not isinstance(experiment_id, str):
        raise ValueError(f"Invalid experiment id: {experiment_id!r}")
    if not _SAFE_ID_RE.match(experiment_id):
        raise ValueError(f"Unsafe experiment id {experiment_id!r}: only [A-Za-z0-9_.-] allowed")
    if ".." in experiment_id:
        raise ValueError(f"Unsafe experiment id {experiment_id!r}: '..' not allowed")
    return experiment_id


class ExperimentRecord(BaseModel):
    """The fully-explicit, mandatory-field experiment record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str

    @field_validator("experiment_id")
    @classmethod
    def _safe_id(cls, v: str) -> str:
        return validate_experiment_id(v)

    git_sha: str
    git_dirty: bool = False
    working_tree_hash: str = ""
    dataset_id: str = ""
    dataset_hash: str
    model_config_dict: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_hash: str = ""
    manifest_sha256: str = ""
    status: str = "PENDING"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    finalized_at: str | None = None

    def to_db_row(self) -> dict[str, Any]:
        """SQLite column -> value map (JSON columns serialized)."""
        return {
            "experiment_id": self.experiment_id,
            "status": self.status,
            "git_sha": self.git_sha,
            "git_dirty": int(self.git_dirty),
            "working_tree_hash": self.working_tree_hash,
            "dataset_hash": self.dataset_hash,
            "dataset_id": self.dataset_id,
            "model_config": json.dumps(self.model_config_dict, sort_keys=True),
            "params": json.dumps(self.params, sort_keys=True),
            "seed": self.seed,
            "metrics": json.dumps(self.metrics, sort_keys=True),
            "artifact_hash": self.artifact_hash,
            "manifest_sha256": self.manifest_sha256,
            "created_at": self.created_at,
            "finalized_at": self.finalized_at,
        }

    @staticmethod
    def from_db_row(row: sqlite3.Row) -> ExperimentRecord:
        d = dict(row)
        d["model_config"] = json.loads(d.pop("model_config"))
        d["params"] = json.loads(d["params"])
        d["metrics"] = json.loads(d["metrics"])
        d["git_dirty"] = bool(d["git_dirty"])
        return ExperimentRecord(**d)


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def json_path(metric: str) -> str:
    """SQLite JSON path for a metrics key.

    SQLite's ``$['key']`` bracket form is for ARRAY indices, not object keys
    — on SQLite 3.53 ``$['val_loss']`` resolves to a key literally named
    ``[val_loss]`` and ``json_extract`` returns NULL, silently emptying
    every query. The dot form ``$.val_loss`` is correct for object keys;
    keys containing ``.`` or non-identifier characters get the (working)
    double-quoted object-key form.
    """
    if not metric:
        raise ValueError("metric name must be non-empty")
    if all(c.isalnum() or c == "_" for c in metric):
        return f"$.{metric}"
    return f'$."{metric.replace(chr(34), "")}"'


def sha256_text(payload: dict[str, Any]) -> str:
    """Deterministic sha256 over a canonical (sorted-key) JSON encoding."""
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def capture_git_revision(cwd: Path | str = ".") -> tuple[str, bool, str]:
    """(git_sha, git_dirty, working_tree_hash).

    Never raises: if git is unavailable / not a repo, returns ``("UNRESOLVED",
    True, "UNRESOLVED")`` and the caller records the fact explicitly.  When the
    tree is DIRTY the commit SHA alone is not reproducible, so a hash of the
    working-tree diff is captured alongside it (the abort condition in
    ML-EXP-001: record DIRTY with working tree diff hash, do not fail).
    """
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
        if not sha:
            return "UNRESOLVED", True, "UNRESOLVED"
        diff = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
        untracked = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
        dirty = bool(diff.strip() or untracked.strip())
        tree_hash = sha256_text({"sha": sha, "diff": diff, "untracked": untracked})
        return sha, dirty, tree_hash
    except Exception:
        return "UNRESOLVED", True, "UNRESOLVED"


class ExperimentRegistry:
    """SQLite + JSON-manifest immutable experiment store.

    Thread-safe within one process (connection-per-call under a lock); the
    SQLite file itself is safe across processes thanks to WAL + busy_timeout.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else DEFAULT_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / DB_FILENAME
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            conn.commit()

    # ------------------------------------------------------------------
    # connection handling
    # ------------------------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            isolation_level=None,  # autocommit; transactions are explicit
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self, conn: sqlite3.Connection) -> Iterator[None]:
        """Explicit BEGIN IMMEDIATE / COMMIT / ROLLBACK.

        COMMIT happens only on a clean exit, ROLLBACK only when an exception
        propagates OUT of the ``with`` body — so a validation error raised
        after BEGIN never reaches ``__exit__`` of :meth:`_connect` with an
        already-rolled-back transaction (``cannot rollback - no transaction
        is active``). The idempotent no-op path may return inside the block:
        committing an empty read-only transaction is harmless.
        """
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            with suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def _manifest_path(self, experiment_id: str) -> Path:
        return self.root / experiment_id / MANIFEST_FILENAME

    # ------------------------------------------------------------------
    # write-once registration
    # ------------------------------------------------------------------
    def register(self, record: ExperimentRecord) -> ExperimentRecord:
        """Registers a PENDING experiment. Idempotent on experiment_id:
        re-registering the *identical* record is a no-op, re-registering a
        *different* one raises (immutable identity)."""
        with self._lock, self._connect() as conn:
            with self._transaction(conn):
                existing = conn.execute(
                    "SELECT * FROM experiments WHERE experiment_id = ?",
                    (record.experiment_id,),
                ).fetchone()
                if existing is not None:
                    prev = ExperimentRecord.from_db_row(existing)
                    same_id = (
                        prev.git_sha == record.git_sha and prev.dataset_hash == record.dataset_hash
                    )
                    same_cfg = (
                        json.loads(prev.to_db_row()["model_config"]) == record.model_config_dict
                    )
                    if not (same_id and same_cfg and prev.seed == record.seed):
                        raise ExperimentImmutabilityError(
                            f"experiment {record.experiment_id!r} is already registered with "
                            "a different identity (git_sha/dataset/model_config/seed); "
                            "immutable records cannot be redefined — mint a NEW experiment_id."
                        )
                    return prev
                conn.execute(
                    """INSERT INTO experiments
                       (experiment_id, status, git_sha, git_dirty, working_tree_hash,
                        dataset_hash, dataset_id, model_config, params, seed, metrics,
                        artifact_hash, manifest_sha256, created_at, finalized_at)
                       VALUES
                       (:experiment_id, :status, :git_sha, :git_dirty, :working_tree_hash,
                        :dataset_hash, :dataset_id, :model_config, :params, :seed, :metrics,
                        :artifact_hash, :manifest_sha256, :created_at, :finalized_at)""",
                    record.to_db_row(),
                )
        return record

    def record_result(
        self,
        experiment_id: str,
        *,
        metrics: dict[str, Any],
        artifact_hash: str = "",
        manifest_sha256: str = "",
    ) -> ExperimentRecord:
        """Finalizes a PENDING experiment with its observed metrics + artifact
        hash. Write-once: a second finalize raises
        :class:`ExperimentImmutabilityError` (immutability contract)."""
        with self._lock, self._connect() as conn:
            with self._transaction(conn):
                row = conn.execute(
                    "SELECT * FROM experiments WHERE experiment_id = ?",
                    (experiment_id,),
                ).fetchone()
                if row is None:
                    raise ExperimentUnknownError(experiment_id)
                if row["status"] != "PENDING":
                    raise ExperimentImmutabilityError(
                        f"experiment {experiment_id!r} is already finalized "
                        f"(status={row['status']}); metrics are immutable."
                    )
                conn.execute(
                    """UPDATE experiments
                          SET status = 'COMPLETED',
                              metrics = ?,
                              artifact_hash = ?,
                              manifest_sha256 = ?,
                              finalized_at = ?
                        WHERE experiment_id = ?""",
                    (
                        json.dumps(metrics, sort_keys=True),
                        artifact_hash,
                        manifest_sha256,
                        _now_utc(),
                        experiment_id,
                    ),
                )
        rec = self.get_experiment(experiment_id)
        assert rec is not None
        return rec

    def record_failure(self, experiment_id: str, error: str) -> ExperimentRecord:
        """Marks a PENDING experiment FAILED (once). Failed rows stay
        queryable and keep their provenance; they cannot be revived."""
        with self._lock, self._connect() as conn:
            with self._transaction(conn):
                row = conn.execute(
                    "SELECT * FROM experiments WHERE experiment_id = ?",
                    (experiment_id,),
                ).fetchone()
                if row is None:
                    raise ExperimentUnknownError(experiment_id)
                if row["status"] != "PENDING":
                    raise ExperimentImmutabilityError(
                        f"experiment {experiment_id!r} is already finalized"
                    )
                conn.execute(
                    """UPDATE experiments
                          SET status = 'FAILED',
                              metrics = ?,
                              finalized_at = ?
                        WHERE experiment_id = ?""",
                    (json.dumps({"error": error[:800]}, sort_keys=True), _now_utc(), experiment_id),
                )
        rec = self.get_experiment(experiment_id)
        assert rec is not None
        return rec

    # ------------------------------------------------------------------
    # immutable JSON manifest
    # ------------------------------------------------------------------
    def write_manifest(self, record: ExperimentRecord) -> Path:
        """Writes the immutable on-disk manifest for ONE experiment.

        Atomic (tmp + ``os.replace``). Second call for the same id raises
        :class:`ExperimentImmutableError`. The manifest embeds its own
        ``manifest_sha256`` (computed over the payload WITHOUT that field),
        so a later hand-edit is detectable by :meth:`verify_manifest`.
        """
        path = self._manifest_path(record.experiment_id)
        if path.exists():
            raise ExperimentImmutableError(
                f"manifest for {record.experiment_id!r} already exists — "
                "immutable artifacts are never rewritten; mint a NEW experiment_id."
            )
        payload: dict[str, Any] = dict(record.model_dump(by_alias=True, mode="json"))  # type: ignore[assignment]
        digest = sha256_text({k: v for k, v in payload.items() if k != "manifest_sha256"})
        payload["manifest_sha256"] = digest
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True, default=str)
        os.replace(tmp, path)
        return path

    def read_manifest(self, experiment_id: str) -> dict[str, Any] | None:
        path = self._manifest_path(experiment_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def verify_manifest(self, experiment_id: str) -> dict[str, Any]:
        """Detects tampering: recomputes the manifest hash and compares it
        to the stored value. Never raises; returns a verdict dict."""
        path = self._manifest_path(experiment_id)
        if not path.exists():
            return {
                "experiment_id": experiment_id,
                "verified": False,
                "reason": "MANIFEST_MISSING",
            }
        payload = json.loads(path.read_text(encoding="utf-8"))
        stored = payload.get("manifest_sha256", "")
        recomputed = sha256_text({k: v for k, v in payload.items() if k != "manifest_sha256"})
        return {
            "experiment_id": experiment_id,
            "verified": stored == recomputed and bool(stored),
            "stored_sha256": stored,
            "recomputed_sha256": recomputed,
            "reason": "OK" if stored == recomputed else "MANIFEST_TAMPERED",
        }

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def get_experiment(self, experiment_id: str) -> ExperimentRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
        return ExperimentRecord.from_db_row(row) if row is not None else None

    def list_experiments(
        self,
        *,
        status: str | None = None,
        dataset_hash: str | None = None,
        limit: int | None = None,
    ) -> list[ExperimentRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if dataset_hash is not None:
            clauses.append("dataset_hash = ?")
            params.append(dataset_hash)
        sql = "SELECT * FROM experiments"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at ASC, experiment_id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [ExperimentRecord.from_db_row(r) for r in rows]

    def get_best_experiment(
        self, metric: str = "val_loss", *, lower_is_better: bool | None = None
    ) -> ExperimentRecord | None:
        """Best COMPLETED experiment by ``metric``.

        Direction is derived from the metric name when not given (val_loss /
        *_loss / *_error / brier / ece descend; accuracy / f1 / *_f1 /
        sharpe / profit_factor ascend). Direction is REQUIRED — sorting a
        loss descending would silently surface the worst run.
        """
        direction = lower_is_better
        if direction is None:
            descending_metrics = ("loss", "error", "brier", "ece", "rmse", "mae")
            ascending_metrics = ("accuracy", "f1", "sharpe", "profit_factor", "precision", "recall")
            key = metric.lower()
            if any(key.endswith(m) or m in key for m in descending_metrics):
                direction = True
            elif any(m in key for m in ascending_metrics):
                direction = False
            else:
                raise ValueError(
                    f"cannot infer sort direction for metric {metric!r}; "
                    "pass lower_is_better explicitly"
                )
        order = "ASC" if direction else "DESC"
        # json_extract over the metrics blob; NULLs (metric absent) sort last.
        sql = f"""
            SELECT * FROM experiments
             WHERE status = 'COMPLETED'
               AND json_extract(metrics, ?) IS NOT NULL
          ORDER BY json_extract(metrics, ?) {order}, created_at ASC
             LIMIT 1
        """
        jpath = json_path(metric)
        with self._connect() as conn:
            row = conn.execute(sql, (jpath, jpath)).fetchone()
        return ExperimentRecord.from_db_row(row) if row is not None else None

    def top_n(
        self,
        metric: str,
        n: int = 10,
        *,
        lower_is_better: bool | None = None,
    ) -> list[ExperimentRecord]:
        """Top-n COMPLETED experiments by ``metric`` (same direction rule)."""
        if lower_is_better is None:
            descending_metrics = ("loss", "error", "brier", "ece", "rmse", "mae")
            ascending_metrics = ("accuracy", "f1", "sharpe", "profit_factor")
            key = metric.lower()
            if any(key.endswith(m) or m in key for m in descending_metrics):
                lower_is_better = True
            elif any(m in key for m in ascending_metrics):
                lower_is_better = False
            else:
                raise ValueError(
                    f"cannot infer sort direction for metric {metric!r}; "
                    "pass lower_is_better explicitly"
                )
        order = "ASC" if lower_is_better else "DESC"
        jpath = json_path(metric)
        sql = f"""
            SELECT * FROM experiments
             WHERE status = 'COMPLETED'
               AND json_extract(metrics, ?) IS NOT NULL
          ORDER BY json_extract(metrics, ?) {order}, created_at ASC
             LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (jpath, jpath, max(1, int(n)))).fetchall()
        return [ExperimentRecord.from_db_row(r) for r in rows]

    # ------------------------------------------------------------------
    # reproducibility
    # ------------------------------------------------------------------
    def reproduction_bundle(self, experiment_id: str) -> dict[str, Any]:
        """Everything needed to attempt an exact reproduction offline:
        git sha (+ dirty flag + working-tree hash), dataset hash, frozen
        model config, seed and the recorded metrics + artifact hash."""
        rec = self.get_experiment(experiment_id)
        if rec is None:
            raise ExperimentUnknownError(experiment_id)
        return {
            "experiment_id": rec.experiment_id,
            "git_sha": rec.git_sha,
            "git_dirty": rec.git_dirty,
            "working_tree_hash": rec.working_tree_hash,
            "dataset_id": rec.dataset_id,
            "dataset_hash": rec.dataset_hash,
            "model_config": rec.model_config_dict,
            "params": rec.params,
            "seed": rec.seed,
            "recorded_metrics": rec.metrics,
            "artifact_hash": rec.artifact_hash,
            "reproducible": (not rec.git_dirty) and rec.git_sha != "UNRESOLVED",
            "note": (
                "checkout git_sha + rebuild dataset until dataset_hash matches, then "
                "retrain with model_config/params/seed; the recorded metrics should "
                "reproduce up to non-determinism documented in the manifest."
            ),
        }

    def count(self, status: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM experiments"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        with self._connect() as conn:
            return int(conn.execute(sql, params).fetchone()[0])


__all__ = [
    "DB_FILENAME",
    "DEFAULT_ROOT",
    "ExperimentImmutabilityError",
    "ExperimentImmutableError",
    "ExperimentRecord",
    "ExperimentRegistry",
    "ExperimentUnknownError",
    "capture_git_revision",
    "json_path",
    "sha256_text",
    "validate_experiment_id",
]
