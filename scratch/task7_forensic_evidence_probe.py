# TASK-7 forensic evidence probe — READ-ONLY — 2026-08-18
# Proves the BUG-083..090 ledger from the LIVE artifacts/audit.db + order_manager source.
# Output: artifacts/logs/task7_forensic_evidence.out.txt (see scratch/task7_... .out.txt)
import sqlite3

DB = r"artifacts/audit.db"


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    lines: list[str] = []

    def out(s: str = "") -> None:
        lines.append(s)
        print(s)

    out("=== TASK-7 FORENSIC EVIDENCE (read-only) ===")
    cur.execute(
        """
        SELECT l.ticket, l.exit_mechanism, l.net_pnl_usd, t.gross_pnl,
               t.exit_reason, t.exit_comment, l.was_sl_modified, l.final_sl_price
        FROM audit_ledger l JOIN audit_broker_trades t ON t.position_id = l.ticket
        WHERE l.exit_mechanism='BREAK_EVEN_SL_HIT' AND COALESCE(l.net_pnl_usd,0)=0
          AND ABS(t.gross_pnl) > 0.01 ORDER BY l.ticket LIMIT 10
        """
    )
    for r in cur.fetchall():
        out(
            f"BE-mislabel t={r['ticket']} ledgerPnL={r['net_pnl_usd']} "
            f"brokerGross={r['gross_pnl']} brokerReason={r['exit_reason']} "
            f"comment={r['exit_comment']} was_mod={r['was_sl_modified']} "
            f"final_sl={r['final_sl_price']}"
        )
    cur.execute(
        """
        SELECT COUNT(*) c FROM audit_ledger l
        JOIN audit_broker_trades t ON t.position_id = l.ticket
        WHERE COALESCE(l.net_pnl_usd,0)=0 AND ABS(t.gross_pnl) > 0.01
        """
    )
    out(f"ledger-zero-with-real-broker-gross total: {cur.fetchone()['c']}")
    cur.execute(
        """
        SELECT action, COUNT(*) c FROM audit_orders
        WHERE execution_mode='PROTECTION' GROUP BY action ORDER BY c DESC
        """
    )
    for r in cur.fetchall():
        out(f"protection-audit {r['action']}: {r['c']}")
    conn.close()


if __name__ == "__main__":
    main()
