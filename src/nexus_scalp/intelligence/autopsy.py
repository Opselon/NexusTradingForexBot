"""
Trade Autopsy Engine
====================
PHASE 09 forensic analysis of every completed trade.

The Phase 08 `OutcomeAnalyzer` already decomposes a closed trade into the six
quality dimensions (strategy, entry, management, exit, execution, + risk). This
engine packages that decomposition into an explanatory NARRATIVE that answers:

    WHY DID THIS TRADE WIN?   /   WHY DID THIS TRADE LOSE?

It explicitly separates "the market was wrong" from "we managed it badly" so the
system never collapses "losing trade" into "bad strategy".

VERDICT MODEL
-------------
    CLEAN_WIN    profitable, correct thesis, sound execution/management
    LUCKY_WIN    profitable but thesis/entry evidence was poor
    MANAGED_LOSS negative but stop/risk respected (acceptable loss)
    COSTLY_LOSS  negative AND a management/execution failure amplified it

An autopsy is a derived, rebuildable object - it never writes financial truth
and never touches execution. It is persisted once per closed ticket (upsert).
"""

from __future__ import annotations

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.models import (
    ExperienceRecord,
    OutcomeDecomposition,
)
from nexus_scalp.experience.quality import OutcomeAnalyzer
from nexus_scalp.intelligence.models import (
    AutopsyVerdict,
    TradeAutopsy,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.intelligence.autopsy")

_UPSERT_AUTOPSY_SQL = """
    INSERT INTO trade_autopsies
    (ticket, trade_id, experience_id, strategy_id, strategy_version, symbol,
     timeframe, entry_price, exit_price, volume, direction, entry_reason,
     realized_pnl_usd, realized_r, mfe_r, mae_r, giveback_pct,
     holding_duration_sec, exit_mechanism, strategy_quality, entry_quality,
     management_quality, exit_quality, execution_quality, quality_verdict,
     behavioral_flags, narrative, autopsied_at, payload)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(ticket) DO UPDATE SET
        strategy_id=excluded.strategy_id,
        realized_pnl_usd=excluded.realized_pnl_usd,
        realized_r=excluded.realized_r,
        mfe_r=excluded.mfe_r,
        mae_r=excluded.mae_r,
        giveback_pct=excluded.giveback_pct,
        exit_mechanism=excluded.exit_mechanism,
        strategy_quality=excluded.strategy_quality,
        entry_quality=excluded.entry_quality,
        management_quality=excluded.management_quality,
        exit_quality=excluded.exit_quality,
        execution_quality=excluded.execution_quality,
        quality_verdict=excluded.quality_verdict,
        behavioral_flags=excluded.behavioral_flags,
        narrative=excluded.narrative,
        autopsied_at=excluded.autopsied_at,
        payload=excluded.payload;
"""


class TradeAutopsyEngine:
    """
    Produces and persists the forensic narrative for completed trades.

    Truth comes from merge of the decision experience + outcome decomposition
    (Phase 08). All thresholds are auditable constants derived from the same
    quality decomposition already applied by `OutcomeAnalyzer`.
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        analyzer: OutcomeAnalyzer | None = None,
        captured_giveback_pct: float = 0.35,
    ) -> None:
        self.audit_repo = audit_repo
        self.analyzer = analyzer or OutcomeAnalyzer()
        self.captured_giveback_pct = captured_giveback_pct
        self.autopsy_count: int = 0

    def build_autopsy(
        self,
        record: ExperienceRecord | None,
        decomposition: OutcomeDecomposition,
        realized_pnl_usd: float,
        realized_r: float,
        ticket: str,
        symbol: str = "",
        timeframe: str = "M1",
        exit_mechanism: str = "",
        flags: list[str] | None = None,
    ) -> TradeAutopsy:
        """Constructs the explanatory narrative for one closed trade."""
        flags = list(flags or [])
        strategy_quality = decomposition.strategy_quality
        entry_quality = decomposition.entry_quality
        management_quality = decomposition.position_management_quality
        exit_quality = decomposition.exit_quality
        execution_quality = decomposition.execution_quality

        mfe_r = 0.0
        mae_r = 0.0
        giveback_pct = 0.0
        duration = 0.0
        entry_price = 0.0
        exit_price = 0.0
        volume = 0.0
        entry_reason = ""
        strategy_id = ""
        strategy_version = ""
        experience_id = ""
        direction = ""

        if record is not None:
            entry_price = record.proposed_entry
            exit_price = record.proposed_entry
            volume = record.approved_volume
            entry_reason = record.entry_reason
            strategy_id = record.strategy_id
            strategy_version = record.strategy_version
            experience_id = record.experience_id
            direction = record.action
            mfe_r = record.behavior.mfe_r
            mae_r = record.behavior.mae_r
            duration = record.behavior.duration_sec
            if record.behavior.mfe_r > 1e-9 and realized_r > 0.0:
                giveback_pct = max(0.0, 1.0 - (realized_r / record.behavior.mfe_r))

        verdict, narrative = self._narrate(
            realized_r=realized_r,
            strategy_quality=strategy_quality,
            entry_quality=entry_quality,
            management_quality=management_quality,
            exit_quality=exit_quality,
            execution_quality=execution_quality,
            mfe_r=mfe_r,
            mae_r=mae_r,
            giveback_pct=giveback_pct,
            flags=flags,
        )

        return TradeAutopsy(
            ticket=str(ticket),
            experience_id=experience_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            symbol=symbol or (record.symbol if record else ""),
            timeframe=timeframe,
            entry_price=entry_price,
            exit_price=exit_price,
            volume=volume,
            direction=direction,
            entry_reason=entry_reason,
            realized_pnl_usd=realized_pnl_usd,
            realized_r=realized_r,
            mfe_r=mfe_r,
            mae_r=mae_r,
            giveback_pct=giveback_pct,
            holding_duration_sec=duration,
            exit_mechanism=exit_mechanism,
            strategy_quality=strategy_quality,
            entry_quality=entry_quality,
            management_quality=management_quality,
            exit_quality=exit_quality,
            execution_quality=execution_quality,
            verdict=verdict,
            behavioral_flags=flags,
            narrative=narrative,
        )

    def _narrate(
        self,
        realized_r: float,
        strategy_quality: float,
        entry_quality: float,
        management_quality: float,
        exit_quality: float,
        execution_quality: float,
        mfe_r: float,
        mae_r: float,
        giveback_pct: float,
        flags: list[str],
    ) -> tuple[AutopsyVerdict, str]:
        """Deterministically decides the verdict and writes the narrative."""
        strategy_good = strategy_quality >= 0.35
        entry_good = entry_quality >= 0.0

        if realized_r > 0.0:
            if strategy_good and entry_good:
                verdict = AutopsyVerdict.CLEAN_WIN
                narrative = (
                    "WIN: the market validated the thesis (strategy edge + sound "
                    f"entry) and management/execution preserved {max(0.0, 1.0 - giveback_pct) * 100:.0f}% "
                    "of the move."
                )
            else:
                verdict = AutopsyVerdict.LUCKY_WIN
                narrative = (
                    "WIN but for the wrong reason: PnL was positive while "
                    f"strategy/entry evidence was weak (strategy {strategy_quality:+.2f}, "
                    f"entry {entry_quality:+.2f}). Do not credit a broken thesis for this."
                )
        elif abs(realized_r) < 1e-9:
            verdict = AutopsyVerdict.EVEN
            narrative = "EVEN: expired or closed around breakeven; no material edge or loss."
        else:
            loss_quality = (management_quality < 0.0) or (execution_quality < 0.0)
            if not loss_quality:
                verdict = AutopsyVerdict.MANAGED_LOSS
                narrative = (
                    "LOSS but well managed: risk was respected (acceptable loss). "
                    "The strategy got a real look and was stopped at planned risk - "
                    "this is not evidence the strategy is broken."
                )
            else:
                verdict = AutopsyVerdict.COSTLY_LOSS
                reasons = []
                if management_quality < 0.0:
                    reasons.append(f"management {management_quality:+.2f}")
                if execution_quality < 0.0:
                    reasons.append(f"execution {execution_quality:+.2f}")
                if giveback_pct >= self.captured_giveback_pct:
                    reasons.append(f"{giveback_pct * 100:.0f}% profit giveback")
                narrative = (
                    "LOSS amplified by process failure: "
                    + "; ".join(reasons)
                    + ". The loss is attributable to management/execution, not "
                    "necessarily the strategy hypothesis."
                )
        return verdict, narrative

    def persist(self, autopsy: TradeAutopsy) -> bool:
        """Queues the autopsy row (upsert on ticket). Returns True when queued."""
        if not self.audit_repo._is_sqlite:
            return False
        d = autopsy
        args = (
            d.ticket,
            d.trade_id,
            d.experience_id,
            d.strategy_id,
            d.strategy_version,
            d.symbol,
            d.timeframe,
            d.entry_price,
            d.exit_price,
            d.volume,
            d.direction,
            d.entry_reason,
            d.realized_pnl_usd,
            d.realized_r,
            d.mfe_r,
            d.mae_r,
            d.giveback_pct,
            d.holding_duration_sec,
            d.exit_mechanism,
            d.strategy_quality,
            d.entry_quality,
            d.management_quality,
            d.exit_quality,
            d.execution_quality,
            d.verdict.value,
            ",".join(d.behavioral_flags),
            d.narrative,
            d.autopsied_at.isoformat(),
            d.model_dump_json(),
        )
        try:
            self.audit_repo._queue.put_nowait((_UPSERT_AUTOPSY_SQL, args))
            self.autopsy_count += 1
            logger.info(
                "[TRADE_AUTOPSY]",
                ticket=d.ticket,
                strategy_quality=round(d.strategy_quality, 3),
                entry_quality=round(d.entry_quality, 3),
                management_quality=round(d.management_quality, 3),
                exit_quality=round(d.exit_quality, 3),
                execution_quality=round(d.execution_quality, 3),
                verdict=d.verdict.value,
            )
            return True
        except Exception as e:
            logger.error("[TRADE_AUTOPSY] persist failed (isolated)", ticket=d.ticket, error=str(e))
            return False
