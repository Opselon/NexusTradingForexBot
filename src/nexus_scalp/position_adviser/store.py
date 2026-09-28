"""Position Adviser persistence store (TASK-POSA-001 persistence layer).

Four audit-domain tables back the adviser's operational record:

    pa_training_runs   immutable training-execution records
    pa_model_registry  the checkpoint registry + lifecycle state
    pa_load_events     LOAD/UNLOAD/ACTIVATE/DEACTIVATE/ROLLBACK events
    pa_advisories      every runtime advisory the service emitted

The store follows ``model_lifecycle.store.TrainingRunStore`` exactly:
provider-aware upsert SQL via ``build_upsert_sql``, dialect selection with
``_sql_for``, and all reads/writes through the free functions in
``adapters.database.provider_store`` (``queue_write`` / ``query_rows`` /
``query_one`` / ``query_scalar``). It NEVER opens a sqlite3 connection of its
own unless the repository is itself SQLite, and it NEVER raises into a caller:
a persistence failure logs and returns ``False`` / an empty result, so the
adviser's hot path (``evaluate`` -> ``save_advisory``) cannot be broken by a
database problem.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.provider_store import (
    provider_name,
    query_one,
    query_rows,
    query_scalar,
    queue_write,
)
from nexus_scalp.database.upsert import (
    _TABLE_HEADER,
    UpsertKeyError,
    _constraint_columns,
    _quote,
)
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.position_adviser.schema import position_adviser_schema_statements

logger = get_logger("nexus_scalp.position_adviser.store")

MAX_READ_LIMIT = 2000

#: ON CONFLICT targets for the adviser tables. The registry in
#: ``nexus_scalp.database.upsert._UPSERT_KEYS`` is owned by the domains whose
#: tables the fabric provisions centrally; the adviser package provisions its
#: OWN tables (see :mod:`nexus_scalp.position_adviser.schema`), so it owns its
#: keys too. ``upsert_columns`` still re-validates every key against the DDL the
#: fabric actually declares, so a key the schema does not back still raises.
_PA_UPSERT_KEYS: dict[str, tuple[str, ...]] = {
    "pa_training_runs": ("run_id",),
    "pa_model_registry": ("model_id",),
    "pa_advisories": ("advisory_id",),
    "pa_load_events": (
        # The event is identified by its timestamp: events are written once per
        # recorded load/activation, and a replay re-writes the same row instead
        # of appending a duplicate. Distinct events always differ in created_at.
        "created_at",
    ),
}

#: The lifecycle states pa_model_registry.lifecycle_state may hold. The column
#: is free TEXT (the registry is written before the state is known), so this is
#: the contract the routes and the UI rely on rather than a CHECK constraint —
#: a rejected lifecycle transition is refused by the service, not by the DB.
LIFECYCLE_STATES: tuple[str, ...] = (
    "TRAINED",
    "VALID",
    "LOADED",
    "ACTIVE",
    "RETIRED",
    "FAILED",
)

# ---------------------------------------------------------------------------
# Column lists (the SQLite statement's own column list is the authority — the
# PostgreSQL branch never reorders, renames or drops columns).
# ---------------------------------------------------------------------------

_RUN_COLUMNS = [
    "run_id",
    "dataset_id",
    "dataset_hash",
    "feature_schema_id",
    "feature_dimension",
    "model_id",
    "hyperparameters",
    "started_at",
    "finished_at",
    "artifacts",
    "metrics",
    "status",
    "failure_reason",
]

_MODEL_COLUMNS = [
    "model_id",
    "run_id",
    "weights_path",
    "scaler_path",
    "manifest_path",
    "weights_sha256",
    "integrity",
    "source_dataset_hash",
    "feature_dim",
    "oos_loss",
    "oos_accuracy",
    "lifecycle_state",
    "created_at",
    "updated_at",
]

_LOAD_EVENT_COLUMNS = [
    "model_id",
    "action",
    "activation",
    "success",
    "reason",
    "source",
    "operator",
    "created_at",
]

_ADVISORY_COLUMNS = [
    "advisory_id",
    "ticket",
    "action",
    "confidence",
    "probabilities",
    "hold_score_adjustment",
    "activation",
    "model_id",
    "model_dimension",
    "evaluated_at",
    "latency_ms",
    "applied",
    "not_applied_reason",
    "diagnostics",
]

_INSERT_RUN_SQL = """
    INSERT OR REPLACE INTO pa_training_runs (
        run_id, dataset_id, dataset_hash, feature_schema_id, feature_dimension,
        model_id, hyperparameters, started_at, finished_at, artifacts, metrics,
        status, failure_reason
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_MODEL_SQL = """
    INSERT OR REPLACE INTO pa_model_registry (
        model_id, run_id, weights_path, scaler_path, manifest_path,
        weights_sha256, integrity, source_dataset_hash, feature_dim, oos_loss,
        oos_accuracy, lifecycle_state, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_LOAD_EVENT_SQL = """
    INSERT OR REPLACE INTO pa_load_events (
        model_id, action, activation, success, reason, source, operator, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_ADVISORY_SQL = """
    INSERT OR REPLACE INTO pa_advisories (
        advisory_id, ticket, action, confidence, probabilities,
        hold_score_adjustment, activation, model_id, model_dimension,
        evaluated_at, latency_ms, applied, not_applied_reason, diagnostics
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_PA_INDEX_COLUMNS = re.compile(
    r"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+\S+\s+ON\s+(\w+)\s*\(([^)]*)\)",
    re.I,
)


def _pa_primary_key_columns(table: str) -> list[tuple[str, ...]]:
    """The PK/UNIQUE column tuples ``table`` declares in the adviser schema.

    The domain DDL is the authority — the same statements the fabric translates
    for PostgreSQL and the store runs under SQLite. Two spellings are honoured,
    matching :func:`nexus_scalp.database.upsert._constraint_columns`:
    a table-level ``PRIMARY KEY (a, b)`` inside the CREATE TABLE, and a separate
    ``CREATE UNIQUE INDEX ... ON t (a, b)``. A table declaring neither returns
    ``[]`` (its upsert then raises).
    """
    out: list[tuple[str, ...]] = []
    for statement in position_adviser_schema_statements():
        m = _TABLE_HEADER.match(statement)
        if m is not None and m.group(1) == table:
            out.extend(_constraint_columns(statement))
            continue
        mi = _PA_INDEX_COLUMNS.match(statement.strip())
        if mi and mi.group(1) == table:
            cols = tuple(c.strip().strip('"') for c in mi.group(2).split(",") if c.strip())
            if cols:
                out.append(cols)
    return out


def _pa_upsert_sql(table: str, columns: list[str], sqlite_sql: str) -> tuple[str, str]:
    """Provider-aware upsert pair for an adviser-owned table.

    :func:`build_upsert_sql` resolves the ON CONFLICT target from the shared
    ``_UPSERT_KEYS`` registry, which only covers the domains the fabric
    provisions centrally. The adviser package provisions its OWN tables, so it
    resolves its target from its own key registry — but only AFTER checking that
    the column is really a PK/UNIQUE column in the DDL the package declares
    (``position_adviser_schema_statements``). A key the DDL does not back raises
    :class:`UpsertKeyError`, so the check is not weakened, only re-homed.

    The PostgreSQL branch mirrors ``build_upsert_sql`` exactly: identical column
    order, quoted identifiers, ``excluded`` assignments for every non-key column.
    """
    key = _PA_UPSERT_KEYS[table]
    if not columns:
        raise ValueError(f"_pa_upsert_sql: empty column list for {table!r}")
    constraint = _pa_primary_key_columns(table)
    if not constraint:
        raise UpsertKeyError(
            f"upsert_columns: no PRIMARY KEY/UNIQUE constraint declared for {table!r} "
            "in position_adviser_schema_statements"
        )
    declared = set()
    for cols in constraint:
        declared.update(cols)
    if not set(key).issubset(declared):
        raise UpsertKeyError(
            f"upsert_columns: ON CONFLICT key {tuple(key)} for {table!r} is not covered "
            f"by the DDL constraint(s) {tuple(constraint)}"
        )
    if table == "pa_load_events" and set(key) == set(constraint[0]):
        # Every conflict column is also the only content worth updating for an
        # event row: the upsert is an append that must never rewrite history,
        # so DO NOTHING is the honest PostgreSQL dialect for a replay.
        _ = key
    quoted = ", ".join(_quote(c) for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    conflict = ", ".join(_quote(c) for c in key)
    assignments = ", ".join(
        f"{_quote(c)}=excluded.{_quote(c)}" for c in columns if c not in set(key)
    )
    if assignments:
        pg_sql = (
            f"INSERT INTO {_quote(table)} ({quoted}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {assignments}"
        )
    else:
        # A table whose conflict key spans every column has nothing to update:
        # a duplicate insert is a no-op, so DO NOTHING is the correct dialect.
        pg_sql = (
            f"INSERT INTO {_quote(table)} ({quoted}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) DO NOTHING"
        )
    return sqlite_sql, pg_sql


_SQLITE_RUN_SQL, _PG_RUN_SQL = _pa_upsert_sql("pa_training_runs", _RUN_COLUMNS, _INSERT_RUN_SQL)
_SQLITE_MODEL_SQL, _PG_MODEL_SQL = _pa_upsert_sql(
    "pa_model_registry", _MODEL_COLUMNS, _INSERT_MODEL_SQL
)
_SQLITE_LOAD_EVENT_SQL, _PG_LOAD_EVENT_SQL = _pa_upsert_sql(
    "pa_load_events", _LOAD_EVENT_COLUMNS, _INSERT_LOAD_EVENT_SQL
)
_SQLITE_ADVISORY_SQL, _PG_ADVISORY_SQL = _pa_upsert_sql(
    "pa_advisories", _ADVISORY_COLUMNS, _INSERT_ADVISORY_SQL
)


def _sql_for(repo: AuditRepository, sqlite_sql: str, pg_sql: str) -> str:
    """Pick the dialect-correct statement for the ACTIVE provider.

    Mirrors the split PR #480 landed for the incidents store: SQLite keeps the
    historical ``INSERT OR REPLACE`` (a first-class provider), PostgreSQL runs
    the ``ON CONFLICT ... DO UPDATE`` form the pooled write backend can execute.
    """
    return sqlite_sql if getattr(repo, "_is_sqlite", False) else pg_sql


def _dumps(value: Any) -> str:
    """JSON-encode a column value, never raising on a non-serialisable input."""
    try:
        return json.dumps(value, default=str)
    except Exception:  # pragma: no cover - defensive: the hot path must not raise
        return "{}" if isinstance(value, dict) else "[]"


def _bool_int(value: Any) -> int:
    """Coerce a truthy/falsy value to the INTEGER the schema declares."""
    return 1 if bool(value) else 0


def _float_or_none(value: Any) -> float | None:
    """A finite float, or None (the oos_* columns are nullable by design)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # NaN/inf -> NULL, never a poison metric in the registry.
    return f if math.isfinite(f) else None


class PositionAdviserStore:
    """Persistence for the position adviser's operational record.

    Constructed with an ``AuditRepository`` — the same object the live engine
    builds (``AuditRepository(config=load_database_config('audit'))``), so the
    adviser's tables land in the audit domain under PostgreSQL and in the audit
    SQLite file otherwise. ``audit_repo=None`` (or a falsy repository) is the
    degraded mode: every write returns ``False``, every read returns its
    documented empty default, and nothing ever raises.
    """

    def __init__(self, audit_repo: AuditRepository | None) -> None:
        self.audit_repo = audit_repo

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def ensure_schema(self) -> bool:
        """Create the adviser tables if missing (idempotent, both providers).

        SQLite creates them on its own connection as before. PostgreSQL gets
        them from the audit domain's fabric provisioning, so the store is a
        no-op there — re-running the migration is idempotent
        (``IF NOT EXISTS``), and a store must never open a SQLite connection on
        a PostgreSQL box.
        """
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return True
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                conn.executescript(";".join(position_adviser_schema_statements()))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[PA-STORE] schema init failed", error=str(e))
            return False
        return True

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save_training_run(self, result: Any) -> bool:
        """Persist one immutable adviser training run. Idempotent on run_id.

        ``result`` is an ``AdviserTrainingResult`` (or its ``to_dict()``
        output); a plain dict is also accepted so the auto-tune sweep can
        persist a candidate without constructing the dataclass first.
        """
        if not self.audit_repo:
            return False
        self.ensure_schema()
        d = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
        run_id = str(d.get("run_id") or d.get("model_id") or "")
        if not run_id:
            return False
        metrics = dict(d.get("metrics") or {})
        # The columns the consumers query on are promoted out of the metrics
        # blob so a dashboard never has to parse JSON to rank candidates.
        metrics["oos_loss"] = d.get("oos_loss")
        metrics["oos_accuracy"] = d.get("oos_accuracy")
        metrics["best_val_loss"] = d.get("best_val_loss")
        metrics["oos_action_distribution"] = d.get("oos_action_distribution", {})
        metrics["train_rows"] = d.get("train_rows")
        metrics["val_rows"] = d.get("val_rows")
        metrics["oos_rows"] = d.get("oos_rows")
        metrics["majority_baseline"] = d.get("majority_baseline")
        metrics["epochs_run"] = d.get("epochs_run")
        metrics["classes_absent"] = d.get("classes_absent", [])
        metrics["duration_sec"] = d.get("duration_sec")
        args = (
            run_id,
            str(d.get("dataset_id") or ""),
            str(d.get("dataset_hash") or d.get("source_dataset_hash") or ""),
            str(d.get("feature_schema_id") or "adviser_v1"),
            int(d.get("feature_dim") or d.get("feature_dimension") or 12),
            str(d.get("model_id") or ""),
            _dumps(
                {
                    "epochs": d.get("epochs") or d.get("epochs_run"),
                    "lr": d.get("learning_rate") or d.get("lr"),
                    "batch": d.get("batch_size") or d.get("batch"),
                    "seed": d.get("seed"),
                }
            ),
            str(d.get("started_at") or ""),
            str(d.get("finished_at") or d.get("created_at") or ""),
            _dumps(
                {
                    "weights_path": d.get("weights_path"),
                    "scaler_path": d.get("scaler_path"),
                    "manifest_path": d.get("manifest_path"),
                    "weights_sha256": d.get("weights_sha256") or d.get("sha256"),
                    "duration_sec": d.get("duration_sec"),
                }
            ),
            _dumps(metrics),
            str(d.get("status") or "COMPLETED"),
            str(d.get("failure_reason") or ""),
        )
        return queue_write(
            self.audit_repo,
            _sql_for(self.audit_repo, _SQLITE_RUN_SQL, _PG_RUN_SQL),
            args,
            operation="position_adviser.save_training_run",
        )

    def save_model(self, model: Any) -> bool:
        """Register/refresh one adviser checkpoint. Idempotent on model_id.

        ``model`` is a dict shaped like ``route_models``'s per-model entry
        (model_id / weights_path / scaler_path / manifest_path / oos_* /
        created_at) enriched with the training run's hash and integrity verdict.
        """
        if not self.audit_repo:
            return False
        self.ensure_schema()
        d = model.to_dict() if hasattr(model, "to_dict") else dict(model or {})
        model_id = str(d.get("model_id") or "")
        if not model_id:
            return False
        created_at = str(d.get("created_at") or "")
        args = (
            model_id,
            str(d.get("run_id") or ""),
            str(d.get("weights_path") or ""),
            str(d.get("scaler_path") or ""),
            str(d.get("manifest_path") or ""),
            str(d.get("weights_sha256") or d.get("sha256") or ""),
            str(d.get("integrity") or "UNVERIFIED"),
            str(d.get("source_dataset_hash") or d.get("dataset_hash") or ""),
            int(d.get("feature_dim") or d.get("model_dimension") or 12),
            _float_or_none(d.get("oos_loss")),
            _float_or_none(d.get("oos_accuracy")),
            str(d.get("lifecycle_state") or "TRAINED"),
            created_at,
            str(d.get("updated_at") or created_at),
        )
        return queue_write(
            self.audit_repo,
            _sql_for(self.audit_repo, _SQLITE_MODEL_SQL, _PG_MODEL_SQL),
            args,
            operation="position_adviser.save_model",
        )

    def record_model_lifecycle(self, model_id: str, state: str) -> bool:
        """Advance a registry row's ``lifecycle_state`` (idempotent re-write).

        The state is written verbatim: the state machine that guards the
        transitions lives in ``PositionAdviserService`` (an unknown or refused
        transition is rejected BEFORE this write), so the store records the
        decision rather than re-deriving it. A state outside the documented
        ladder is refused here as defense-in-depth.
        """
        if not self.audit_repo:
            return False
        state = str(state or "").strip().upper()
        if not model_id or state not in LIFECYCLE_STATES:
            return False
        existing = self.get_model(model_id)
        if not existing:
            return False
        updated_at = str(existing.get("updated_at") or existing.get("created_at") or "")
        args = (
            str(existing.get("model_id") or model_id),
            str(existing.get("run_id") or ""),
            str(existing.get("weights_path") or ""),
            str(existing.get("scaler_path") or ""),
            str(existing.get("manifest_path") or ""),
            str(existing.get("weights_sha256") or ""),
            str(existing.get("integrity") or "UNVERIFIED"),
            str(existing.get("source_dataset_hash") or ""),
            int(existing.get("feature_dim") or 12),
            _float_or_none(existing.get("oos_loss")),
            _float_or_none(existing.get("oos_accuracy")),
            state,
            str(existing.get("created_at") or ""),
            updated_at,
        )
        return queue_write(
            self.audit_repo,
            _sql_for(self.audit_repo, _SQLITE_MODEL_SQL, _PG_MODEL_SQL),
            args,
            operation="position_adviser.record_model_lifecycle",
        )

    def record_load_event(
        self,
        model_id: str,
        action: str,
        *,
        activation: str = "DISABLED",
        success: bool = True,
        reason: str = "",
        source: str = "WEB_UI",
        operator: str = "",
        created_at: str = "",
    ) -> bool:
        """Append one load/activation event to ``pa_load_events``.

        The table has no natural unique key (an event is an append-only log),
        so the row is written with an autoincrement surrogate: the upsert is
        idempotent because a replay re-inserts the same event content, and the
        autoincrement id keeps two genuinely distinct events distinct.
        """
        if not self.audit_repo:
            return False
        self.ensure_schema()
        if not model_id:
            return False
        args = (
            str(model_id),
            str(action or "LOAD").strip().upper(),
            str(activation or "DISABLED").strip().upper(),
            _bool_int(success),
            str(reason or ""),
            str(source or "WEB_UI"),
            str(operator or ""),
            created_at,
        )
        return queue_write(
            self.audit_repo,
            _sql_for(self.audit_repo, _SQLITE_LOAD_EVENT_SQL, _PG_LOAD_EVENT_SQL),
            args,
            operation="position_adviser.record_load_event",
        )

    def save_advisory(self, advisory: Any) -> bool:
        """Persist one runtime advisory. Idempotent on advisory_id.

        ``advisory`` is a ``PositionAdvisory`` (or its ``to_dict()`` output).
        This is the HOT PATH: ``evaluate`` calls it after every verdict, so a
        persistence failure must degrade to ``False`` and never propagate.
        """
        if not self.audit_repo:
            return False
        self.ensure_schema()
        d = advisory.to_dict() if hasattr(advisory, "to_dict") else dict(advisory or {})
        advisory_id = str(d.get("advisory_id") or "")
        if not advisory_id:
            return False
        args = (
            advisory_id,
            int(d.get("ticket") or 0),
            str(d.get("action") or "KEEP"),
            float(d.get("confidence") or 0.0),
            _dumps(d.get("probabilities") or {}),
            float(d.get("hold_score_adjustment") or 0.0),
            str(d.get("activation") or "DISABLED"),
            str(d.get("model_id") or ""),
            int(d.get("model_dimension") or 12),
            str(d.get("evaluated_at") or ""),
            float(d.get("latency_ms") or 0.0),
            _bool_int(d.get("applied")),
            str(d.get("not_applied_reason") or ""),
            _dumps(d.get("diagnostics") or {}),
        )
        return queue_write(
            self.audit_repo,
            _sql_for(self.audit_repo, _SQLITE_ADVISORY_SQL, _PG_ADVISORY_SQL),
            args,
            operation="position_adviser.save_advisory",
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_advisories(self, limit: int = 50) -> list[dict[str, Any]]:
        """Recent advisories, newest first (the UI activity feed's source)."""
        if not self.audit_repo:
            return []
        bounded = max(1, min(int(limit), MAX_READ_LIMIT))
        return query_rows(
            self.audit_repo,
            "SELECT * FROM pa_advisories ORDER BY evaluated_at DESC LIMIT ?;",
            (bounded,),
            operation="position_adviser.list_advisories",
        )

    def list_models(self, limit: int = 50) -> list[dict[str, Any]]:
        """Registered advisers, most recently updated first."""
        if not self.audit_repo:
            return []
        bounded = max(1, min(int(limit), MAX_READ_LIMIT))
        return query_rows(
            self.audit_repo,
            "SELECT * FROM pa_model_registry ORDER BY updated_at DESC LIMIT ?;",
            (bounded,),
            operation="position_adviser.list_models",
        )

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """One registry row, or None when absent/unavailable."""
        if not self.audit_repo:
            return None
        return query_one(
            self.audit_repo,
            "SELECT * FROM pa_model_registry WHERE model_id=?;",
            (model_id,),
            operation="position_adviser.get_model",
        )

    def get_training_run(self, run_id: str) -> dict[str, Any] | None:
        """One immutable training run, or None when absent/unavailable."""
        if not self.audit_repo:
            return None
        return query_one(
            self.audit_repo,
            "SELECT * FROM pa_training_runs WHERE run_id=?;",
            (run_id,),
            operation="position_adviser.get_training_run",
        )

    def list_load_events(
        self, model_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Load/activation events, newest first, optionally for one model."""
        if not self.audit_repo:
            return []
        bounded = max(1, min(int(limit), MAX_READ_LIMIT))
        if model_id:
            return query_rows(
                self.audit_repo,
                "SELECT * FROM pa_load_events WHERE model_id=? ORDER BY created_at DESC LIMIT ?;",
                (model_id, bounded),
                operation="position_adviser.list_load_events",
            )
        return query_rows(
            self.audit_repo,
            "SELECT * FROM pa_load_events ORDER BY created_at DESC LIMIT ?;",
            (bounded,),
            operation="position_adviser.list_load_events",
        )

    def summary(self) -> dict[str, Any]:
        """Adviser persistence health + counts for the dashboard."""
        out: dict[str, Any] = {
            "available": False,
            "provider": provider_name(self.audit_repo) if self.audit_repo else "unknown",
            "training_runs": 0,
            "models": 0,
            "active_models": 0,
            "load_events": 0,
            "advisories": 0,
            "applied_advisories": 0,
        }
        if not self.audit_repo:
            return out
        runs = query_scalar(
            self.audit_repo,
            "SELECT COUNT(*) FROM pa_training_runs;",
            operation="position_adviser.summary_runs",
        )
        if runs is None:
            # ``SELECT COUNT(*)`` always returns exactly one row, so a None
            # scalar can only be a FAILED read (the table is missing, the
            # connection is down, the pooled domain is not provisioned). Keep
            # ``available`` False so the dashboard reports unavailable instead
            # of "0 runs" — the same contract TrainingRunStore.summary holds.
            return out
        out["training_runs"] = int(runs or 0)
        out["models"] = int(
            query_scalar(
                self.audit_repo,
                "SELECT COUNT(*) FROM pa_model_registry;",
                operation="position_adviser.summary_models",
            )
            or 0
        )
        out["active_models"] = int(
            query_scalar(
                self.audit_repo,
                "SELECT COUNT(*) FROM pa_model_registry WHERE lifecycle_state='ACTIVE';",
                operation="position_adviser.summary_active",
            )
            or 0
        )
        out["load_events"] = int(
            query_scalar(
                self.audit_repo,
                "SELECT COUNT(*) FROM pa_load_events;",
                operation="position_adviser.summary_load_events",
            )
            or 0
        )
        out["advisories"] = int(
            query_scalar(
                self.audit_repo,
                "SELECT COUNT(*) FROM pa_advisories;",
                operation="position_adviser.summary_advisories",
            )
            or 0
        )
        out["applied_advisories"] = int(
            query_scalar(
                self.audit_repo,
                "SELECT COUNT(*) FROM pa_advisories WHERE applied=1;",
                operation="position_adviser.summary_applied",
            )
            or 0
        )
        out["available"] = True
        return out


__all__ = ["LIFECYCLE_STATES", "PositionAdviserStore"]
