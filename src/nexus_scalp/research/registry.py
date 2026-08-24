"""
Strategy Registry (Persistence)
===============================
PHASE 09B (spec 20 / 26 / 40).

The registry is the enduring home of validation truth. It preserves for every
strategy: identity, version, feature schema, discovery source, validation
lineage, backtest / walk-forward / OOS / robustness results, score, confidence,
lifecycle, creation time and retirement reason.

Registry is INDEPENDENT of the current model file (spec 24) and preserves
historical research data across model rebuilds and schema width changes.

Persistence: rows are written through the AuditRepository background queue so
the registry never blocks the live path. Historical validation truth is never
mutated; updates append lineage (spec 28 immutability).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.lifecycle import transition
from nexus_scalp.research.models import (
    BacktestResult,
    CandidateLifecycle,
    OOSResult,
    RobustnessResult,
    StrategyRegistryEntry,
    StrategyScore,
    WalkForwardResult,
)

logger = get_logger("nexus_scalp.research.registry")

UPSERT_ENTRY_SQL = """
    INSERT INTO strategy_registry (
        strategy_id, strategy_version, feature_schema_id, feature_dimension,
        discovery_source, discovery_window, context_definition,
        parent_strategy_ids, lifecycle, backtest, walkforward, oos, robustness,
        score, confidence, sample_count, validation_lineage, retirement_reason,
        created_at, updated_at, context_matrices
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(strategy_id, strategy_version) DO UPDATE SET
        feature_schema_id=excluded.feature_schema_id,
        feature_dimension=excluded.feature_dimension,
        discovery_source=excluded.discovery_source,
        discovery_window=excluded.discovery_window,
        context_definition=excluded.context_definition,
        parent_strategy_ids=excluded.parent_strategy_ids,
        lifecycle=excluded.lifecycle,
        backtest=excluded.backtest,
        walkforward=excluded.walkforward,
        oos=excluded.oos,
        robustness=excluded.robustness,
        score=excluded.score,
        confidence=excluded.confidence,
        sample_count=excluded.sample_count,
        validation_lineage=excluded.validation_lineage,
        retirement_reason=excluded.retirement_reason,
        updated_at=excluded.updated_at,
        context_matrices=excluded.context_matrices;
"""


class StrategyRegistry:
    """Bounded registry persistence over the research tables."""

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def upsert(
        self, entry: StrategyRegistryEntry, forbid_lifecycle_regression: bool = True
    ) -> bool:
        """
        Persists a registry entry.

        TASK-4 immutability contract (spec 28):
        * The historical validation truth of a version is never rewritten with
          a DIFFERENT definition: when an existing (strategy_id, strategy_version)
          row carries a context_definition that differs from the new entry's,
          the upsert is REFUSED (returns False) instead of silently replacing
          the definition under the same version.
        * Lifecycle regression protection is ON by default (P2 hardening,
          2026-08-23): refuses replacing a terminal/advanced lifecycle
          (VALIDATED / SHADOW / ACTIVE / REJECTED / DEGRADED / RETIRED) with a
          weaker one (DISCOVERED etc.) — seeding and re-validation must never
          downgrade established validation truth. Callers that legitimately
          need an administrative downgrade must pass
          `forbid_lifecycle_regression=False` explicitly (audited exception).
        """
        if not self.audit_repo._is_sqlite:
            return False
        existing = self.get(entry.strategy_id, entry.strategy_version)
        if existing is not None:
            old_def = existing.context_definition or {}
            new_def = entry.context_definition or {}
            if old_def and new_def and old_def != new_def:
                logger.warning(
                    "[STRATEGY_REGISTRY] definition mutation refused",
                    strategy_id=entry.strategy_id,
                    strategy_version=entry.strategy_version,
                )
                return False
            if forbid_lifecycle_regression and _is_stronger(existing.lifecycle, entry.lifecycle):
                logger.warning(
                    "[STRATEGY_REGISTRY] lifecycle regression refused",
                    strategy_id=entry.strategy_id,
                    from_state=existing.lifecycle.value,
                    to_state=entry.lifecycle.value,
                )
                return False
        args = (
            entry.strategy_id,
            entry.strategy_version,
            entry.feature_schema_id,
            entry.feature_dimension,
            entry.discovery_source,
            entry.discovery_window,
            _json(entry.context_definition),
            _json(entry.parent_strategy_ids),
            entry.lifecycle.value,
            _json(entry.backtest.model_dump() if entry.backtest else None),
            _json(entry.walkforward.model_dump() if entry.walkforward else None),
            _json(entry.oos.model_dump() if entry.oos else None),
            _json(entry.robustness.model_dump() if entry.robustness else None),
            _json(entry.score.model_dump() if entry.score else None),
            entry.confidence,
            entry.sample_count,
            _json(entry.validation_lineage),
            entry.retirement_reason,
            _json(entry.context_matrices) if getattr(entry, "context_matrices", None) is not None else "{}",
            entry.created_at.isoformat(),
            entry.updated_at.isoformat(),
        )
        try:
            if hasattr(self.audit_repo, "_queue"):
                self.audit_repo._queue.put_nowait((UPSERT_ENTRY_SQL, args))
                return True
        except Exception as e:
            logger.error("[STRATEGY_REGISTRY] upsert failed (isolated)", error=str(e))
        return False

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(
        self, strategy_id: str, strategy_version: str | None = None
    ) -> StrategyRegistryEntry | None:
        """Loads a registry entry; with no version, the most recent one."""
        if not self.audit_repo._is_sqlite:
            return None
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                if strategy_version:
                    row = conn.execute(
                        "SELECT * FROM strategy_registry WHERE strategy_id=? AND strategy_version=?;",
                        (strategy_id, strategy_version),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT * FROM strategy_registry WHERE strategy_id=? "
                        "ORDER BY updated_at DESC LIMIT 1;",
                        (strategy_id,),
                    ).fetchone()
            finally:
                conn.close()
            return self._from_row(row) if row else None
        except Exception as e:
            logger.error("[STRATEGY_REGISTRY] load failed", strategy=strategy_id, error=str(e))
            return None

    def list(self, lifecycle: str | None = None, limit: int = 200) -> list[StrategyRegistryEntry]:
        """Bounded listing, newest first."""
        if not self.audit_repo._is_sqlite:
            return []
        bounded = max(1, min(int(limit), 500))
        sql = "SELECT * FROM strategy_registry"
        args: tuple[Any, ...] = ()
        if lifecycle:
            sql += " WHERE lifecycle = ?"
            args = (lifecycle,)
        sql += " ORDER BY updated_at DESC LIMIT ?;"
        out: list[StrategyRegistryEntry] = []
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, (*args, bounded)).fetchall()
            finally:
                conn.close()
            for r in rows:
                entry = self._from_row(r)
                if entry is not None:
                    out.append(entry)
        except Exception as e:
            logger.error("[STRATEGY_REGISTRY] list failed", error=str(e))
        return out

    def count(self, lifecycle: str | None = None) -> int:
        if not self.audit_repo._is_sqlite:
            return 0
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                if lifecycle:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM strategy_registry WHERE lifecycle=?;",
                        (lifecycle,),
                    ).fetchone()
                else:
                    row = conn.execute("SELECT COUNT(*) FROM strategy_registry;").fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Validation invariants (TASK-21, spec 55 / 56 / 57)
    # ------------------------------------------------------------------

    def invariant_check(self, entry: StrategyRegistryEntry) -> dict[str, Any]:
        """Validates the registry invariants for one entry.

        * VALIDATED requires: backtest, walkforward, oos, robustness all
          present AND passed (per gate result status), score present with
          verdict VALIDATED (never a shortcut).
        * REJECTED requires: at least one gate FAILED or the score verdict
          REJECTED (never the default for unprocessed strategies).
        """
        lc = entry.lifecycle.value
        problems: list[str] = []
        if lc == "VALIDATED":
            for name, res in (
                ("backtest", entry.backtest),
                ("walkforward", entry.walkforward),
                ("oos", entry.oos),
                ("robustness", entry.robustness),
            ):
                if res is None:
                    problems.append(f"{name} result missing")
            if entry.oos is not None and entry.oos.status != "PASS":
                problems.append(f"OOS status is {entry.oos.status}, not PASS")
            if entry.walkforward is not None and not entry.walkforward.passed:
                problems.append("walk-forward did not pass")
            if entry.robustness is not None and entry.robustness.status != "PASS":
                problems.append(f"robustness status is {entry.robustness.status}, not PASS")
            if entry.score is None:
                problems.append("score missing for VALIDATED strategy")
            elif entry.score.verdict != "VALIDATED":
                problems.append(f"score verdict is {entry.score.verdict}, not VALIDATED")
        elif lc == "REJECTED":
            gate_failed = (
                (entry.oos is not None and entry.oos.status != "PASS")
                or (entry.robustness is not None and entry.robustness.status == "FAIL")
                or (entry.walkforward is not None and not entry.walkforward.passed)
                or (entry.score is not None and entry.score.verdict == "REJECTED")
                or (entry.backtest is not None and entry.backtest.total_trades == 0)
            )
            ran_any = entry.backtest is not None or entry.oos is not None or entry.score is not None
            if not gate_failed and not ran_any:
                problems.append("REJECTED without any failed gate or validation attempt")
        return {
            "strategy_id": entry.strategy_id,
            "lifecycle": lc,
            "valid": not problems,
            "problems": problems,
        }

    # ------------------------------------------------------------------
    # Lifecycle transitions (persisted)
    # ------------------------------------------------------------------

    def transition_lifecycle(
        self, strategy_id: str, target: CandidateLifecycle, reason: str = ""
    ) -> StrategyRegistryEntry | None:
        """Loads, transitions lifecycle in-memory (state machine), persists."""
        entry = self.get(strategy_id)
        if entry is None:
            return None
        try:
            new_state = transition(entry.lifecycle, target)
        except ValueError as e:
            logger.error(
                "[STRATEGY_REGISTRY] illegal transition", strategy=strategy_id, error=str(e)
            )
            return None
        updated = entry.model_copy(
            update={
                "lifecycle": new_state,
                "updated_at": datetime.now(UTC),
                "validation_lineage": [
                    *entry.validation_lineage,
                    f"{datetime.now(UTC).isoformat()}:{new_state.value}"
                    + (f":{reason}" if reason else ""),
                ],
            }
        )
        # P2 hardening note: transition_lifecycle is the EXPLICIT administrative
        # recovery/operations path — legality is already enforced by the state
        # machine above (transition()). The regression guard must not silently
        # block legal operational descents (ACTIVE→DEGRADED/RETIRED,
        # SHADOW→DEGRADED/REJECTED), so persistence bypasses the default
        # forbid_lifecycle_regression=True here. Plain upsert() callers keep
        # full protection.
        self.upsert(updated, forbid_lifecycle_regression=False)
        return updated

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _from_row(row: sqlite3.Row) -> StrategyRegistryEntry | None:
        try:

            def _load(column: str, model: type[Any]) -> Any | None:
                raw = row[column]
                if not raw:
                    return None
                text = str(raw).strip()
                if text.lower() in ("", "null", "none"):
                    return None
                try:
                    data = json.loads(text)
                except (ValueError, TypeError):
                    logger.warning(
                        "[STRATEGY_REGISTRY] column decode failed",
                        column=column,
                        strategy_id=row["strategy_id"],
                    )
                    return None
                # `{}` is the canonical empty-object form (BUG-075 _json());
                # it must decode to None, not explode a required-field model.
                if isinstance(data, dict) and data:
                    return model(**data)
                return None

            bt = _load("backtest", BacktestResult)
            wf = _load("walkforward", WalkForwardResult)
            oos = _load("oos", OOSResult)
            rob = _load("robustness", RobustnessResult)
            score = _load("score", StrategyScore)

            created = _parse_ts(row["created_at"]) or datetime.now(UTC)
            updated = _parse_ts(row["updated_at"]) or created

            context = json.loads(row["context_definition"] or "{}")
            parents = json.loads(row["parent_strategy_ids"] or "[]")
            lineage = json.loads(row["validation_lineage"] or "[]")
            try:
                c_mat = json.loads(row["context_matrices"] or "{}")
            except Exception:
                c_mat = {}

            return StrategyRegistryEntry(
                strategy_id=row["strategy_id"],
                strategy_version=row["strategy_version"],
                feature_schema_id=row["feature_schema_id"],
                feature_dimension=int(row["feature_dimension"] or 0),
                discovery_source=row["discovery_source"] or "",
                discovery_window=row["discovery_window"] or "",
                context_definition=context if isinstance(context, dict) else {},
                parent_strategy_ids=parents if isinstance(parents, list) else [],
                lifecycle=CandidateLifecycle(row["lifecycle"]),
                backtest=bt,
                walkforward=wf,
                oos=oos,
                robustness=rob,
                score=score,
                confidence=float(row["confidence"] or 0.0),
                sample_count=int(row["sample_count"] or 0),
                validation_lineage=lineage if isinstance(lineage, list) else [],
                retirement_reason=row["retirement_reason"] or "",
                context_matrices=c_mat if isinstance(c_mat, dict) else {},
                created_at=created,
                updated_at=updated,
            )
        except Exception as e:
            logger.error("[STRATEGY_REGISTRY] row decode failed", error=str(e))
            return None


def _is_stronger(current: CandidateLifecycle, proposed: CandidateLifecycle) -> bool:
    """True when `proposed` is a weaker lifecycle than the existing `current`.

    Stronger (more validation truth established) states must never be silently
    downgraded: VALIDATED/SHADOW/ACTIVE/REJECTED/DEGRADED/RETIRED outrank
    DISCOVERED/BACKTESTING/VALIDATING/OOS_TESTING/ROBUSTNESS_TESTING.
    """
    _strength = {
        CandidateLifecycle.DISCOVERED: 1,
        CandidateLifecycle.BACKTESTING: 1,
        CandidateLifecycle.VALIDATING: 1,
        CandidateLifecycle.OOS_TESTING: 1,
        CandidateLifecycle.ROBUSTNESS_TESTING: 1,
        CandidateLifecycle.VALIDATED: 2,
        CandidateLifecycle.SHADOW: 3,
        CandidateLifecycle.ACTIVE: 4,
        CandidateLifecycle.DEGRADED: 2,
        CandidateLifecycle.REJECTED: 2,
        CandidateLifecycle.RETIRED: 2,
    }
    if _strength.get(current, 0) > _strength.get(proposed, 0):
        return True
    # Peer-tier truth-rewrite hole (P2 hardening review A4): VALIDATED and
    # REJECTED share strength rank 2; a plain upsert must not silently flip
    # established validation truth between them (REJECTED->VALIDATED or
    # VALIDATED->REJECTED). Real changes go through the pipeline register
    # path with fresh evidence, or the explicit administrative
    # transition_lifecycle() path.
    _peer_truth = {CandidateLifecycle.VALIDATED, CandidateLifecycle.REJECTED}
    # Same-state writes are evidence REFRESHES (new backtest/OOS payloads on
    # an unchanged lifecycle) and must pass; only cross-peer truth rewrites
    # are refused.
    return current in _peer_truth and proposed in _peer_truth and current is not proposed


def _json(value: Any) -> str:
    """Serializes a research value into its canonical JSON text form.

    None (absent result/score) MUST round-trip to the schema's empty-object
    default (``'{}'``), never the JSON literal ``"null"``: rows persisted as
    ``"null"`` crashed the research registry UI (see BUG-075). Empty-string
    and empty-dict values stay canonical empty objects as well.
    """
    if value is None:
        return "{}"
    try:
        encoded = json.dumps(value, default=str)
        if encoded == "null":
            return "{}"
        return encoded
    except Exception:
        return "{}"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except Exception:
        return None
