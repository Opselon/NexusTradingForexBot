"""
Deterministic Research Dataset Builder
======================================
PHASE 09B consumes the immutable Phase 08 experience ledger (NOT a parallel
trade database) and produces a causally-safe `ResearchDataset`.

Guarantees (spec 5 / 6 / 7):
  * Every sample preserves decision_timestamp, experience_id, symbol, timeframe,
    strategy_id/version, feature_schema_id/dimension, regime, session, context,
    risk, execution and outcome provenance.
  * Causal ordering is preserved: samples are sorted by decision_timestamp.
  * Only EXECUTED + CLOSED experiences enter research.
  * Leakage guard: `build(as_of=...)` never includes samples whose DECISION
    happened at or after `as_of` (future outcomes can never be used).

TASK-4 (data-integrity forensics) additions:
  * Explicit sample eligibility audit: every closed outcome is classified
    ELIGIBLE or REJECTED with an exact rejection reason (never a blanket
    LOW_EVIDENCE). The rejection taxonomy covers missing outcome, zero
    substitution (UNKNOWN-R), invalid PnL, invalid risk, missing context,
    future leakage, invalid timestamp, schema mismatch and malformed data.
  * UNKNOWN is not ZERO: outcome rows whose broker result is missing
    (reconstruction_source NONE / no broker outcome) are REJECTED as
    zero-substituted evidence rather than silently entering research as
    realized_r == 0.0 (BUG-046 / BUG-045 pattern). A genuinely recorded zero
    (authoritative broker reconstruction of a break-even) stays eligible.
  * `audit()` exposes a per-trade_id rejection ledger so the API and
    dashboard can explain WHY the registry is empty.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.lifecycle import (
    DEGRADED_TERMINAL_STATES,
    NON_TRADE_TERMINAL_STATES,
    RECOVERY_SOURCE_BROKER_HISTORY,
    DecisionLifecycle,
    lifecycle_from_outcome,
)
from nexus_scalp.experience.models import ExperienceRecord
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.models import ResearchDataset, ResearchSample

logger = get_logger("nexus_scalp.research.dataset")

# ---------------------------------------------------------------------------
# Rejection taxonomy (TASK-4). Each reason maps to a deterministic check.
# P0-C (BUG-140): MISSING_OUTCOME now means "no outcome AND no known terminal
# state" — i.e. genuinely unresolved evidence. Terminal non-trade states are
# classified by their exact lifecycle (CANCELED_UNFILLED / EXPIRED_UNFILLED /
# REJECTED_UNFILLED / REPLACED_UNFILLED / EXECUTION_FAILED / NOT_DISPATCHED)
# and FILLED-but-result-lost is FILLED_OUTCOME_MISSING (recovery queue).
# ---------------------------------------------------------------------------
REASON_MISSING_OUTCOME = "MISSING_OUTCOME"
REASON_NOT_DISPATCHED = "NOT_DISPATCHED"
REASON_CANCELED_UNFILLED = "CANCELED_UNFILLED"
REASON_EXPIRED_UNFILLED = "EXPIRED_UNFILLED"
REASON_REJECTED_UNFILLED = "REJECTED_UNFILLED"
REASON_REPLACED_UNFILLED = "REPLACED_UNFILLED"
REASON_EXECUTION_FAILED = "EXECUTION_FAILED"
REASON_FILLED_OUTCOME_MISSING = "FILLED_OUTCOME_MISSING"
REASON_OUTCOME_PRECEDES_DECISION = "OUTCOME_PRECEDES_DECISION"
REASON_MISSING_REALIZED_R = "MISSING_REALIZED_R"  # UNKNOWN R recorded as zero
REASON_MISSING_REALIZED_PNL = "MISSING_REALIZED_PNL"  # UNKNOWN PnL recorded as zero
REASON_INVALID_PNL = "INVALID_PNL"  # non-finite PnL
REASON_INVALID_R = "INVALID_R"  # non-finite R
REASON_INVALID_INITIAL_RISK = "INVALID_INITIAL_RISK"  # stop distance unusable
REASON_MISSING_CONTEXT = "MISSING_CONTEXT"  # no strategy context snapshot
REASON_MISSING_FEATURE_SCHEMA = "MISSING_FEATURE_SCHEMA"
REASON_INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
REASON_FUTURE_LEAKAGE = "FUTURE_LEAKAGE"
REASON_SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
REASON_MALFORMED_PROVENANCE = "MALFORMED_PROVENANCE"
REASON_NON_FINITE_SAMPLE = "NON_FINITE_SAMPLE"

#: Lifecycle state -> dataset rejection reason (deterministic map).
_LIFECYCLE_REASON: dict[DecisionLifecycle, str] = {
    DecisionLifecycle.NOT_DISPATCHED: REASON_NOT_DISPATCHED,
    DecisionLifecycle.CANCELED_UNFILLED: REASON_CANCELED_UNFILLED,
    DecisionLifecycle.EXPIRED_UNFILLED: REASON_EXPIRED_UNFILLED,
    DecisionLifecycle.REJECTED_UNFILLED: REASON_REJECTED_UNFILLED,
    DecisionLifecycle.REPLACED_UNFILLED: REASON_REPLACED_UNFILLED,
    DecisionLifecycle.EXECUTION_FAILED: REASON_EXECUTION_FAILED,
    DecisionLifecycle.FILLED_OUTCOME_MISSING: REASON_FILLED_OUTCOME_MISSING,
}

#: Outcome reconstruction sources that carry authoritative broker truth.
#: Any other source (or none) means "no broker result captured" and the
#: realized fields are not trustworthy (they may be zero-substituted).
_AUTHORITATIVE_RECONSTRUCTION_SOURCES = frozenset(
    {"BROKER_DEALS", "BROKER_DEALS_AGGREGATED", "BROKER_NATIVE"}
)

#: Zero-R outcomes are treated as missing only when the broker result is also
#: missing. A genuinely recorded zero (authoritative reconstruction with a
#: real break-even result) is a legitimate sample.
_ZERO_R_TOL = 1e-9
_ZERO_PNL_TOL = 1e-6

#: Terminal NON-TRADE reasons: known, expected, permanent operational
#: evidence — they are counted once, never re-logged per dataset build.
_NON_TRADE_REASONS: frozenset[str] = frozenset(
    {
        REASON_NOT_DISPATCHED,
        REASON_CANCELED_UNFILLED,
        REASON_EXPIRED_UNFILLED,
        REASON_REJECTED_UNFILLED,
        REASON_REPLACED_UNFILLED,
        REASON_EXECUTION_FAILED,
    }
)

_RECOVERABLE_REASONS = frozenset(
    {
        REASON_MISSING_OUTCOME,
        REASON_MISSING_REALIZED_R,
        REASON_MISSING_REALIZED_PNL,
        REASON_FILLED_OUTCOME_MISSING,
    }
)

#: Deterministic, human-readable detail for terminal lifecycle states.
TERMINAL_DETAIL: dict[DecisionLifecycle, str] = {
    DecisionLifecycle.NOT_DISPATCHED: "decision never dispatched to broker",
    DecisionLifecycle.CANCELED_UNFILLED: "pending order canceled before any fill",
    DecisionLifecycle.EXPIRED_UNFILLED: "pending order expired before any fill",
    DecisionLifecycle.REJECTED_UNFILLED: "order rejected by broker before any fill",
    DecisionLifecycle.REPLACED_UNFILLED: "pending order replaced/superseded before any fill",
    DecisionLifecycle.EXECUTION_FAILED: "order dispatch failed (no broker ticket)",
    DecisionLifecycle.FILLED_OUTCOME_MISSING: "broker fill known, outcome result lost",
}

#: P0-E dataset contract: explicit, versioned eligibility rules that travel
#: with every dataset. A consumer can always answer "what entered research
#: and why was everything else excluded" without reading code.
ELIGIBILITY_RULES: dict[str, str] = {
    "contract_version": "p0e-bug140-1",
    "EXECUTED_CLOSED": "research eligible (realized R enters expectancy)",
    "CANCELED_UNFILLED": "excluded (terminal non-trade; lifecycle evidence only)",
    "EXPIRED_UNFILLED": "excluded (terminal non-trade; lifecycle evidence only)",
    "REJECTED_UNFILLED": "excluded (terminal non-trade; lifecycle evidence only)",
    "REPLACED_UNFILLED": "excluded (terminal non-trade; lifecycle evidence only)",
    "EXECUTION_FAILED": "excluded (terminal non-trade; lifecycle evidence only)",
    "NOT_DISPATCHED": "excluded (terminal non-trade; lifecycle evidence only)",
    "FILLED_OUTCOME_MISSING": "excluded pending recovery (recovery queue)",
    "MISSING_OUTCOME": "excluded (unresolved; recoverable finding)",
    "ZERO_SUBSTITUTED": "excluded (UNKNOWN broker result may not pass as R=0)",
    "fabricated_r": "forbidden - non-trade decisions never receive an R value",
}


def _sample_id(rec: ExperienceRecord) -> str:
    key = rec.idempotency_key or rec.experience_id
    return f"rs_{hashlib.sha256(key.encode()).hexdigest()[:16]}"


def _is_finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


class ResearchDatasetBuilder:
    """Builds deterministic research datasets from the experience ledger."""

    def __init__(self, ledger: ExperienceLedger) -> None:
        self.ledger = ledger
        #: idempotency_key -> reconstruction_source (loaded once per audit/build)
        self._source_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Provenance loading (bounded, one query per audit/build)
    # ------------------------------------------------------------------

    def _load_reconstruction_sources(self) -> dict[str, str]:
        """Loads broker reconstruction sources for all outcome rows.

        The typed merged record does not carry `broker_outcome` (Phase 14
        stores it in the persisted outcome payload), so the authoritative
        source is read directly from the outcome table. Bounded single query;
        called at most once per build/audit.
        """
        repo = self.ledger.audit_repo
        if not repo._is_sqlite:
            return {}
        out: dict[str, str] = {}
        try:
            conn = sqlite3.connect(repo._db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT idempotency_key, payload FROM audit_experience_outcomes "
                    "WHERE is_closed = 1 LIMIT 100000;"
                ).fetchall()
            finally:
                conn.close()
            for r in rows:
                try:
                    payload = json.loads(r["payload"] or "{}")
                    bo = payload.get("broker_outcome") or {}
                    src = bo.get("reconstruction_source", "") if isinstance(bo, dict) else ""
                except Exception:
                    src = ""
                out[str(r["idempotency_key"])] = str(src or "")
        except Exception as e:
            logger.warning("[STRATEGY_RESEARCH] reconstruction source load failed", error=str(e))
        return out

    def _reconstruction_source(self, rec: ExperienceRecord) -> str:
        if not self._source_cache:
            self._source_cache = self._load_reconstruction_sources()
        return self._source_cache.get(rec.idempotency_key, "")

    def _count_recovered_outcomes(self) -> int:
        """Counts outcomes recovered by the BUG-140 historical sweep.

        The sweep stamps RECOVERY_SOURCE_BROKER_HISTORY into
        correlation_detail (reconstructed trades) or lifecycle_detail
        (recovered terminal non-trades). Counted separately so
        consumers can weigh repaired history honestly instead of it
        blending invisibly into native outcomes.
        """
        repo = self.ledger.audit_repo
        if not repo._is_sqlite:
            return 0
        try:
            conn = sqlite3.connect(repo._db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT payload FROM audit_experience_outcomes "
                    "WHERE is_closed = 1 LIMIT 100000;"
                ).fetchall()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("[STRATEGY_RESEARCH] recovery marker load failed", error=str(e))
            return 0
        marker = RECOVERY_SOURCE_BROKER_HISTORY
        n = 0
        for r in rows:
            try:
                payload = json.loads(r["payload"] or "{}")
            except Exception:
                continue
            cd = str(payload.get("correlation_detail") or "")
            ld = str(payload.get("lifecycle_detail") or "")
            if cd.startswith(marker) or ld.startswith(marker):
                n += 1
        return n

    # ------------------------------------------------------------------
    # Sample conversion + eligibility
    # ------------------------------------------------------------------

    def evaluate_sample(self, rec: ExperienceRecord) -> tuple[bool, str, str]:
        """Deterministic eligibility audit for one closed experience.

        Returns (eligible, rejection_reason, detail). Never raises.

        P0-C (BUG-140): records WITHOUT an outcome are first classified by
        their terminal lifecycle (when known) instead of collapsing into a
        generic MISSING_OUTCOME. Terminal non-trades are ineligible for
        realized-R research (they are not trades) but are counted as explicit
        lifecycle evidence; FILLED_OUTCOME_MISSING enters the recovery queue.
        """
        # 1. Terminal lifecycle classification (outcome presence).
        state = lifecycle_from_outcome(
            is_executed=bool(rec.is_executed),
            is_closed=bool(rec.is_closed),
            exit_reason=getattr(rec, "exit_reason", "") or "",
            decision_lifecycle="",
        )
        if state in NON_TRADE_TERMINAL_STATES or state in DEGRADED_TERMINAL_STATES:
            reason = _LIFECYCLE_REASON[state]
            detail = TERMINAL_DETAIL.get(state, state.value)
            if state in DEGRADED_TERMINAL_STATES:
                detail = "broker fill known, outcome result lost -> recovery queue"
            return False, reason, detail
        if not rec.is_executed:
            return False, REASON_MISSING_OUTCOME, "not executed (terminal state unknown)"
        if not rec.is_closed:
            return False, REASON_MISSING_OUTCOME, "no recorded outcome"

        # 2. Causality.
        if rec.outcome_timestamp is None or rec.outcome_timestamp < rec.decision_timestamp:
            return False, REASON_OUTCOME_PRECEDES_DECISION, "outcome precedes decision"

        # 3. Non-finite guard (NaN/Inf never enter research).
        if not _is_finite(rec.realized_pnl_usd):
            return False, REASON_INVALID_PNL, f"pnl={rec.realized_pnl_usd!r}"
        if not _is_finite(rec.realized_r_multiple):
            return False, REASON_INVALID_R, f"r={rec.realized_r_multiple!r}"

        # 4. Zero-substitution audit: UNKNOWN broker result must NOT look like
        #    a real break-even zero. A zero R/PnL pair with no authoritative
        #    reconstruction source is a corrupted (missing) result.
        src = self._reconstruction_source(rec)
        authoritative = src in _AUTHORITATIVE_RECONSTRUCTION_SOURCES
        is_zero_r = abs(float(rec.realized_r_multiple)) < _ZERO_R_TOL
        is_zero_pnl = abs(float(rec.realized_pnl_usd)) < _ZERO_PNL_TOL
        if not authoritative and is_zero_r and is_zero_pnl:
            return (
                False,
                REASON_MISSING_REALIZED_R,
                f"zero-substituted outcome (reconstruction_source={src or 'NONE'})",
            )
        if not authoritative and is_zero_r and not is_zero_pnl:
            return (
                False,
                REASON_MISSING_REALIZED_R,
                f"R=0 with non-authoritative source {src or 'NONE'}",
            )
        if not authoritative and is_zero_pnl and not is_zero_r:
            return (
                False,
                REASON_MISSING_REALIZED_PNL,
                f"PnL=0 with non-authoritative source {src or 'NONE'}",
            )

        # 5. Initial risk sanity (planned stop distance).
        risk = rec.planned_risk_distance
        if not (_is_finite(risk) and risk > 1e-9):
            return False, REASON_INVALID_INITIAL_RISK, f"risk_distance={risk!r}"

        # 6. Context presence.
        if rec.context is None:
            return False, REASON_MISSING_CONTEXT, "no strategy context"
        if not (rec.context.symbol and rec.context.regime):
            return False, REASON_MISSING_CONTEXT, "incomplete context tokens"

        # 7. Feature schema provenance.
        if not rec.feature_schema_id or rec.feature_dimension <= 0:
            return False, REASON_MISSING_FEATURE_SCHEMA, "schema id/dimension missing"

        # 8. Timestamps.
        if rec.decision_timestamp is None or rec.outcome_timestamp is None:
            return False, REASON_INVALID_TIMESTAMP, "null timestamp"

        return True, "", ""

    def _to_sample(self, rec: ExperienceRecord) -> ResearchSample:
        ctx = rec.context
        return ResearchSample(
            sample_id=_sample_id(rec),
            experience_id=rec.experience_id,
            idempotency_key=rec.idempotency_key,
            decision_timestamp=rec.decision_timestamp,
            outcome_timestamp=rec.outcome_timestamp or rec.decision_timestamp,
            symbol=rec.symbol,
            timeframe=rec.timeframe,
            strategy_id=rec.strategy_id,
            strategy_version=rec.strategy_version,
            feature_schema_id=rec.feature_schema_id,
            feature_dimension=rec.feature_dimension,
            regime=ctx.regime,
            session=ctx.session,
            volatility_regime=ctx.volatility_regime,
            trend_state=ctx.trend_state,
            feature_hash=rec.feature_hash,
            context_fingerprint=ctx.confluence_fingerprint,
            entry_price=rec.proposed_entry,
            stop_loss=rec.stop_loss,
            take_profit=rec.take_profit,
            direction=rec.action,
            realized_r=rec.realized_r_multiple,
            realized_pnl_usd=rec.realized_pnl_usd,
            risk_distance=rec.planned_risk_distance,
            holding_duration_sec=rec.behavior.duration_sec,
            mae_r=rec.behavior.mae_r,
            mfe_r=rec.behavior.mfe_r,
            exit_reason=rec.exit_reason,
        )

    # ------------------------------------------------------------------
    # Build paths
    # ------------------------------------------------------------------

    def _iter_records(self) -> list[ExperienceRecord]:
        """All ledger records (bounded) with duplicate keys collapsed."""
        records: list[ExperienceRecord] = []
        seen: set[str] = set()
        for sid in self.ledger.list_strategy_ids():
            for rec in self.ledger.get_experiences_for_strategy(sid, limit=10000):
                if rec.idempotency_key in seen:
                    continue
                seen.add(rec.idempotency_key)
                records.append(rec)
        return records

    def audit(self, records: list[ExperienceRecord] | None = None) -> dict[str, Any]:
        """Full eligibility audit with structured rejection reasons.

        P0-C (BUG-140): terminal non-trades are counted as lifecycle evidence
        WITHOUT a per-row rejection log (they are expected, known states —
        not data anomalies). Only genuinely unresolved records
        (MISSING_OUTCOME with no terminal state, FILLED_OUTCOME_MISSING,
        zero-substitution) remain per-row recoverable findings.
        """
        self._source_cache = {}
        records = records if records is not None else self._iter_records()
        eligible: list[ExperienceRecord] = []
        rejected: list[dict[str, Any]] = []
        non_trade_count = 0
        for rec in records:
            if not (rec.is_executed and rec.is_closed):
                # Classify by terminal lifecycle instead of a blanket
                # MISSING_OUTCOME (P0-C). Known terminal non-trades are
                # counted quietly; unknown hangs stay recoverable findings.
                state = lifecycle_from_outcome(
                    is_executed=bool(rec.is_executed),
                    is_closed=bool(rec.is_closed),
                    exit_reason=getattr(rec, "exit_reason", "") or "",
                    decision_lifecycle="",
                )
                reason = _LIFECYCLE_REASON.get(state, REASON_MISSING_OUTCOME)
                entry = {
                    "trade_id": rec.experience_id,
                    "idempotency_key": rec.idempotency_key,
                    "strategy_id": rec.strategy_id,
                    "rejection_reason": reason,
                    "rejection_stage": "dataset",
                    "detail": TERMINAL_DETAIL.get(state, "not executed/closed"),
                    "recoverable": reason in _RECOVERABLE_REASONS,
                    "source": "ledger",
                }
                if reason in _NON_TRADE_REASONS:
                    non_trade_count += 1
                else:
                    rejected.append(entry)
                continue
            ok, reason, detail = self.evaluate_sample(rec)
            if ok:
                eligible.append(rec)
            else:
                rejected.append(
                    {
                        "trade_id": rec.experience_id,
                        "idempotency_key": rec.idempotency_key,
                        "strategy_id": rec.strategy_id,
                        "rejection_reason": reason,
                        "rejection_stage": "dataset",
                        "detail": detail,
                        "recoverable": reason in _RECOVERABLE_REASONS,
                        "source": "ledger",
                    }
                )
        top: dict[str, int] = {}
        for r in rejected:
            top[r["rejection_reason"]] = top.get(r["rejection_reason"], 0) + 1
        return {
            "total_records": len(records),
            "eligible": len(eligible),
            "rejected": len(rejected),
            "terminal_non_trades": non_trade_count,
            "zero_substituted": top.get(REASON_MISSING_REALIZED_R, 0)
            + top.get(REASON_MISSING_REALIZED_PNL, 0),
            "rejection_reasons": top,
            "rejections": rejected,
        }

    def build(self, dataset_id: str | None = None) -> ResearchDataset:
        """
        Builds the full research dataset from all closed experiences, causally
        ordered by decision_timestamp. Records failing the eligibility audit
        are excluded with structured rejection logs.

        P0-C (BUG-140): terminal non-trades are EXCLUDED from realized-R
        research (they are not trades and receive no fake R) but are counted
        in the dataset's lifecycle census rather than re-logged row by row on
        every build.
        """
        self._source_cache = {}
        samples: list[ResearchSample] = []
        seen: set[str] = set()
        audit_all: list[ExperienceRecord] = list(self._iter_records())
        # One deterministic full classification pass (no per-row info spam for
        # known terminal non-trades; only unresolved evidence is logged).
        for rec in audit_all:
            ok, reason, detail = self.evaluate_sample(rec)
            if not ok:
                if reason in _NON_TRADE_REASONS:
                    continue  # counted via audit(); expected lifecycle evidence
                logger.info(
                    "[STRATEGY_RESEARCH] event=DATASET_REJECTED",
                    stage="dataset",
                    trade_id=rec.experience_id,
                    reason=reason,
                    detail=detail[:120],
                    recoverable=reason in _RECOVERABLE_REASONS,
                )
                continue
            if rec.idempotency_key in seen:
                continue
            seen.add(rec.idempotency_key)
            samples.append(self._to_sample(rec))
        samples.sort(key=lambda s: s.decision_timestamp)
        ds = self._dataset(dataset_id, samples)
        # P0-E (dataset contract): explicit, auditable evidence census that
        # travels with the dataset. Deterministic and reproducible. The model
        # is frozen, so the census is attached via an immutable copy.
        ds_audit = self.audit(audit_all)
        ds = ds.model_copy(
            update={
                "provenance_extra": {
                    "total_decisions": ds_audit["total_records"],
                    "valid_research_samples": ds_audit["eligible"],
                    "terminal_non_trades": ds_audit["terminal_non_trades"],
                    "recovered_outcomes": self._count_recovered_outcomes(),
                    "filled_outcome_missing": ds_audit["rejection_reasons"].get(
                        REASON_FILLED_OUTCOME_MISSING, 0
                    ),
                    "unresolved_missing_outcome": ds_audit["rejection_reasons"].get(
                        REASON_MISSING_OUTCOME, 0
                    ),
                    "eligibility_rules": ELIGIBILITY_RULES,
                }
            }
        )
        return ds

    def build_for_strategy(
        self,
        strategy_id: str,
        dataset_id: str | None = None,
        as_of: datetime | None = None,
    ) -> ResearchDataset:
        """
        Builds a causally-safe dataset for one strategy family.

        When `as_of` is given, only samples whose DECISION happened strictly
        BEFORE `as_of` are included (spec 7 leakage guard). This lets the
        walk-forward / OOS pipeline construct train / validation splits that can
        never peek into the future.
        """
        self._source_cache = {}
        records = self.ledger.get_experiences_for_strategy(strategy_id, limit=10000)
        samples: list[ResearchSample] = []
        for rec in records:
            ok, _, _ = self.evaluate_sample(rec)
            if not ok:
                continue
            if as_of is not None and rec.decision_timestamp >= as_of:
                continue
            samples.append(self._to_sample(rec))
        samples.sort(key=lambda s: s.decision_timestamp)
        return self._dataset(dataset_id, samples)

    def _dataset(self, dataset_id: str | None, samples: list[ResearchSample]) -> ResearchDataset:
        did = dataset_id or _dataset_id(samples)
        source_range: dict[str, str] = {}
        if samples:
            source_range = {
                "start": samples[0].decision_timestamp.isoformat(),
                "end": samples[-1].decision_timestamp.isoformat(),
            }
        schema_ids = sorted({s.feature_schema_id for s in samples})
        return ResearchDataset(
            dataset_id=did,
            created_at=datetime.now(UTC),
            samples=samples,
            source_range=source_range,
            schema_ids=schema_ids,
        )


def _dataset_id(samples: list[ResearchSample]) -> str:
    if not samples:
        return f"ds_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    digest = hashlib.sha256()
    for s in samples:
        digest.update(f"{s.idempotency_key}|{int(s.decision_timestamp.timestamp())}".encode())
    return f"ds_{digest.hexdigest()[:16]}"


def dataset_provenance(dataset: ResearchDataset) -> dict[str, Any]:
    """Eligible lineage summary for a dataset (spec 26: research data versioning)."""
    schemas: dict[str, int] = {}
    for s in dataset.samples:
        schemas[s.feature_schema_id] = schemas.get(s.feature_schema_id, 0) + 1
    return {
        "dataset_id": dataset.dataset_id,
        "sample_count": len(dataset.samples),
        "source_range": dataset.source_range,
        "schema_ids": dataset.schema_ids,
        "schema_distribution": schemas,
        "strategy_ids": sorted({s.strategy_id for s in dataset.samples}),
    }
