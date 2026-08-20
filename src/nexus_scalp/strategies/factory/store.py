"""
Strategy Factory — Persistence Store
====================================
STRATEGY FACTORY (2026-08-20).

All factory research memory is persisted through the SAME AuditRepository
background queue as the research layer (spec 38 / 41 / 74 / 75). Tables:

  factory_generations      — one row per population (spec 25)
  factory_candidates       — one row per generated candidate + structural verdict
  factory_failures         — structured rejection reasons per candidate (spec 23)
  factory_events           — immutable event stream for the UI (spec 50)
  factory_runs             — research-run ledger (reproducibility, spec 40)
  factory_provider_usage   — LLM request/cost ledger (spec 45)
  factory_loop_state       — autonomous loop control-plane state (spec 73)

Immutability: a candidate's historical record is never mutated; lifecycle
updates append (mirrors strategy_registry contract).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.strategies.factory.store")

MAX_READ_LIMIT = 2000


def _json(value: Any) -> str:
    if value is None:
        return "{}"
    try:
        encoded = json.dumps(value, default=str)
        if encoded == "null":
            return "{}"
        return encoded
    except Exception:
        return "{}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _conn(repo: AuditRepository) -> sqlite3.Connection | None:
    if not repo._is_sqlite:
        return None
    try:
        conn = sqlite3.connect(repo._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] connect failed", error=str(e))
        return None


# ---------------------------------------------------------------------------
# Writes (through the audit background queue — never blocks the live path)
# ---------------------------------------------------------------------------


def upsert_generation(repo: AuditRepository, generation: dict[str, Any]) -> bool:
    if not repo._is_sqlite:
        return False
    sql = """
        INSERT INTO factory_generations (
            generation_id, number, mode, parent_generation, population_target,
            created_at, completed_at, status, config
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(generation_id) DO UPDATE SET
            status=excluded.status, completed_at=excluded.completed_at,
            config=excluded.config;
    """
    try:
        repo._queue.put_nowait(
            (
                sql,
                (
                    generation.get("generation_id", ""),
                    int(generation.get("number", 0)),
                    generation.get("mode", "MANUAL"),
                    generation.get("parent_generation", ""),
                    int(generation.get("population_target", 0)),
                    generation.get("created_at", _now()),
                    generation.get("completed_at"),
                    generation.get("status", "PENDING"),
                    _json(generation.get("config")),
                ),
            )
        )
        return True
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] upsert_generation failed", error=str(e))
        return False


def upsert_candidate(repo: AuditRepository, candidate: dict[str, Any]) -> bool:
    if not repo._is_sqlite:
        return False
    sql = """
        INSERT INTO factory_candidates (
            candidate_id, definition_hash, generation_id, source, operator,
            parent_ids, family, population_index, dsl, structural, lifecycle,
            failure_reasons, llm_response_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
            structural=excluded.structural,
            lifecycle=excluded.lifecycle,
            failure_reasons=excluded.failure_reasons;
    """
    try:
        repo._queue.put_nowait(
            (
                sql,
                (
                    candidate.get("candidate_id", ""),
                    candidate.get("definition_hash", ""),
                    candidate.get("generation_id", ""),
                    candidate.get("source", "TEMPLATE"),
                    candidate.get("operator", "NONE"),
                    _json(candidate.get("parent_ids")),
                    candidate.get("family", "HYBRID"),
                    int(candidate.get("population_index", 0)),
                    _json(candidate.get("dsl")),
                    _json(candidate.get("structural")),
                    candidate.get("lifecycle", "GENERATED"),
                    _json(candidate.get("failure_reasons")),
                    candidate.get("llm_response_id", ""),
                    candidate.get("created_at", _now()),
                ),
            )
        )
        return True
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] upsert_candidate failed", error=str(e))
        return False


def record_failure(repo: AuditRepository, failure: dict[str, Any]) -> bool:
    if not repo._is_sqlite:
        return False
    sql = """
        INSERT INTO factory_failures (
            failure_id, candidate_id, strategy_id, generation_id, stage,
            reason, detail, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(failure_id) DO NOTHING;
    """
    try:
        repo._queue.put_nowait(
            (
                sql,
                (
                    failure.get("failure_id", ""),
                    failure.get("candidate_id", ""),
                    failure.get("strategy_id", ""),
                    failure.get("generation_id", ""),
                    failure.get("stage", "DSL_VALIDATION"),
                    failure.get("reason", ""),
                    _json(failure.get("detail")),
                    failure.get("created_at", _now()),
                ),
            )
        )
        return True
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] record_failure failed", error=str(e))
        return False


def emit_event(repo: AuditRepository, event: dict[str, Any]) -> bool:
    if not repo._is_sqlite:
        return False
    sql = """
        INSERT INTO factory_events (
            event_id, generation_id, candidate_id, event_type, message,
            payload, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO NOTHING;
    """
    try:
        repo._queue.put_nowait(
            (
                sql,
                (
                    event.get("event_id", ""),
                    event.get("generation_id", ""),
                    event.get("candidate_id", ""),
                    event.get("event_type", "GENERIC"),
                    event.get("message", ""),
                    _json(event.get("payload")),
                    event.get("created_at", _now()),
                ),
            )
        )
        return True
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] emit_event failed", error=str(e))
        return False


def record_run(repo: AuditRepository, run: dict[str, Any]) -> bool:
    if not repo._is_sqlite:
        return False
    sql = """
        INSERT INTO factory_runs (
            run_id, generation_id, strategy_id, experiment_kind,
            executed_at, config, result_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO NOTHING;
    """
    try:
        repo._queue.put_nowait(
            (
                sql,
                (
                    run.get("run_id", ""),
                    run.get("generation_id", ""),
                    run.get("strategy_id", ""),
                    run.get("experiment_kind", "GENERATE"),
                    run.get("executed_at", _now()),
                    _json(run.get("config")),
                    _json(run.get("result_summary")),
                ),
            )
        )
        return True
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] record_run failed", error=str(e))
        return False


def record_provider_usage(repo: AuditRepository, usage: dict[str, Any]) -> bool:
    if not repo._is_sqlite:
        return False
    sql = """
        INSERT INTO factory_provider_usage (
            usage_id, generation_id, requests, failures, prompt_tokens,
            completion_tokens, total_tokens, estimated_cost_usd,
            last_latency_ms, last_error, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(usage_id) DO NOTHING;
    """
    try:
        repo._queue.put_nowait(
            (
                sql,
                (
                    usage.get("usage_id", ""),
                    usage.get("generation_id", ""),
                    int(usage.get("requests", 0)),
                    int(usage.get("failures", 0)),
                    int(usage.get("prompt_tokens", 0)),
                    int(usage.get("completion_tokens", 0)),
                    int(usage.get("total_tokens", 0)),
                    float(usage.get("estimated_cost_usd", 0.0)),
                    float(usage.get("last_latency_ms", 0.0)),
                    usage.get("last_error", ""),
                    usage.get("created_at", _now()),
                ),
            )
        )
        return True
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] record_provider_usage failed", error=str(e))
        return False


def set_loop_state(repo: AuditRepository, loop: dict[str, Any]) -> bool:
    if not repo._is_sqlite:
        return False
    sql = """
        INSERT INTO factory_loop_state (
            scope, state, generation_id, checkpoint, updated_at, last_error
        ) VALUES ('autonomous', ?, ?, ?, ?, ?)
        ON CONFLICT(scope) DO UPDATE SET
            state=excluded.state,
            generation_id=excluded.generation_id,
            checkpoint=excluded.checkpoint,
            updated_at=excluded.updated_at,
            last_error=excluded.last_error;
    """
    try:
        repo._queue.put_nowait(
            (
                sql,
                (
                    loop.get("state", "STOPPED"),
                    loop.get("generation_id", ""),
                    _json(loop.get("checkpoint")),
                    loop.get("updated_at", _now()),
                    loop.get("last_error", ""),
                ),
            )
        )
        return True
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] set_loop_state failed", error=str(e))
        return False


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_generation(repo: AuditRepository, generation_id: str) -> dict[str, Any] | None:
    conn = _conn(repo)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT * FROM factory_generations WHERE generation_id=?;", (generation_id,)
        ).fetchone()
        return _row_safe(dict(row)) if row else None
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] get_generation failed", error=str(e))
        return None
    finally:
        conn.close()


def list_generations(repo: AuditRepository, limit: int = 50) -> list[dict[str, Any]]:
    conn = _conn(repo)
    if conn is None:
        return []
    bounded = max(1, min(int(limit), MAX_READ_LIMIT))
    try:
        rows = conn.execute(
            "SELECT * FROM factory_generations ORDER BY number DESC LIMIT ?;", (bounded,)
        ).fetchall()
        return [_row_safe(dict(r)) for r in rows]
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] list_generations failed", error=str(e))
        return []
    finally:
        conn.close()


def list_candidates(
    repo: AuditRepository,
    generation_id: str | None = None,
    lifecycle: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    conn = _conn(repo)
    if conn is None:
        return []
    bounded = max(1, min(int(limit), MAX_READ_LIMIT))
    sql = "SELECT * FROM factory_candidates"
    clauses: list[str] = []
    args: list[Any] = []
    if generation_id:
        clauses.append("generation_id = ?")
        args.append(generation_id)
    if lifecycle:
        clauses.append("lifecycle = ?")
        args.append(lifecycle)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY population_index ASC LIMIT ?;"
    args.append(bounded)
    try:
        rows = conn.execute(sql, args).fetchall()
        return [_row_safe(dict(r)) for r in rows]
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] list_candidates failed", error=str(e))
        return []
    finally:
        conn.close()


def list_failures(
    repo: AuditRepository,
    generation_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    conn = _conn(repo)
    if conn is None:
        return []
    bounded = max(1, min(int(limit), MAX_READ_LIMIT))
    sql = "SELECT * FROM factory_failures"
    args: list[Any] = []
    if generation_id:
        sql += " WHERE generation_id = ?"
        args.append(generation_id)
    sql += " ORDER BY created_at DESC LIMIT ?;"
    args.append(bounded)
    try:
        rows = conn.execute(sql, args).fetchall()
        return [_row_safe(dict(r)) for r in rows]
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] list_failures failed", error=str(e))
        return []
    finally:
        conn.close()


def list_events(
    repo: AuditRepository,
    generation_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    conn = _conn(repo)
    if conn is None:
        return []
    bounded = max(1, min(int(limit), MAX_READ_LIMIT))
    sql = "SELECT * FROM factory_events"
    args: list[Any] = []
    if generation_id:
        sql += " WHERE generation_id = ?"
        args.append(generation_id)
    sql += " ORDER BY created_at DESC LIMIT ?;"
    args.append(bounded)
    try:
        rows = conn.execute(sql, args).fetchall()
        return [_row_safe(dict(r)) for r in rows]
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] list_events failed", error=str(e))
        return []
    finally:
        conn.close()


def list_runs(repo: AuditRepository, limit: int = 100) -> list[dict[str, Any]]:
    conn = _conn(repo)
    if conn is None:
        return []
    bounded = max(1, min(int(limit), 500))
    try:
        rows = conn.execute(
            "SELECT * FROM factory_runs ORDER BY executed_at DESC LIMIT ?;", (bounded,)
        ).fetchall()
        return [_row_safe(dict(r)) for r in rows]
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] list_runs failed", error=str(e))
        return []
    finally:
        conn.close()


def get_loop_state(repo: AuditRepository) -> dict[str, Any]:
    conn = _conn(repo)
    if conn is None:
        return {"state": "STOPPED"}
    try:
        row = conn.execute(
            "SELECT * FROM factory_loop_state WHERE scope='autonomous' LIMIT 1;"
        ).fetchone()
        return _row_safe(dict(row)) if row else {"state": "STOPPED"}
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] get_loop_state failed", error=str(e))
        return {"state": "STOPPED"}
    finally:
        conn.close()


def provider_usage_total(repo: AuditRepository) -> dict[str, Any]:
    conn = _conn(repo)
    if conn is None:
        return {"requests": 0, "estimated_cost_usd": 0.0}
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(requests),0) AS req, COALESCE(SUM(failures),0) AS fail, "
            "COALESCE(SUM(total_tokens),0) AS toks, "
            "COALESCE(SUM(estimated_cost_usd),0.0) AS cost FROM factory_provider_usage;"
        ).fetchone()
        return {
            "requests": int(row["req"] or 0),
            "failures": int(row["fail"] or 0),
            "total_tokens": int(row["toks"] or 0),
            "estimated_cost_usd": round(float(row["cost"] or 0.0), 4),
        }
    except Exception as e:
        logger.error("[STRATEGY_FACTORY] provider_usage_total failed", error=str(e))
        return {"requests": 0, "estimated_cost_usd": 0.0}
    finally:
        conn.close()


def _row_safe(row: dict[str, Any]) -> dict[str, Any]:
    """Normalizes JSON-text columns ('' / 'null' -> '{}') for UI safety."""
    out = dict(row)
    for col in (
        "config",
        "parent_ids",
        "dsl",
        "structural",
        "failure_reasons",
        "detail",
        "payload",
        "result_summary",
        "checkpoint",
    ):
        if col in out:
            raw = out[col]
            if raw is None:
                out[col] = "{}"
            else:
                text = str(raw).strip()
                if text == "" or text.lower() == "null":
                    out[col] = "{}"
    return out


__all__ = [
    "emit_event",
    "get_generation",
    "get_loop_state",
    "list_candidates",
    "list_events",
    "list_failures",
    "list_generations",
    "list_runs",
    "provider_usage_total",
    "record_failure",
    "record_provider_usage",
    "record_run",
    "set_loop_state",
    "upsert_candidate",
    "upsert_generation",
]