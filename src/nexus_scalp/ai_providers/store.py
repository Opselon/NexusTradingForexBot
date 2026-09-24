"""Provider decision persistence (ECOSYSTEM-001, Sections 41, 58, 63).

What this is
------------
Every final AI-provider decision is recorded durably so a historical decision
can be answered completely (Section 64): which provider, which model, which
configuration, what the snapshot was, what internal ML said, what the external
provider said, what the policy scored, what the risk gate allowed, whether
fallback was used, and — later — what MT5 actually did.

Provider parity (SQLite AND PostgreSQL)
---------------------------------------
The table is authored ONCE in the SQLite dialect and provisioned on PostgreSQL
by the repo's own translation path (``database/migration/pg_schema.translate_ddl``)
so the physical schema is identical on both providers. This deliberately reuses
the existing fabric rather than introducing a second persistence system
(Section 60: "Do not introduce another model registry if an existing canonical
registry exists").

On SQLite the store uses one dedicated writer connection (WAL single-writer),
the same model as ``AuditRepository``. On PostgreSQL it uses the fabric's pooled
write backend via ``get_domain_backend``/``provision_domain``.

Retention (Section 63)
----------------------
``_max_rows`` bounds the table. Pruning deletes the OLDEST decisions by id, so
unbounded growth is impossible and old raw provider content does not live
forever. The prune runs inline on the writer thread after the insert commits.

Safety
------
* The provider decision is recorded AS evidence. Nothing here can issue an
  order, and nothing is read back into the decide path as authoritative.
* ``payload`` is the minimal canonical snapshot (Section 8): no secrets, no
  broker credentials, no filesystem paths.
* Writes are fail-closed for the CALLER but never raise into the decide path:
  a persistence failure records a warning and returns, so recording can never
  block or change a decision (the store is an observer, not a participant).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nexus_scalp.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger("nexus_scalp.ai_providers.store")

#: Domain name registered with the DB fabric for PostgreSQL provisioning.
DECISION_DOMAIN = "ai_provider_decisions"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS ai_provider_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decision_mode TEXT NOT NULL,
    active_provider TEXT NOT NULL,
    active_model TEXT,
    final_action TEXT NOT NULL,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT,
    providers_used TEXT NOT NULL,
    providers_failed TEXT NOT NULL,
    p_hold REAL,
    p_close REAL,
    p_reduce REAL,
    winner_score REAL,
    confidence REAL,
    latency_ms REAL,
    symbol TEXT,
    ticket INTEGER,
    snapshot_id TEXT,
    template_version TEXT,
    policy_version TEXT,
    contract_version TEXT,
    is_test_data INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_apd_decision_id ON ai_provider_decisions(decision_id);
CREATE INDEX IF NOT EXISTS idx_apd_created_at ON ai_provider_decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_apd_provider ON ai_provider_decisions(active_provider);
"""

_INSERT = """
INSERT INTO ai_provider_decisions (
    decision_id, created_at, decision_mode, active_provider, active_model,
    final_action, fallback_used, fallback_reason, providers_used,
    providers_failed, p_hold, p_close, p_reduce, winner_score, confidence,
    latency_ms, symbol, ticket, snapshot_id, template_version, policy_version,
    contract_version, is_test_data, payload
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

_PRUNE = "DELETE FROM ai_provider_decisions WHERE id NOT IN (SELECT id FROM ai_provider_decisions ORDER BY id DESC LIMIT ?)"


class ProviderDecisionStore:
    """Durable record of every final provider decision, on either DB provider."""

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        dsn: str | None = None,
        max_rows: int = 5_000,
        provision: bool = True,
    ) -> None:
        """One store per process.

        Exactly one of ``db_path`` (SQLite) or ``dsn`` (PostgreSQL) is used.
        When neither is supplied the store degrades to in-memory recording so
        the orchestrator never fails to boot on a misconfigured box.
        """
        self._lock = threading.RLock()
        self._max_rows = max_rows
        self._dsn = dsn
        self._sqlite: sqlite3.Connection | None = None
        self._closed = False
        if dsn is not None:
            self._pg: Any = None
            if provision:
                self._pg = self._provision_pg(dsn)
        elif db_path is not None:
            self._sqlite = self._open_sqlite(Path(db_path))
        else:
            # In-memory fallback: the engine still runs, decisions are not lost
            # to the process, and the box logs why durable storage is absent.
            logger.warning("[AI-PROV] no db_path/dsn given; decisions stay in-memory")
            self._mem: list[dict[str, Any]] = []

    # -- construction ---------------------------------------------------------

    @staticmethod
    def _open_sqlite(path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.executescript(_SCHEMA)
        return conn

    @staticmethod
    def _provision_pg(dsn: str) -> Any:
        try:
            from nexus_scalp.database.fabric import get_domain_backend, provision_domain

            backend = get_domain_backend(DECISION_DOMAIN, readonly=False)
            if backend is not None:
                return backend
            return provision_domain(DECISION_DOMAIN, dsn, min_size=1, max_size=4)
        except Exception as exc:  # noqa: BLE001 - provisioning must never block boot
            logger.error("[AI-PROV] %s domain provisioning failed: %s", DECISION_DOMAIN, exc)
            return None

    # -- writing --------------------------------------------------------------

    def record(self, outcome: dict[str, Any]) -> None:
        """Record one final decision. Never raises into the caller.

        ``outcome`` is the orchestrator's ``DecisionOutcome.to_dict()``. Extra
        keys are preserved verbatim inside ``payload`` (the full evidence
        record) so the audit trail is complete without schema churn.
        """
        try:
            with self._lock:
                if self._closed:
                    return
                row = self._row(outcome)
                if self._sqlite is not None:
                    self._sqlite.execute(_INSERT, row)
                    self._prune_sqlite()
                elif self._dsn is not None:
                    self._write_pg(row)
                else:
                    self._mem.insert(0, {**outcome, "_row": row})
                    del self._mem[self._max_rows :]
        except Exception as exc:  # noqa: BLE001 - observer must never break the decide path
            logger.warning("[AI-PROV] failed to record decision %s: %s", outcome.get("decision_id"), exc)

    def _row(self, outcome: dict[str, Any]) -> tuple[Any, ...]:
        decision = outcome.get("decision", {}) if isinstance(outcome.get("decision"), dict) else {}
        providers_used = outcome.get("providers_used") or []
        providers_failed = outcome.get("providers_failed") or []
        versions = outcome.get("versions", {}) or {}
        policy = outcome.get("policy", {}) if isinstance(outcome.get("policy"), dict) else {}
        winner = policy.get("winner") or outcome.get("final_action")
        return (
            outcome.get("decision_id", ""),
            outcome.get("created_at", ""),
            outcome.get("decision_mode", ""),
            outcome.get("active_provider") or outcome.get("provider", ""),
            outcome.get("active_model"),
            str(outcome.get("final_action", "")),
            1 if outcome.get("fallback_used") else 0,
            outcome.get("fallback_reason"),
            json.dumps(providers_used),
            json.dumps(providers_failed),
            decision.get("p_hold"),
            decision.get("p_close"),
            decision.get("p_reduce"),
            policy.get("scores", {}).get(winner),
            decision.get("confidence"),
            outcome.get("latency_ms"),
            outcome.get("symbol"),
            outcome.get("ticket"),
            outcome.get("snapshot_id"),
            versions.get("template"),
            versions.get("policy"),
            versions.get("contract"),
            1 if outcome.get("is_test_data") else 0,
            json.dumps(outcome, default=str, separators=(",", ":")),
        )

    def _prune_sqlite(self) -> None:
        assert self._sqlite is not None
        self._sqlite.execute(_PRUNE, (self._max_rows,))

    def _write_pg(self, row: tuple[Any, ...]) -> None:
        backend = getattr(self, "_pg", None)
        if backend is None:
            return
        # The fabric's pooled backend translates placeholders itself.
        sql = _INSERT.replace("?", "%s")
        cursor = None
        try:
            conn = backend.conn() if hasattr(backend, "conn") else backend.connection()
            cursor = conn.cursor()
            cursor.execute(sql, row)
            cursor.execute(_PRUNE.replace("?", "%s"), (self._max_rows,))
            conn.commit()
        finally:
            if cursor is not None:
                cursor.close()

    # -- reading --------------------------------------------------------------

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Newest decisions first, as stored (payload still JSON)."""
        with self._lock:
            if self._sqlite is not None:
                cur = self._sqlite.execute(
                    "SELECT * FROM ai_provider_decisions ORDER BY id DESC LIMIT ?", (limit,)
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
            if self._dsn is not None:
                return self._read_pg(limit)
            return [dict(o) for o in self._mem[:limit]]

    def _read_pg(self, limit: int) -> list[dict[str, Any]]:
        backend = getattr(self, "_pg", None)
        if backend is None:
            return []
        cursor = None
        try:
            conn = backend.conn() if hasattr(backend, "conn") else backend.connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM ai_provider_decisions ORDER BY id DESC LIMIT %s", (limit,))
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, r)) for r in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AI-PROV] decision read failed: %s", exc)
            return []
        finally:
            if cursor is not None:
                cursor.close()

    def get(self, decision_id: str) -> dict[str, Any] | None:
        """One decision by id, as ``DecisionRecord``-shaped dict (or ``None``)."""
        rows = self.recent(self._max_rows)
        for row in rows:
            if row.get("decision_id") == decision_id:
                payload = row.get("payload")
                if isinstance(payload, str):
                    row["payload"] = json.loads(payload)
                return self._shape(row)
        return None

    # -- contract-shaped reads (API / UI / CLI) -------------------------------

    def list_recent(self, limit: int = 25) -> list[dict[str, Any]]:
        """Recent decisions as ``DecisionRecord``-shaped dicts.

        The stored row is flat + a JSON ``payload``; this restores the contract
        shape (``decision``/``policy``/``risk``/``evidence``/``versions``) so
        the UI and API consumers read exactly what ``to_dict()`` produced.
        """
        return [self._shape(r) for r in self.recent(limit)]

    @staticmethod
    def _shape(row: dict[str, Any]) -> dict[str, Any]:
        # ``recent()`` returns the payload as a JSON *string* (``get()`` decodes
        # it); decode here so both read paths yield the contract shape.
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        full = payload if isinstance(payload, dict) else {}

        def _j(key: str, default: Any) -> Any:
            v = row.get(key)
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except json.JSONDecodeError:
                    return default
            return v if v is not None else default

        providers_used = _j("providers_used", [])
        providers_failed = _j("providers_failed", [])
        policy_payload = full.get("policy", {}) if isinstance(full.get("policy"), dict) else {}
        risk_payload = full.get("risk", {}) if isinstance(full.get("risk"), dict) else {}
        decision_payload = (
            full.get("decision", {}) if isinstance(full.get("decision"), dict) else {}
        )
        versions = full.get("versions", {}) if isinstance(full.get("versions"), dict) else {}
        # Usage is carried inside evidence (Section 22); pick the first
        # provider that reported it so the UI shows real token cost.
        usage: dict[str, Any] = {}
        evidence = full.get("evidence") if isinstance(full.get("evidence"), dict) else {}
        for prov in providers_used:
            ev = evidence.get(prov)
            if isinstance(ev, dict) and isinstance(ev.get("usage"), dict):
                usage = ev["usage"]
                break
        return {
            "decision_id": row.get("decision_id", ""),
            "final_action": row.get("final_action", ""),
            "policy": policy_payload or {"scores": {}, "winner": row.get("final_action")},
            "risk": risk_payload or {"allowed": True, "rejections": []},
            "evidence": evidence,
            "providers_used": providers_used if isinstance(providers_used, list) else [],
            "providers_failed": providers_failed if isinstance(providers_failed, list) else [],
            "fallback_used": bool(row.get("fallback_used")),
            "fallback_reason": row.get("fallback_reason") or "",
            "decision_mode": row.get("decision_mode", ""),
            "versions": versions,
            "created_at": row.get("created_at", ""),
            "latency_ms": row.get("latency_ms") or 0.0,
            "stage_timings_ms": full.get("stage_timings_ms", {}),
            "is_test_data": bool(row.get("is_test_data")),
            # Denormalized hot columns — one query instead of JSON parsing for
            # the UI table.
            "active_provider": row.get("active_provider"),
            "active_model": row.get("active_model"),
            "symbol": row.get("symbol"),
            "ticket": row.get("ticket"),
            "p_hold": decision_payload.get("p_hold"),
            "p_close": decision_payload.get("p_close"),
            "p_reduce": decision_payload.get("p_reduce"),
            "confidence": decision_payload.get("confidence"),
            "usage": usage,
        }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._sqlite is not None:
                self._sqlite.close()
                self._sqlite = None
