"""
BUG-046 REPAIR DRY-RUN against production DB — READ ONLY.
Runs the OutcomeRepairJob candidate + deal-matching logic with a write-suppressed
ledger so NO rows are modified. Reports what WOULD be repaired.
"""

import json
import sys
from datetime import UTC, datetime, timedelta

import MetaTrader5 as mt5

sys.path.insert(0, "src")

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.outcome_repair import OutcomeRepairJob

OK = mt5.initialize()
if not OK:
    print("FATAL: MT5 init failed:", mt5.last_error())
    sys.exit(1)

repo = AuditRepository(db_url="sqlite:///artifacts/audit.db", flush_interval_sec=30.0)
ledger = ExperienceLedger(repo)


class DryRunLedger(ExperienceLedger):
    """Suppresses ALL writes: repair_outcome/record_outcome become no-ops."""

    def repair_outcome(self, outcome, repair_reason=""):
        print(
            f"  [DRY-RUN WOULD REPAIR] key={outcome.idempotency_key[:24]} "
            f"R={outcome.realized_r_multiple:.4f} pnl={outcome.realized_pnl_usd:.2f}"
        )
        return True

    def record_outcome(self, outcome):
        return True


dry = DryRunLedger(repo)


# Reuse the real broker accessor
def broker_deals_fn(ticket, hours_back):
    now = datetime.now(UTC)
    deals = mt5.history_deals_get(now - timedelta(hours=hours_back), now, group="XAUUSD") or []
    out = []
    for d in deals:
        if d.entry == 1:  # DEAL_ENTRY_OUT only
            out.append(
                {
                    "ticket": d.ticket,
                    "order_ticket": d.order,
                    "position_ticket": d.position_id,
                    "symbol": d.symbol,
                    "price": d.price,
                    "volume": d.volume,
                    "profit": d.profit,
                    "commission": d.commission,
                    "swap": d.swap,
                    "comment": d.comment,
                    "closed_at": datetime.fromtimestamp(d.time, tz=UTC),
                    "reason": d.reason,
                }
            )
    return out


job = OutcomeRepairJob(ledger=dry, broker_deals_fn=broker_deals_fn)
candidates = job._candidates()
print(f"=== DRY-RUN: {len(candidates)} zero-R candidates ===")
result = job.run()
print("=== RESULT ===")
print(json.dumps(result.to_dict(), indent=2, default=str)[:2000])

repo.close()
mt5.shutdown()
