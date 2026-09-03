"""Apply BUG-046 ledger edits via deterministic byte-level string replacement."""

from pathlib import Path

p = Path(
    r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src\nexus_scalp\experience\ledger.py"
)
text = p.read_text(encoding="utf-8")

# 1) Insert _REPAIR_OUTCOME_SQL after _INSERT_OUTCOME_SQL block
anchor = """    ON CONFLICT(idempotency_key) DO NOTHING;
\"\"\"

#: Merged projection used by every retrieval path."""

repair_sql = """    ON CONFLICT(idempotency_key) DO NOTHING;
\"\"\"

#: BUG-046 repair path: UPDATEs ONLY the derived outcome layer (never the
#: immutable decision row). Used exclusively by the historical outcome repair
#: job to replace a zero/corrupt realized result with broker-reconstructed
#: truth. Idempotent by key; the payload carries repair provenance.
_REPAIR_OUTCOME_SQL = \"\"\"
    UPDATE audit_experience_outcomes SET
        execution_id = ?,
        outcome_timestamp = ?,
        is_executed = ?,
        is_closed = ?,
        exit_reason = ?,
        realized_pnl_usd = ?,
        realized_r_multiple = ?,
        approved_volume = ?,
        mae_points = ?,
        mfe_points = ?,
        mae_usd = ?,
        mfe_usd = ?,
        mae_r = ?,
        mfe_r = ?,
        holding_duration_seconds = ?,
        slippage_points = ?,
        execution_latency_ms = ?,
        strategy_quality = ?,
        entry_quality = ?,
        execution_quality = ?,
        management_quality = ?,
        exit_quality = ?,
        behavioral_flags = ?,
        payload = ?
    WHERE idempotency_key = ?
\"\"\"

#: Merged projection used by every retrieval path."""

assert text.count(anchor) == 1, f"anchor1 count={text.count(anchor)}"
text = text.replace(anchor, repair_sql)

# 2) Insert repair_outcome method before record_correction
method_anchor = """            return False

    def record_correction(self, correction: ExperienceCorrection) -> bool:"""

repair_method = """            return False

    def repair_outcome(self, outcome: ExperienceOutcome, repair_reason: str = "") -> bool:
        \"\"\"
        BUG-046: corrects a previously-recorded OUTCOME (derived layer only).

        The immutable decision row in `audit_experiences` is NEVER modified.
        This updates the outcome row (unique on idempotency_key) with
        broker-reconstructed truth and stamps repair provenance into the
        payload. Idempotent: repairing the same key twice converges to the
        same value; it never duplicates rows or double-counts PnL.

        Returns True when the repair write was queued.
        \"\"\"
        if not self.audit_repo._is_sqlite:
            return False

        d = outcome.decomposition
        b = outcome.behavior
        args = (
            outcome.execution_id,
            outcome.outcome_timestamp.isoformat(),
            1 if outcome.is_executed else 0,
            1 if outcome.is_closed else 0,
            outcome.exit_reason,
            float(outcome.realized_pnl_usd),
            float(outcome.realized_r_multiple),
            float(outcome.approved_volume),
            float(b.mae_points),
            float(b.mfe_points),
            float(b.mae_usd),
            float(b.mfe_usd),
            float(b.mae_r),
            float(b.mfe_r),
            float(b.duration_sec),
            float(outcome.execution.slippage_points),
            float(outcome.execution.latency_ms),
            float(d.strategy_quality),
            float(d.entry_quality),
            float(d.execution_quality),
            float(d.position_management_quality),
            float(d.exit_quality),
            ",".join(f.value for f in outcome.behavioral_flags),
            outcome.model_dump_json(),
            outcome.idempotency_key,
        )
        try:
            self.audit_repo._queue.put_nowait((_REPAIR_OUTCOME_SQL, args))
            logger.info(
                "[BROKER_OUTCOME_REPAIR] event=REPAIRED",
                idempotency_key=outcome.idempotency_key,
                realized_r=round(outcome.realized_r_multiple, 4),
                realized_pnl=round(outcome.realized_pnl_usd, 2),
                reason=repair_reason or "",
            )
            return True
        except Exception as e:
            logger.error("[EXPERIENCE] OUTCOME repair queue failure", error=str(e))
            return False

    def record_correction(self, correction: ExperienceCorrection) -> bool:"""

assert text.count(method_anchor) == 1, f"anchor2 count={text.count(method_anchor)}"
text = text.replace(method_anchor, repair_method)

# Preserve CRLF (repo convention) — the original bytes are CRLF; str replace kept them.
p.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
print("ledger.py edits applied")
