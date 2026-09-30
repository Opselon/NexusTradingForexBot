"""
Shadow Persistence Store
========================
PHASE 11 append-oriented, auditable persistence (spec 20 / 24 / 25).

Tables:
    shadow_runs       one bounded shadow evaluation run
    shadow_decisions  one parallel Champion/Challenger decision per row
    shadow_comparisons aggregated multi-dimension comparison snapshots
    shadow_promotions promotion evaluations (eligibility + veto history)

Historical shadow results are NEVER overwritten: rows are append-only keyed by
run_id / decision_id. Model rebuilds and feature-schema evolution do NOT erase
shadow history (spec 25): every row preserves model version + schema identity.

Writes go through the AuditRepository background queue so the live path is
never blocked.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.provider_store import (
    OPS_SHADOW_DOMAIN,
    ops_ensure_schema,
    ops_query_rows,
    ops_query_scalar,
    ops_queue_write,
)
from nexus_scalp.database.upsert import build_upsert_sql
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.models import (
    PromotionEvaluation,
    ShadowComparison,
    ShadowDecisionRecord,
    ShadowRun,
)
from nexus_scalp.shadow.outcomes import STATUS_RESOLVED

logger = get_logger("nexus_scalp.shadow.store")

MAX_READ_LIMIT = 3000

_INSERT_RUN_SQL = """
    INSERT OR REPLACE INTO shadow_runs (
        run_id, champion_model_id, champion_version, challenger_model_id,
        challenger_version, status, started_at, finished_at, decision_count, error,
        git_revision, configuration_version, challenger_artifact_hash, champion_artifact_hash
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_DECISION_SQL = """
    INSERT OR REPLACE INTO shadow_decisions (
        shadow_decision_id, run_id, decision_id, timestamp, symbol, timeframe,
        champion_model_id, champion_version, challenger_model_id, challenger_version,
        feature_schema_id, feature_dimension, feature_hash, regime, session,
        champion_action, champion_confidence, challenger_action, challenger_confidence,
        action_agreement, valid_comparison, invalid_reason,
        hypothetical_pnl_usd, hypothetical_r, mfe_r, mae_r, holding_duration_sec,
        exit_reason, simulated,
        champion_entry, champion_sl, champion_tp, shadow_entry, shadow_sl, shadow_tp,
        spread_usd, shadow_r, shadow_mfe_r, shadow_mae_r, shadow_pnl_usd,
        shadow_holding_sec, shadow_exit_reason, delta_r, outcome_status,
        champion_probabilities, challenger_probabilities, champion_strategy_id,
        challenger_strategy_id, hypothetical_risk_pct, hypothetical_volume,
        hypothetical_entry, hypothetical_exit
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_COMPARISON_SQL = """
    INSERT OR REPLACE INTO shadow_comparisons (
        run_id, champion_model_id, champion_version, challenger_model_id,
        challenger_version, sample_count, valid_comparisons, invalid_comparisons,
        action_agreement_rate, champion_expectancy_r, challenger_expectancy_r,
        champion_drawdown_r, challenger_drawdown_r, evidence_status,
        samples_required, samples_observed, by_regime, by_strategy,
        best_regimes, worst_regimes, degraded_regimes, degraded_strategies,
        evaluated_at, payload
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_PROMOTION_SQL = """
    INSERT OR REPLACE INTO shadow_promotions (
        run_id, candidate_model_id, candidate_version, champion_model_id,
        champion_version, final_score, eligible, vetoes, reasons, evaluated_at,
        payload
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

#: ML-OBS-001: bounded UPDATE of a shadow_decisions row's resolved outcome
#: fields. The row was written by save_decision with outcome_status='PENDING';
#: the certified tick resolver fills the SAME columns afterwards. Scanned by
#: name (never SELECT *: the additive migration makes the column set
#: version-dependent across legacy databases).
#:
#: NOTE: the resolved ENTRY/EXIT prices (apply_to_record_fields returns
#: hypothetical_entry / hypothetical_exit) previously had NO DB columns, so the
#: resolver computed them and dropped them. DEEP-OPT L1 gave them real columns
#: (part of the minimal representation that replaced the JSON mirror), so the
#: realized-R contract is now complete: R + geometry + the resolved price pair.
_UPDATE_OUTCOME_SQL = """
    UPDATE shadow_decisions SET
        hypothetical_pnl_usd = ?,
        hypothetical_r = ?,
        mfe_r = ?,
        mae_r = ?,
        holding_duration_sec = ?,
        exit_reason = ?,
        shadow_r = ?,
        shadow_mfe_r = ?,
        shadow_mae_r = ?,
        shadow_pnl_usd = ?,
        shadow_holding_sec = ?,
        shadow_exit_reason = ?,
        delta_r = ?,
        outcome_status = ?,
        hypothetical_entry = ?,
        hypothetical_exit = ?
    WHERE shadow_decision_id = ? AND outcome_status = 'PENDING';
"""


#: Lane B (PG upsert parity, PR #480 pattern): each SQLite statement stays
#: byte-identical (SQLite remains a first-class provider — INSERT OR REPLACE
#: is untouched and the SQLite write route is unchanged). The PostgreSQL branch
#: gets ``INSERT INTO ... ON CONFLICT (...) DO UPDATE SET``, because
#: ``INSERT OR REPLACE`` is SQLite-only syntax and the pooled write backend
#: hands the statement to the server verbatim apart from the ``?``->``%s``
#: placeholder translation — so it lands as ``syntax error at or near "OR"``.
#:
#: The ON CONFLICT target is resolved and re-validated against the table's real
#: DDL by ``build_upsert_sql`` (nexus_scalp/database/upsert.py): a target with
#: no covering UNIQUE/PRIMARY KEY constraint would be invalid under PostgreSQL,
#: and the helper raises instead of emitting it.
_RUN_COLUMNS = [
    "run_id",
    "champion_model_id",
    "champion_version",
    "challenger_model_id",
    "challenger_version",
    "status",
    "started_at",
    "finished_at",
    "decision_count",
    "error",
    "git_revision",
    "configuration_version",
    "challenger_artifact_hash",
    "champion_artifact_hash",
]
_DECISION_COLUMNS = [
    "shadow_decision_id",
    "run_id",
    "decision_id",
    "timestamp",
    "symbol",
    "timeframe",
    "champion_model_id",
    "champion_version",
    "challenger_model_id",
    "challenger_version",
    "feature_schema_id",
    "feature_dimension",
    "feature_hash",
    "regime",
    "session",
    "champion_action",
    "champion_confidence",
    "challenger_action",
    "challenger_confidence",
    "action_agreement",
    "valid_comparison",
    "invalid_reason",
    "hypothetical_pnl_usd",
    "hypothetical_r",
    "mfe_r",
    "mae_r",
    "holding_duration_sec",
    "exit_reason",
    "simulated",
    "champion_entry",
    "champion_sl",
    "champion_tp",
    "shadow_entry",
    "shadow_sl",
    "shadow_tp",
    "spread_usd",
    "shadow_r",
    "shadow_mfe_r",
    "shadow_mae_r",
    "shadow_pnl_usd",
    "shadow_holding_sec",
    "shadow_exit_reason",
    "delta_r",
    "outcome_status",
    # Minimal durable representation (DEEP-OPT L1, Part 5 rule 8).
    # The whole-record JSON mirror was removed: 46.6% of it duplicated these
    # columns byte-for-byte, and the remainder is reconstructed below from the
    # fields these five columns plus the run row carry. Probabilities are the
    # only model output with no column, so they earn two small TEXT vectors.
    "champion_probabilities",
    "challenger_probabilities",
    "champion_strategy_id",
    "challenger_strategy_id",
    "hypothetical_risk_pct",
    "hypothetical_volume",
    "hypothetical_entry",
    "hypothetical_exit",
]
_SHADOW_COMPARISON_COLUMNS = [
    "run_id",
    "champion_model_id",
    "champion_version",
    "challenger_model_id",
    "challenger_version",
    "sample_count",
    "valid_comparisons",
    "invalid_comparisons",
    "action_agreement_rate",
    "champion_expectancy_r",
    "challenger_expectancy_r",
    "champion_drawdown_r",
    "challenger_drawdown_r",
    "evidence_status",
    "samples_required",
    "samples_observed",
    "by_regime",
    "by_strategy",
    "best_regimes",
    "worst_regimes",
    "degraded_regimes",
    "degraded_strategies",
    "evaluated_at",
    "payload",
]
_PROMOTION_COLUMNS = [
    "run_id",
    "candidate_model_id",
    "candidate_version",
    "champion_model_id",
    "champion_version",
    "final_score",
    "eligible",
    "vetoes",
    "reasons",
    "evaluated_at",
    "payload",
]

_SQLITE_RUN_SQL, _PG_RUN_SQL = build_upsert_sql(
    "shadow_runs", _RUN_COLUMNS, sqlite_sql=_INSERT_RUN_SQL
)
_SQLITE_DECISION_SQL, _PG_DECISION_SQL = build_upsert_sql(
    "shadow_decisions", _DECISION_COLUMNS, sqlite_sql=_INSERT_DECISION_SQL
)
_SQLITE_COMPARISON_SQL, _PG_COMPARISON_SQL = build_upsert_sql(
    "shadow_comparisons", _SHADOW_COMPARISON_COLUMNS, sqlite_sql=_INSERT_COMPARISON_SQL
)
_SQLITE_PROMOTION_SQL, _PG_PROMOTION_SQL = build_upsert_sql(
    "shadow_promotions", _PROMOTION_COLUMNS, sqlite_sql=_INSERT_PROMOTION_SQL
)


def _sql_for(repo: AuditRepository, sqlite_sql: str, pg_sql: str) -> str:
    """Pick the dialect-correct statement for the ACTIVE provider.

    Mirrors the split PR #480 landed for the incidents store: SQLite keeps the
    historical ``INSERT OR REPLACE`` (a first-class provider — its statement is
    untouched), PostgreSQL runs the ``ON CONFLICT ... DO UPDATE`` form the
    pooled write backend can execute.
    """
    return sqlite_sql if getattr(repo, "_is_sqlite", False) else pg_sql


def _compact_vector(values: Any) -> str:
    """Compact JSON vector for a probability list.

    ``json.dumps`` default separators insert a space after every comma; a
    3-class probability vector is written once per decision, so the separators
    are worth tightening. Non-numeric entries are dropped rather than
    serialized (they would be unreadable to every consumer anyway).
    """
    try:
        seq = [float(v) for v in (values or [])]
    except (TypeError, ValueError):
        seq = []
    return json.dumps(seq, separators=(",", ":")) if seq else "[]"


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def compact_database(db_path: str) -> dict[str, Any]:
    """Reclaims the space a retired column left behind (explicit, one-shot).

    SQLite keeps freed pages on its freelist, so migrating 108 MB of mirror out
    of ``shadow_decisions`` shrinks the FILE only after a ``VACUUM``. That is a
    full-file rewrite, so it never runs automatically at boot — the operator (or
    a scheduled maintenance job) calls it when convenient.

    Idempotent and safe to re-run: it reports the same sizes when there is
    nothing to reclaim.
    """
    if not os.path.exists(db_path):
        return {"ok": False, "error": "database not found", "path": db_path}
    before = os.path.getsize(db_path)
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        reclaimable = ShadowStore.pending_mirror_bytes(conn)
        conn.execute("VACUUM;")
        conn.commit()
        after = os.path.getsize(db_path)
        return {
            "ok": True,
            "path": db_path,
            "bytes_before": before,
            "bytes_after": after,
            "bytes_reclaimed": max(0, before - after),
            "retired_mirror_bytes": reclaimable,
        }
    finally:
        conn.close()


class ShadowStore:
    """Append-only persistence for shadow evaluation."""

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo
        # Schema is created once per process; repeating the DDL on every live
        # tick (save_decision -> ensure_schema) is synchronous SQLite I/O on
        # the hot path. A miss re-checks; a hit skips all DDL entirely.
        self._schema_ensured: bool = False
        self._additive_ensured: bool = False

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Creates the Phase 11 shadow tables if missing (idempotent).

        Guarded by an in-process flag: the DDL runs at most once. Live-path
        callers (ShadowEngine.record_shadow_decision -> save_decision) invoke
        this on every tick, so a per-tick sqlite3.connect + CREATE would be
        blocking I/O on the hot path.

        CHG-0046 (SHADOW_EVIDENCE v2): additive column migration for the
        paired-outcome + run-freeze fields. Legacy rows keep their values;
        new columns are NULL/'' (= NOT_RECORDED) — historical evidence is
        never rewritten, only extended.
        """
        if self._schema_ensured and self._additive_ensured:
            return
        if not self.audit_repo:
            return
        if self.audit_repo._is_sqlite:
            try:
                conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
                try:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS shadow_runs (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            run_id TEXT UNIQUE NOT NULL,
                            champion_model_id TEXT NOT NULL,
                            champion_version TEXT NOT NULL,
                            challenger_model_id TEXT NOT NULL,
                            challenger_version TEXT NOT NULL,
                            status TEXT NOT NULL,
                            started_at TEXT NOT NULL,
                            finished_at TEXT DEFAULT '',
                            decision_count INTEGER DEFAULT 0,
                            error TEXT DEFAULT ''
                        );
                        """
                    )
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS shadow_decisions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            shadow_decision_id TEXT UNIQUE NOT NULL,
                            run_id TEXT NOT NULL,
                            decision_id TEXT DEFAULT '',
                            timestamp TEXT NOT NULL,
                            symbol TEXT NOT NULL,
                            timeframe TEXT DEFAULT '',
                            champion_model_id TEXT NOT NULL,
                            champion_version TEXT NOT NULL,
                            challenger_model_id TEXT NOT NULL,
                            challenger_version TEXT NOT NULL,
                            feature_schema_id TEXT DEFAULT 'scalp_v1',
                            feature_dimension INTEGER DEFAULT 50,
                            feature_hash TEXT DEFAULT '',
                            regime TEXT DEFAULT '',
                            session TEXT DEFAULT '',
                            champion_action TEXT DEFAULT '',
                            champion_confidence REAL DEFAULT 0.0,
                            challenger_action TEXT DEFAULT '',
                            challenger_confidence REAL DEFAULT 0.0,
                            action_agreement INTEGER DEFAULT 0,
                            valid_comparison INTEGER DEFAULT 1,
                            invalid_reason TEXT DEFAULT '',
                            hypothetical_pnl_usd REAL DEFAULT 0.0,
                            hypothetical_r REAL DEFAULT 0.0,
                            mfe_r REAL DEFAULT 0.0,
                            mae_r REAL DEFAULT 0.0,
                            holding_duration_sec REAL DEFAULT 0.0,
                            exit_reason TEXT DEFAULT '',
                            simulated INTEGER DEFAULT 1,
                            payload TEXT DEFAULT '{}'
                        );
                        """
                    )
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS shadow_comparisons (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            run_id TEXT UNIQUE NOT NULL,
                            champion_model_id TEXT NOT NULL,
                            champion_version TEXT NOT NULL,
                            challenger_model_id TEXT NOT NULL,
                            challenger_version TEXT NOT NULL,
                            sample_count INTEGER DEFAULT 0,
                            valid_comparisons INTEGER DEFAULT 0,
                            invalid_comparisons INTEGER DEFAULT 0,
                            action_agreement_rate REAL DEFAULT 0.0,
                            champion_expectancy_r REAL DEFAULT 0.0,
                            challenger_expectancy_r REAL DEFAULT 0.0,
                            champion_drawdown_r REAL DEFAULT 0.0,
                            challenger_drawdown_r REAL DEFAULT 0.0,
                            evidence_status TEXT DEFAULT 'INSUFFICIENT_EVIDENCE',
                            samples_required INTEGER DEFAULT 30,
                            samples_observed INTEGER DEFAULT 0,
                            by_regime TEXT DEFAULT '{}',
                            by_strategy TEXT DEFAULT '{}',
                            best_regimes TEXT DEFAULT '[]',
                            worst_regimes TEXT DEFAULT '[]',
                            degraded_regimes TEXT DEFAULT '[]',
                            degraded_strategies TEXT DEFAULT '[]',
                            evaluated_at TEXT NOT NULL,
                            payload TEXT DEFAULT '{}'
                        );
                        """
                    )
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS shadow_promotions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            run_id TEXT UNIQUE NOT NULL,
                            candidate_model_id TEXT NOT NULL,
                            candidate_version TEXT NOT NULL,
                            champion_model_id TEXT NOT NULL,
                            champion_version TEXT NOT NULL,
                            final_score REAL DEFAULT 0.0,
                            eligible INTEGER DEFAULT 0,
                            vetoes TEXT DEFAULT '[]',
                            reasons TEXT DEFAULT '[]',
                            evaluated_at TEXT NOT NULL,
                            payload TEXT DEFAULT '{}'
                        );
                        """
                    )
                    for idx in (
                        "CREATE INDEX IF NOT EXISTS idx_shadow_decisions_run ON shadow_decisions(run_id, timestamp);",
                        "CREATE INDEX IF NOT EXISTS idx_shadow_decisions_symbol ON shadow_decisions(symbol, timestamp);",
                        "CREATE INDEX IF NOT EXISTS idx_shadow_runs_status ON shadow_runs(status);",
                    ):
                        conn.execute(idx)
                    conn.commit()
                    self._schema_ensured = True
                finally:
                    conn.close()
            except Exception as e:
                logger.error("[SHADOW] schema init failed", error=str(e))
        else:
            # PostgreSQL: the ops_shadow domain's pooled backend provisions the
            # translated schema (see nexus_scalp.shadow.schema); the fabric
            # runs the CREATE IF NOT EXISTS statements idempotently.
            self._schema_ensured = ops_ensure_schema(
                self.audit_repo, OPS_SHADOW_DOMAIN, operation="shadow.ensure_schema"
            )
        # Additive SHADOW_EVIDENCE v2 columns — AFTER base tables exist.
        self._ensure_additive_columns()

    def _ensure_additive_columns(self) -> None:
        """SHADOW_EVIDENCE v2 additive migration (runs at most once).

        Adds the paired-outcome columns to shadow_decisions and the
        run-freeze columns to shadow_runs when absent. Deterministic,
        non-destructive: existing rows read back as NULL (NOT_RECORDED).
        """
        if self._additive_ensured or not self.audit_repo:
            return
        if self.audit_repo._is_sqlite:
            try:
                conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
                try:
                    self._add_missing_columns(
                        conn,
                        "shadow_decisions",
                        [
                            ("champion_entry", "REAL DEFAULT 0.0"),
                            ("champion_sl", "REAL DEFAULT 0.0"),
                            ("champion_tp", "REAL DEFAULT 0.0"),
                            ("shadow_entry", "REAL DEFAULT 0.0"),
                            ("shadow_sl", "REAL DEFAULT 0.0"),
                            ("shadow_tp", "REAL DEFAULT 0.0"),
                            ("spread_usd", "REAL DEFAULT 0.0"),
                            ("shadow_r", "REAL"),
                            ("shadow_mfe_r", "REAL"),
                            ("shadow_mae_r", "REAL"),
                            ("shadow_pnl_usd", "REAL"),
                            ("shadow_holding_sec", "REAL"),
                            ("shadow_exit_reason", "TEXT DEFAULT ''"),
                            ("delta_r", "REAL"),
                            ("outcome_status", "TEXT DEFAULT 'NOT_RECORDED'"),
                            # DEEP-OPT L1: minimal-representation columns. An
                            # existing database gains them empty; the backfill
                            # below migrates the data out of the retired JSON
                            # mirror and the caller compacts the table.
                            ("champion_probabilities", "TEXT DEFAULT '[]'"),
                            ("challenger_probabilities", "TEXT DEFAULT '[]'"),
                            ("champion_strategy_id", "TEXT DEFAULT ''"),
                            ("challenger_strategy_id", "TEXT DEFAULT ''"),
                            ("hypothetical_risk_pct", "REAL DEFAULT 0.0"),
                            ("hypothetical_volume", "REAL DEFAULT 0.0"),
                            ("hypothetical_entry", "REAL DEFAULT 0.0"),
                            ("hypothetical_exit", "REAL DEFAULT 0.0"),
                        ],
                    )
                    self._backfill_minimal_representation(conn)
                    self._add_missing_columns(
                        conn,
                        "shadow_runs",
                        [
                            ("git_revision", "TEXT DEFAULT ''"),
                            ("configuration_version", "TEXT DEFAULT ''"),
                            ("challenger_artifact_hash", "TEXT DEFAULT ''"),
                            ("champion_artifact_hash", "TEXT DEFAULT ''"),
                        ],
                    )
                    conn.commit()
                    self._additive_ensured = True
                finally:
                    conn.close()
            except Exception as e:
                logger.error("[SHADOW] additive migration failed (isolated)", error=str(e))
            return
        # PostgreSQL: the additive columns are part of the ops_shadow domain's
        # translated schema (see nexus_scalp.shadow.schema), so provisioning
        # the domain already installs them. Nothing to ALTER at runtime — the
        # domain is the single source of truth for its own column set.
        #
        # DEEP-OPT L1 FOLLOW-UP: that assumption is why the live PostgreSQL
        # ledger kept 108 MB of retired mirror JSON after the producer stopped
        # writing it. The domain schema is authored in the SQLite dialect and
        # only the eight DEEP-OPT columns were translated into the live table
        # when it was first provisioned — a domain already provisioned is NEVER
        # re-translated, so later column additions to the domain DDL do not
        # reach an existing PostgreSQL database. A SQLite box heals via the
        # branch above on the next ``ensure_schema``; a PostgreSQL box did not
        # heal at all.
        #
        # The runtime additive heal below closes that gap using the domain's
        # own pooled write backend — the same seam every other domain DDL path
        # uses, so no raw connection and no sqlite3 import. It is idempotent
        # (each missing column is guarded by an information_schema check) and
        # it then runs the same provider-neutral backfill, which migrates the
        # retired mirror into the minimal columns server-side. On a ledger that
        # was provisioned before DEEP-OPT this reclaims the mirror bytes; on a
        # fresh PostgreSQL database it finds nothing to do.
        try:
            self._ensure_additive_columns_postgres()
        except Exception as e:
            logger.error("[SHADOW] additive migration failed (pg)", error=str(e))
        self._additive_ensured = True

    def _ensure_additive_columns_postgres(self) -> None:
        """Additive SHADOW_EVIDENCE v2 + DEEP-OPT L1 columns on PostgreSQL.

        The mirror of the SQLite branch in :meth:`_ensure_additive_columns`,
        for the case the domain was provisioned before these columns were
        authored. Uses the domain's pooled write backend; never opens a raw
        connection and never imports ``sqlite3``.
        """
        from nexus_scalp.adapters.database.provider_store import _write_backend

        backend = _write_backend(self.audit_repo, domain=OPS_SHADOW_DOMAIN, provision=True)
        if backend is None:
            return
        minimal_columns = [
            ("champion_probabilities", "TEXT DEFAULT '[]'"),
            ("challenger_probabilities", "TEXT DEFAULT '[]'"),
            ("champion_strategy_id", "TEXT DEFAULT ''"),
            ("challenger_strategy_id", "TEXT DEFAULT ''"),
            ("hypothetical_risk_pct", "REAL DEFAULT 0.0"),
            ("hypothetical_volume", "REAL DEFAULT 0.0"),
            ("hypothetical_entry", "REAL DEFAULT 0.0"),
            ("hypothetical_exit", "REAL DEFAULT 0.0"),
            # Mirror-only provenance, carried out of the retired payload so the
            # mirror can be reclaimed without losing it. See
            # :meth:`_backfill_minimal_representation` for the full mapping.
            ("mirror_created_at", "TEXT DEFAULT ''"),
            ("champion_model_version", "TEXT DEFAULT ''"),
            ("challenger_model_version", "TEXT DEFAULT ''"),
            ("champion_artifact_hash", "TEXT DEFAULT ''"),
            ("challenger_artifact_hash", "TEXT DEFAULT ''"),
            ("shared_configuration_version", "TEXT DEFAULT ''"),
        ]
        run_columns = [
            ("git_revision", "TEXT DEFAULT ''"),
            ("configuration_version", "TEXT DEFAULT ''"),
            ("challenger_artifact_hash", "TEXT DEFAULT ''"),
            ("champion_artifact_hash", "TEXT DEFAULT ''"),
        ]
        pool = getattr(backend, "pool", None)
        try:
            conn_ctx = pool.connection() if pool is not None else backend
        except Exception:
            conn_ctx = backend
        with conn_ctx as conn:
            self._add_missing_columns(conn, "shadow_decisions", minimal_columns)
            self._add_missing_columns(conn, "shadow_runs", run_columns)
            self._backfill_minimal_representation(conn)

    @staticmethod
    def _add_missing_columns(conn: Any, table: str, columns: list[tuple[str, str]]) -> None:
        is_sqlite = isinstance(conn, sqlite3.Connection)
        try:
            if is_sqlite:
                existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table});")}
            else:
                existing = {
                    row[0]
                    for row in conn.execute(
                        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                        (table,),
                    )
                }
        except Exception:
            existing = set()
        for name, ddl in columns:
            if name not in existing:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl};")
                except Exception as e:
                    logger.error(
                        "[SHADOW] add column failed", table=table, column=name, error=str(e)
                    )

    def _backfill_minimal_representation(self, conn: Any) -> None:
        """Migrates the retired JSON mirror into the minimal columns.

        DEEP-OPT L1, contract rule 86 (rollback + retained legacy reader):
        legacy rows carry ``payload`` and empty minimal columns. The extraction
        runs ONCE per row — the guard is "this row has a live mirror and no
        probabilities yet", so a re-run is a no-op.

        Only the fields with no column are migrated (probabilities + the four
        scalars + the three mirror-only provenance fields, below). Everything
        else in the mirror is byte-identical to a column that is already
        populated, so nothing is lost by leaving it behind.

        The retired ``payload`` column is RETIRED, NOT DROPPED: a SQLite
        ``DROP COLUMN`` rewrites every row, which is exactly the write
        amplification the contract forbids. The reclaimable bytes are reported
        instead and the file is compacted explicitly via :func:`compact_database`.

        PROVIDER-PORTABLE (DEEP-OPT L1 follow-up): the original implementation
        used SQLite's ``PRAGMA table_info`` and a Python-side row loop, so it
        never ran on PostgreSQL — the live PostgreSQL ledger kept all 55k rows
        of mirror JSON (108 MB) after the producer stopped writing it. The
        scan and the UPDATE are now both SQL-side and provider-neutral, so a
        PostgreSQL box heals on the same ``ensure_schema`` call a SQLite one
        does. The eligibility SELECT is deliberately a single bounded pass
        (never a per-row round trip): 55k rows over one cursor, not 55k queries.
        """
        is_sqlite = isinstance(conn, sqlite3.Connection)
        try:
            if is_sqlite:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(shadow_decisions);")}
            else:
                cols = {
                    row[0]
                    for row in conn.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'shadow_decisions'"
                    )
                }
        except Exception:
            return
        if not {"payload", "champion_probabilities"} <= cols:
            return

        # The three mirror-only provenance fields. ``shared_input`` is 12.4% of
        # the mirror's bytes but every one of ITS fields is already a column
        # except ``configuration_version`` (verified on the live ledger), so it
        # is reconstructed from that single key rather than stored wholesale.
        provenance_cols = [
            ("mirror_created_at", "TEXT DEFAULT ''"),
            ("champion_model_version", "TEXT DEFAULT ''"),
            ("challenger_model_version", "TEXT DEFAULT ''"),
            ("champion_artifact_hash", "TEXT DEFAULT ''"),
            ("challenger_artifact_hash", "TEXT DEFAULT ''"),
            ("shared_configuration_version", "TEXT DEFAULT ''"),
        ]
        for name, ddl in provenance_cols:
            if name not in cols:
                try:
                    conn.execute(f"ALTER TABLE shadow_decisions ADD COLUMN {name} {ddl};")
                except Exception as e:
                    logger.error(
                        "[SHADOW] add column failed",
                        table="shadow_decisions",
                        column=name,
                        error=str(e),
                    )

        # One provider-neutral UPDATE does the whole migration server-side.
        # The JSON extraction is expressed with each engine's own function
        # family rather than a Python row loop: on PostgreSQL jsonb operators
        # do it in one statement, on SQLite json_extract does. Both keep the
        # work off the application side and both are idempotent — the guard
        # (live mirror AND no probabilities yet) is in the WHERE clause, so a
        # second call updates zero rows.
        if is_sqlite:
            stmt = """
                UPDATE shadow_decisions SET
                    champion_probabilities = COALESCE(
                        json_extract(payload, '$.champion_probabilities'), '[]'),
                    challenger_probabilities = COALESCE(
                        json_extract(payload, '$.challenger_probabilities'), '[]'),
                    champion_strategy_id = COALESCE(NULLIF(
                        json_extract(payload, '$.champion_strategy_id'), ''),
                        NULLIF(json_extract(payload, '$.champion.strategy_id'), ''), ''),
                    challenger_strategy_id = COALESCE(NULLIF(
                        json_extract(payload, '$.challenger_strategy_id'), ''),
                        NULLIF(json_extract(payload, '$.challenger.strategy_id'), ''), ''),
                    hypothetical_risk_pct = COALESCE(
                        json_extract(payload, '$.hypothetical.risk_pct'),
                        json_extract(payload, '$.hypothetical_risk_pct'), 0.0),
                    hypothetical_volume = COALESCE(
                        json_extract(payload, '$.hypothetical.volume'),
                        json_extract(payload, '$.hypothetical_volume'), 0.0),
                    hypothetical_entry = COALESCE(
                        json_extract(payload, '$.hypothetical.entry'),
                        json_extract(payload, '$.hypothetical_entry'), 0.0),
                    hypothetical_exit = COALESCE(
                        json_extract(payload, '$.hypothetical.exit'),
                        json_extract(payload, '$.hypothetical_exit'), 0.0),
                    mirror_created_at = COALESCE(
                        json_extract(payload, '$.created_at'), ''),
                    champion_model_version = COALESCE(
                        json_extract(payload, '$.champion.model_version'), ''),
                    challenger_model_version = COALESCE(
                        json_extract(payload, '$.challenger.model_version'), ''),
                    champion_artifact_hash = COALESCE(
                        json_extract(payload, '$.champion.artifact_hash'), ''),
                    challenger_artifact_hash = COALESCE(
                        json_extract(payload, '$.challenger.artifact_hash'), ''),
                    shared_configuration_version = COALESCE(
                        json_extract(payload, '$.shared_input.configuration_version'), '')
                WHERE payload IS NOT NULL AND payload != '' AND payload != '{}'
                  AND (champion_probabilities IS NULL
                       OR champion_probabilities IN ('', '[]'))
            """
        else:
            # PostgreSQL: the mirror is TEXT, so cast to jsonb for extraction.
            # The nested groups (champion/challenger/hypothetical) are objects;
            # ->> returns text, which is what the TEXT columns want.
            #
            # BYTE-IDENTITY: jsonb's text output is the *expanded* form
            # ('[0.1, 0.2]' with a space after each comma). The SQLite side
            # (json_extract over the ORIGINAL text) yields the producer's own
            # bytes, which are compact. read_decision_row prefers the column
            # over the mirror, so a divergence here would silently change the
            # probabilities every consumer reads after migration. Re-serialize
            # through the same compact encoder the write path uses so the
            # migrated column is byte-identical to the mirror's own value.
            stmt = """
                UPDATE shadow_decisions SET
                    champion_probabilities = COALESCE(
                        NULLIF(replace((payload::jsonb -> 'champion_probabilities')::text,
                                       ', ', ','), '[]'), '[]'),
                    challenger_probabilities = COALESCE(
                        NULLIF(replace((payload::jsonb -> 'challenger_probabilities')::text,
                                       ', ', ','), '[]'), '[]'),
                    champion_strategy_id = COALESCE(NULLIF(
                        (payload::jsonb ->> 'champion_strategy_id'), ''),
                        NULLIF((payload::jsonb -> 'champion') ->> 'strategy_id', ''), ''),
                    challenger_strategy_id = COALESCE(NULLIF(
                        (payload::jsonb ->> 'challenger_strategy_id'), ''),
                        NULLIF((payload::jsonb -> 'challenger') ->> 'strategy_id', ''), ''),
                    hypothetical_risk_pct = COALESCE(
                        ((payload::jsonb -> 'hypothetical') ->> 'risk_pct')::double precision,
                        (payload::jsonb ->> 'hypothetical_risk_pct')::double precision, 0.0),
                    hypothetical_volume = COALESCE(
                        ((payload::jsonb -> 'hypothetical') ->> 'volume')::double precision,
                        (payload::jsonb ->> 'hypothetical_volume')::double precision, 0.0),
                    hypothetical_entry = COALESCE(
                        ((payload::jsonb -> 'hypothetical') ->> 'entry')::double precision,
                        (payload::jsonb ->> 'hypothetical_entry')::double precision, 0.0),
                    hypothetical_exit = COALESCE(
                        ((payload::jsonb -> 'hypothetical') ->> 'exit')::double precision,
                        (payload::jsonb ->> 'hypothetical_exit')::double precision, 0.0),
                    mirror_created_at = COALESCE(
                        (payload::jsonb ->> 'created_at'), ''),
                    champion_model_version = COALESCE(
                        ((payload::jsonb -> 'champion') ->> 'model_version'), ''),
                    challenger_model_version = COALESCE(
                        ((payload::jsonb -> 'challenger') ->> 'model_version'), ''),
                    champion_artifact_hash = COALESCE(
                        ((payload::jsonb -> 'champion') ->> 'artifact_hash'), ''),
                    challenger_artifact_hash = COALESCE(
                        ((payload::jsonb -> 'challenger') ->> 'artifact_hash'), ''),
                    shared_configuration_version = COALESCE(
                        ((payload::jsonb -> 'shared_input') ->> 'configuration_version'), '')
                WHERE payload IS NOT NULL AND payload != '' AND payload != '{}'
                  AND (champion_probabilities IS NULL
                       OR champion_probabilities IN ('', '[]'))
            """
        try:
            cur = conn.execute(stmt)
            migrated = getattr(cur, "rowcount", -1) or 0
        except Exception as e:
            # A provider that lacks the JSON function family (or a row whose
            # payload is not valid JSON) must not break schema init. The legacy
            # Python-side path below is the fallback for those engines.
            logger.warning(
                "[SHADOW] server-side mirror backfill unavailable, falling back to the row loop",
                error=str(e),
            )
            migrated = self._backfill_minimal_representation_loop(conn, cols)

        if migrated:
            logger.info(
                "[SHADOW] minimal-representation backfill complete",
                rows=migrated,
                reclaimable_bytes=self.pending_mirror_bytes(conn),
            )

    def _backfill_minimal_representation_loop(self, conn: Any, cols: set[str]) -> int:
        """Per-row fallback when a provider has no usable JSON function family.

        This is the original implementation, preserved: it reads each eligible
        row, extracts the eight minimal fields plus the three provenance ones
        in Python, and UPDATEs them individually. It is correct on every
        provider (it needs no JSON SQL at all — :mod:`json` does the parsing),
        which is why it stays as the safety net for the server-side statement.
        """
        try:
            # ci: schema-guarded — caller enters this fallback only when payload exists.
            pending = conn.execute(
                "SELECT id, payload FROM shadow_decisions "
                "WHERE payload IS NOT NULL AND payload != '' AND payload != '{}' "
                "AND (champion_probabilities IS NULL OR champion_probabilities IN ('', '[]'))"
            ).fetchall()
        except Exception:
            return 0
        migrated = 0
        for row in pending:
            row_id = row[0]
            raw = row[1]
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            champion = data.get("champion") or {}
            challenger = data.get("challenger") or {}
            shared = data.get("shared_input") or {}
            hyp = data.get("hypothetical") or {}
            try:
                conn.execute(
                    "UPDATE shadow_decisions SET "
                    "champion_probabilities = ?, challenger_probabilities = ?, "
                    "champion_strategy_id = ?, challenger_strategy_id = ?, "
                    "hypothetical_risk_pct = ?, hypothetical_volume = ?, "
                    "hypothetical_entry = ?, hypothetical_exit = ?, "
                    "mirror_created_at = ?, "
                    "champion_model_version = ?, challenger_model_version = ?, "
                    "champion_artifact_hash = ?, challenger_artifact_hash = ?, "
                    "shared_configuration_version = ? "
                    "WHERE id = ?",
                    (
                        _compact_vector(data.get("champion_probabilities") or []),
                        _compact_vector(data.get("challenger_probabilities") or []),
                        data.get("champion_strategy_id") or champion.get("strategy_id") or "",
                        data.get("challenger_strategy_id") or challenger.get("strategy_id") or "",
                        _as_float(hyp.get("risk_pct", data.get("hypothetical_risk_pct", 0.0))),
                        _as_float(hyp.get("volume", data.get("hypothetical_volume", 0.0))),
                        _as_float(hyp.get("entry", data.get("hypothetical_entry", 0.0))),
                        _as_float(hyp.get("exit", data.get("hypothetical_exit", 0.0))),
                        data.get("created_at") or "",
                        champion.get("model_version") or "",
                        challenger.get("model_version") or "",
                        champion.get("artifact_hash") or "",
                        challenger.get("artifact_hash") or "",
                        shared.get("configuration_version") or "",
                        row_id,
                    ),
                )
                migrated += 1
            except Exception as e:
                logger.error("[SHADOW] payload backfill row failed", row=row_id, error=str(e))
        return migrated

    def reclaim_mirror_payloads(
        self,
        *,
        batch_size: int = 5000,
        max_rows: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """Reclaim the retired ``payload`` mirror already on disk.

        The producer stopped serializing the record in DEEP-OPT L1, but rows
        written before that still carry the duplicate: 108.3 MB of a 227 MB
        table on the live PostgreSQL ledger. This is the read-side twin of
        that change — it NULLs the mirror on rows that
        :meth:`_backfill_minimal_representation` has already migrated.

        The mirror is retired, not dropped, so this sets it to ``''`` (the
        column's pre-L1 default) rather than removing it. The read contract
        (:meth:`read_decision_row`) treats an empty mirror as "no mirror" and
        reads the columns, which is exactly the post-migration shape.

        Lifecycle: idempotent (the guard is "payload non-empty AND the row has
        been migrated"), bounded (``max_rows`` caps a call and the last round
        fetches only the remainder), resumable (a keyset cursor over ``id``,
        not OFFSET), and observable (eligible/archived/bytes counters).
        """
        counters: dict[str, int] = {
            "eligible": 0,
            "reclaimed": 0,
            "bytes_before": 0,
            "rounds": 0,
        }
        if not self.audit_repo:
            return counters
        bsize = max(1, int(batch_size))
        remaining_cap = None if max_rows is None else max(0, int(max_rows))
        # Only rows the backfill has migrated are reclaimable: a row that still
        # has a mirror AND no probabilities has not been migrated yet, so
        # clearing it would destroy its only copy of those fields.
        # The placeholder is engine-specific: SQLite uses ?, PostgreSQL %s.
        ph = "?"
        if not getattr(self.audit_repo, "_is_sqlite", True):
            ph = "%s"
        sql = (
            "SELECT id, octet_length(payload) AS n FROM shadow_decisions "
            "WHERE payload IS NOT NULL AND payload <> '' AND payload <> '{}' "
            "AND (champion_probabilities IS NOT NULL AND champion_probabilities NOT IN ('', '[]')) "
            f"ORDER BY id LIMIT {ph}"
        )
        keyset_sql = (
            "SELECT id, octet_length(payload) AS n FROM shadow_decisions "
            "WHERE payload IS NOT NULL AND payload <> '' AND payload <> '{}' "
            "AND (champion_probabilities IS NOT NULL AND champion_probabilities NOT IN ('', '[]')) "
            f"AND id > {ph} ORDER BY id LIMIT {ph}"
        )
        last_id = 0
        is_sqlite = bool(getattr(self.audit_repo, "_is_sqlite", True))
        while True:
            if remaining_cap is not None and counters["eligible"] >= remaining_cap:
                break
            fetch = bsize
            if remaining_cap is not None:
                fetch = min(bsize, remaining_cap - counters["eligible"])
            if fetch <= 0:
                break
            if is_sqlite:
                # The SQLite path borrows the repository's one connection seam
                # (Phase 32: domain code never opens its own sqlite3).
                from nexus_scalp.adapters.database.provider_store import sqlite_connection

                with sqlite_connection(self.audit_repo, 15.0) as conn:
                    rows = (
                        conn.execute(sql, (fetch,)).fetchall()
                        if last_id == 0
                        else conn.execute(keyset_sql, (last_id, fetch)).fetchall()
                    )
                    if not rows:
                        break
                    ids, last_id = self._collect(rows, counters, last_id)
                    if dry_run:
                        counters["rounds"] += 1
                        continue
                    placeholders = ",".join("?" * len(ids))
                    cur = conn.execute(
                        f"UPDATE shadow_decisions SET payload = '' WHERE id IN ({placeholders})",
                        ids,
                    )
                    counters["reclaimed"] += int(getattr(cur, "rowcount", len(ids)) or 0)
                    counters["rounds"] += 1
            else:
                # PostgreSQL: the domain's pooled read plane, the same seam the
                # operator surface uses — never a raw connection.
                from nexus_scalp.adapters.database.provider_store import query_rows

                rows = (
                    query_rows(self.audit_repo, sql, (fetch,))
                    if last_id == 0
                    else query_rows(self.audit_repo, keyset_sql, (last_id, fetch))
                )
                if not rows:
                    break
                ids, last_id = self._collect(rows, counters, last_id)
                if dry_run:
                    counters["rounds"] += 1
                    continue
                counters["reclaimed"] += self._clear_payloads(ids)
                counters["rounds"] += 1
        return counters

    @staticmethod
    def _collect(rows: Any, counters: dict[str, int], last_id: int) -> tuple[list[int], int]:
        ids: list[int] = []
        for r in rows:
            rid = int(r["id"] if isinstance(r, dict) else r[0])
            nbytes = int(r["n"] if isinstance(r, dict) else r[1] or 0)
            last_id = rid
            counters["eligible"] += 1
            counters["bytes_before"] += nbytes
            ids.append(rid)
        return ids, last_id

    def _clear_payloads(self, ids: list[int]) -> int:
        """One bounded UPDATE on the domain's write plane (PostgreSQL path)."""
        from nexus_scalp.adapters.database.provider_store import _write_backend

        backend = _write_backend(self.audit_repo, domain=OPS_SHADOW_DOMAIN, provision=True)
        if backend is None:
            return 0
        pool = getattr(backend, "pool", None)
        try:
            conn_ctx = pool.connection() if pool is not None else backend
        except Exception:
            conn_ctx = backend
        with conn_ctx as conn:
            cur = conn.execute(
                "UPDATE shadow_decisions SET payload = '' WHERE id = ANY(%s)", (ids,)
            )
            return int(getattr(cur, "rowcount", len(ids)) or 0)

    @staticmethod
    def pending_mirror_bytes(conn: Any) -> int:
        """Retired-mirror bytes still on disk (0 once the column is dropped)."""
        is_sqlite = isinstance(conn, sqlite3.Connection)
        try:
            if is_sqlite:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(shadow_decisions);")}
                if "payload" not in cols:
                    return 0
                # ci: schema-guarded — payload presence was checked above.
                return int(
                    conn.execute(
                        "SELECT COALESCE(SUM(LENGTH(payload)), 0) FROM shadow_decisions"
                    ).fetchone()[0]
                    or 0
                )
            row = conn.execute(
                "SELECT COALESCE(SUM(LENGTH(payload)), 0) FROM shadow_decisions"
            ).fetchone()
            return int(row[0] or 0) if row else 0
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save_run(self, run: ShadowRun) -> bool:
        if not self.audit_repo:
            return False
        self.ensure_schema()
        args = (
            run.run_id,
            run.champion.model_id,
            run.champion.model_version,
            run.challenger.model_id,
            run.challenger.model_version,
            run.status,
            run.started_at.isoformat(),
            run.finished_at.isoformat() if run.finished_at else "",
            run.decision_count,
            run.error,
            # CHG-0046 D11: run-freeze identity
            run.git_revision,
            run.configuration_version,
            run.challenger_artifact_hash,
            run.champion_artifact_hash,
        )
        return ops_queue_write(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            _sql_for(self.audit_repo, _SQLITE_RUN_SQL, _PG_RUN_SQL),
            args,
            operation="shadow.save_run",
        )

    def save_decision(self, decision: ShadowDecisionRecord) -> bool:
        if not self.audit_repo:
            return False
        self.ensure_schema()
        args = (
            decision.shadow_decision_id,
            decision.run_id,
            decision.decision_id,
            decision.timestamp.isoformat(),
            decision.symbol,
            decision.timeframe,
            decision.champion.model_id,
            decision.champion.model_version,
            decision.challenger.model_id,
            decision.challenger.model_version,
            decision.shared_input.feature_schema_id,
            decision.shared_input.feature_dimension,
            decision.shared_input.feature_hash,
            decision.shared_input.regime,
            decision.shared_input.session,
            decision.champion_action,
            decision.champion_confidence,
            decision.challenger_action,
            decision.challenger_confidence,
            1 if decision.action_agreement else 0,
            1 if decision.valid_comparison else 0,
            decision.invalid_reason,
            decision.hypothetical_pnl_usd,
            decision.hypothetical_r,
            decision.mfe_r,
            decision.mae_r,
            decision.holding_duration_sec,
            decision.exit_reason,
            1 if decision.simulated else 0,
            # CHG-0046: paired outcome + geometry fields (SHADOW_EVIDENCE v2)
            decision.champion_entry,
            decision.champion_sl,
            decision.champion_tp,
            decision.shadow_entry,
            decision.shadow_sl,
            decision.shadow_tp,
            decision.spread_usd,
            decision.shadow_r,
            decision.shadow_mfe_r,
            decision.shadow_mae_r,
            decision.shadow_pnl_usd,
            decision.shadow_holding_sec,
            decision.shadow_exit_reason,
            decision.delta_r,
            decision.outcome_status,
            # DEEP-OPT L1: minimal durable representation. The whole-record
            # json.dumps(model_dump()) mirror is gone — it duplicated the
            # columns above (46.6% of its bytes, measured) and its unique
            # remainder is these nine fields plus the run row. Probabilities
            # are stored as compact JSON vectors; the scalar pair
            # (entry/exit) gets real columns so the outcome resolver's UPDATE
            # rewrites scalars instead of re-serializing the whole record
            # (Part 5 rule 41: no update amplification).
            _compact_vector(decision.champion_probabilities),
            _compact_vector(decision.challenger_probabilities),
            decision.champion_strategy_id,
            decision.challenger_strategy_id,
            decision.hypothetical_risk_pct,
            decision.hypothetical_volume,
            decision.hypothetical_entry,
            decision.hypothetical_exit,
        )
        return ops_queue_write(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            _sql_for(self.audit_repo, _SQLITE_DECISION_SQL, _PG_DECISION_SQL),
            args,
            operation="shadow.save_decision",
        )

    def save_comparison(self, comparison: ShadowComparison) -> bool:
        if not self.audit_repo:
            return False
        self.ensure_schema()
        args = (
            comparison.run_id,
            comparison.champion.model_id,
            comparison.champion.model_version,
            comparison.challenger.model_id,
            comparison.challenger.model_version,
            comparison.sample_count,
            comparison.valid_comparisons,
            comparison.invalid_comparisons,
            comparison.action_agreement_rate,
            comparison.champion_expectancy_r,
            comparison.challenger_expectancy_r,
            comparison.champion_drawdown_r,
            comparison.challenger_drawdown_r,
            comparison.evidence_status.value,
            comparison.samples_required,
            comparison.samples_observed,
            json.dumps(comparison.by_regime, default=str),
            json.dumps(comparison.by_strategy, default=str),
            json.dumps(comparison.best_regimes),
            json.dumps(comparison.worst_regimes),
            json.dumps(comparison.degraded_regimes),
            json.dumps(comparison.degraded_strategies),
            comparison.evaluation_started_at.isoformat(),
            json.dumps(comparison.model_dump(mode="json"), default=str),
        )
        return ops_queue_write(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            _sql_for(self.audit_repo, _SQLITE_COMPARISON_SQL, _PG_COMPARISON_SQL),
            args,
            operation="shadow.save_comparison",
        )

    def save_promotion(self, evaluation: PromotionEvaluation) -> bool:
        if not self.audit_repo:
            return False
        self.ensure_schema()
        args = (
            evaluation.run_id,
            evaluation.candidate_model_id,
            evaluation.candidate_version,
            evaluation.champion_model_id,
            evaluation.champion_version,
            evaluation.final_score,
            1 if evaluation.eligible else 0,
            json.dumps(evaluation.vetoes),
            json.dumps(evaluation.reasons),
            evaluation.evaluated_at.isoformat(),
            json.dumps(evaluation.model_dump(mode="json"), default=str),
        )
        return ops_queue_write(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            _sql_for(self.audit_repo, _SQLITE_PROMOTION_SQL, _PG_PROMOTION_SQL),
            args,
            operation="shadow.save_promotion",
        )

    # ------------------------------------------------------------------
    # ML-OBS-001: outcome resolution (bar-close cadence, never per-tick)
    # ------------------------------------------------------------------

    @staticmethod
    def read_decision_row(row: dict[str, Any]) -> dict[str, Any]:
        """Legacy reader for a shadow_decisions row (contract rule 86).

        The retired ``payload`` mirror is no longer written, but a database that
        has not been compacted still carries it on historical rows. Any consumer
        that used to reach into the mirror for a field this store no longer
        splits into a column gets it back here, from either source, with the
        column winning (the column is the canonical representation now).

        Returns the row augmented with ``probability_vectors`` and
        ``strategy_ids`` — the only mirror content that is not a column —
        reconstructed from the minimal columns, or from the mirror when the row
        predates the backfill.
        """
        out: dict[str, Any] = dict(row)
        raw = out.pop("payload", None)
        mirror: dict[str, Any] = {}
        if isinstance(raw, str) and raw and raw != "{}":
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    mirror = parsed
            except (TypeError, ValueError):
                mirror = {}
        champion = mirror.get("champion") or {}
        challenger = mirror.get("challenger") or {}

        def _vector(column: str, mirror_key: str) -> list[float]:
            stored = out.get(column)
            if stored not in (None, "", "[]"):
                try:
                    seq = json.loads(stored)
                    if isinstance(seq, list) and seq:
                        return [float(v) for v in seq]
                except (TypeError, ValueError):
                    pass
            seq = mirror.get(mirror_key) or []
            try:
                return [float(v) for v in seq]
            except (TypeError, ValueError):
                return []

        out["champion_probabilities"] = _vector("champion_probabilities", "champion_probabilities")
        out["challenger_probabilities"] = _vector(
            "challenger_probabilities", "challenger_probabilities"
        )
        out["champion_strategy_id"] = (
            out.get("champion_strategy_id")
            or mirror.get("champion_strategy_id")
            or champion.get("strategy_id")
            or ""
        )
        out["challenger_strategy_id"] = (
            out.get("challenger_strategy_id")
            or mirror.get("challenger_strategy_id")
            or challenger.get("strategy_id")
            or ""
        )
        out["mirror_present"] = bool(mirror)
        return out

    def list_pending_decisions(
        self,
        run_id: str | None = None,
        older_than: datetime | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Reads PENDING (unresolved) shadow decisions for resolution.

        ML-OBS-001: the resolver walks the shared market path for each PENDING
        decision whose evaluation horizon has expired. ``older_than`` bounds
        the scan to decisions whose horizon provably closed (a decision whose
        horizon is still open must NOT be resolved yet — the walk would stop
        at the coverage limit and mark a live position NOT_RECORDED).

        Read-only and bounded: ``limit`` is clamped to MAX_READ_LIMIT, so even
        a run with millions of decisions never materializes an unbounded row
        set on a bar-close path.
        """
        if not self.audit_repo:
            return []
        bounded = max(1, min(int(limit), MAX_READ_LIMIT))
        sql = (
            "SELECT shadow_decision_id, run_id, timestamp, symbol, "
            "champion_action, champion_entry, champion_sl, champion_tp, "
            "challenger_action, shadow_entry, shadow_sl, "
            "shadow_tp, feature_schema_id, feature_dimension, "
            "valid_comparison, outcome_status FROM shadow_decisions "
            "WHERE outcome_status = 'PENDING'"
        )
        clauses: list[str] = []
        args: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            args.append(run_id)
        if older_than is not None:
            clauses.append("timestamp <= ?")
            args.append(older_than.isoformat())
        if clauses:
            sql += " AND " + " AND ".join(clauses)
        sql += " ORDER BY timestamp ASC LIMIT ?;"
        return ops_query_rows(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            sql,
            (*args, bounded),
            operation="shadow.list_pending_decisions",
        )

    def apply_resolved_outcome(self, decision_id: str, fields: dict[str, Any]) -> bool:
        """Updates ONE shadow_decisions row with its resolved outcome.

        Guarded by ``outcome_status = 'PENDING'``: a row already RESOLVED (by
        this path or by an offline replay) is never rewritten — historical
        evidence is immutable once resolved (INV-007 / spec 25).

        The write is ENQUEUED on the audit background worker exactly like the
        original save_decision, so the bar-close path issues zero synchronous
        SQLite commits (INV-001: the resolution hook is off the per-tick path
        and the write itself never blocks the caller).
        """
        if not self.audit_repo:
            return False
        self.ensure_schema()
        args = (
            float(fields.get("hypothetical_pnl_usd", 0.0) or 0.0),
            float(fields.get("hypothetical_r", 0.0) or 0.0),
            float(fields.get("mfe_r", 0.0) or 0.0),
            float(fields.get("mae_r", 0.0) or 0.0),
            float(fields.get("holding_duration_sec", 0.0) or 0.0),
            str(fields.get("exit_reason", "") or ""),
            _nullable_float(fields.get("shadow_r")),
            _nullable_float(fields.get("shadow_mfe_r")),
            _nullable_float(fields.get("shadow_mae_r")),
            _nullable_float(fields.get("shadow_pnl_usd")),
            _nullable_float(fields.get("shadow_holding_sec")),
            str(fields.get("shadow_exit_reason", "") or ""),
            _nullable_float(fields.get("delta_r")),
            str(fields.get("outcome_status", STATUS_RESOLVED) or STATUS_RESOLVED),
            _nullable_float(fields.get("hypothetical_entry")),
            _nullable_float(fields.get("hypothetical_exit")),
            str(decision_id),
        )
        return ops_queue_write(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            _UPDATE_OUTCOME_SQL,
            args,
            operation="shadow.apply_resolved_outcome",
        )

    def count_outcome_status(self, run_id: str | None = None) -> dict[str, int]:
        """Outcome-status histogram for a run (observability / tests).

        Counts by name, never SELECT *: legacy databases predating the
        SHADOW_EVIDENCE v2 additive migration have no outcome_status column,
        in which case every row reads as NOT_RECORDED (the honest default).
        """
        out: dict[str, int] = {}
        if not self.audit_repo:
            return out
        self.ensure_schema()
        where = "WHERE run_id = ?" if run_id else ""
        params: tuple[Any, ...] = (run_id,) if run_id else ()
        try:
            conn = self.audit_repo._connect_sqlite(timeout=5.0)
            try:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(shadow_decisions);")}
                if "outcome_status" not in cols:
                    # Legacy table: nothing was ever resolved by this path.
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM shadow_decisions {where};", params
                    ).fetchone()
                    out["NOT_RECORDED"] = int(row[0]) if row else 0
                    return out
                rows = conn.execute(
                    f"SELECT outcome_status, COUNT(*) FROM shadow_decisions {where} "
                    "GROUP BY outcome_status;",
                    params,
                ).fetchall()
                for r in rows:
                    out[str(r[0])] = int(r[1])
            finally:
                conn.close()
        except Exception as e:
            logger.error("[SHADOW] count_outcome_status failed", error=str(e))
        return out

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        if not self.audit_repo:
            return None
        rows = ops_query_rows(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            "SELECT * FROM shadow_runs WHERE run_id=?;",
            (run_id,),
            operation="shadow.get_run",
        )
        return rows[0] if rows else None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.audit_repo:
            return []
        bounded = max(1, min(int(limit), 500))
        return ops_query_rows(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            "SELECT * FROM shadow_runs ORDER BY started_at DESC LIMIT ?;",
            (bounded,),
            operation="shadow.list_runs",
        )

    def list_decisions(
        self,
        run_id: str | None = None,
        symbol: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if not self.audit_repo:
            return []
        bounded = max(1, min(int(limit), MAX_READ_LIMIT))
        sql = "SELECT * FROM shadow_decisions"
        clauses: list[str] = []
        args: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            args.append(run_id)
        if symbol:
            clauses.append("symbol = ?")
            args.append(symbol)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC LIMIT ?;"
        return ops_query_rows(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            sql,
            (*args, bounded),
            operation="shadow.list_decisions",
        )

    def get_comparison(self, run_id: str) -> dict[str, Any] | None:
        if not self.audit_repo:
            return None
        rows = ops_query_rows(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            "SELECT * FROM shadow_comparisons WHERE run_id=?;",
            (run_id,),
            operation="shadow.get_comparison",
        )
        return rows[0] if rows else None

    def get_promotion(self, run_id: str) -> dict[str, Any] | None:
        if not self.audit_repo:
            return None
        rows = ops_query_rows(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            "SELECT * FROM shadow_promotions WHERE run_id=?;",
            (run_id,),
            operation="shadow.get_promotion",
        )
        return rows[0] if rows else None

    def list_promotions(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.audit_repo:
            return []
        bounded = max(1, min(int(limit), 500))
        return ops_query_rows(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            "SELECT * FROM shadow_promotions ORDER BY evaluated_at DESC LIMIT ?;",
            (bounded,),
            operation="shadow.list_promotions",
        )

    def summary(self) -> dict[str, Any]:
        """Shadow dashboard summary.

        BUG-221: on a fresh database the shadow tables may not exist yet
        (schema is created lazily by the write path). summary() is a READ
        path used by the API/UI; without ensure_schema() it logged
        '[SHADOW] summary failed: no such table: shadow_runs' and returned
        available=False — presenting a healthy empty store as unavailable.
        ensure_schema() is idempotent + flag-guarded (no hot-path cost).
        """
        out: dict[str, Any] = {"available": False, "runs": {}, "decisions": 0, "promotions": 0}
        if not self.audit_repo:
            return out
        self.ensure_schema()
        if self.audit_repo._is_sqlite:
            try:
                conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
                try:
                    for r in conn.execute(
                        "SELECT status, COUNT(*) AS c FROM shadow_runs GROUP BY status;"
                    ).fetchall():
                        out["runs"][str(r[0])] = int(r[1])
                    row = conn.execute("SELECT COUNT(*) FROM shadow_decisions;").fetchone()
                    out["decisions"] = int(row[0]) if row else 0
                    row = conn.execute("SELECT COUNT(*) FROM shadow_promotions;").fetchone()
                    out["promotions"] = int(row[0]) if row else 0
                    out["available"] = True
                finally:
                    conn.close()
            except Exception as e:
                logger.error("[SHADOW] summary failed", error=str(e))
            return out
        # PostgreSQL: the ops domain's pooled read backend (schema is ensured
        # above, so absence here means an empty store, not a missing table).
        try:
            for r in ops_query_rows(
                self.audit_repo,
                OPS_SHADOW_DOMAIN,
                "SELECT status, COUNT(*) AS c FROM shadow_runs GROUP BY status;",
                (),
                operation="shadow.summary.runs",
            ):
                out["runs"][str(r["status"])] = int(r["c"])
            out["decisions"] = int(
                ops_query_scalar(
                    self.audit_repo,
                    OPS_SHADOW_DOMAIN,
                    "SELECT COUNT(*) FROM shadow_decisions;",
                    (),
                    operation="shadow.summary.decisions",
                )
                or 0
            )
            out["promotions"] = int(
                ops_query_scalar(
                    self.audit_repo,
                    OPS_SHADOW_DOMAIN,
                    "SELECT COUNT(*) FROM shadow_promotions;",
                    (),
                    operation="shadow.summary.promotions",
                )
                or 0
            )
            out["available"] = True
        except Exception as e:
            logger.error("[SHADOW] summary failed", error=str(e))
        return out


def _nullable_float(v: Any) -> float | None:
    """None-preserving float coercion (None = NOT_RECORDED, never 0.0)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # Non-finite results (NaN/inf) are unusable geometry, not a number.
    if math.isnan(f) or math.isinf(f):
        return None
    return f
