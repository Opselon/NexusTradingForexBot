"""AGENT-13 STEP-05 probe: first-divergence audit for the 151 zero-PnL ledger rows.

Read-only. For each affected ticket, trace broker -> execution -> ledger ->
outcome to find the FIRST stage where real PnL becomes zero/missing.
"""

import sqlite3
from collections import Counter

DB = r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

SQL_AFFECTED = (
    "SELECT b.trade_id, b.net_pnl AS broker_pnl, b.gross_pnl, b.commission, "
    "b.swap, b.fee, b.source, b.exit_time, b.entry_time, b.master_order_id, "
    "b.deal_ids, b.order_ids, l.ticket, l.net_pnl_usd AS ledger_pnl, "
    "l.exit_mechanism, l.order_id, l.open_time, l.close_time, "
    "l.exit_reason_source "
    "FROM audit_broker_trades b JOIN audit_ledger l ON l.ticket = b.trade_id "
    "WHERE b.exit_time != '' AND abs(b.net_pnl) > 0.01 "
    "AND abs(l.net_pnl_usd) < 0.005"
)
rows = [dict(r) for r in con.execute(SQL_AFFECTED)]
print("affected (broker PnL != 0, ledger == 0):", len(rows))

# 2) For each: is there an execution row? an experience? an outcome?
exec_rows = {}
for r in con.execute("SELECT * FROM audit_executions"):
    exec_rows[str(r["order_id"])] = dict(r)

exp_by_exec = {}
for r in con.execute("SELECT * FROM audit_experiences"):
    exp_by_exec[str(r["execution_id"])] = dict(r)
    exp_by_exec[str(r["idempotency_key"])] = dict(r)

out_by_exec = {}
for r in con.execute("SELECT * FROM audit_experience_outcomes"):
    out_by_exec[str(r["execution_id"])] = dict(r)
    out_by_exec[str(r["idempotency_key"])] = dict(r)

# 3) Where does the PnL first become zero?
stage_counter = Counter()
samples = []
for r in rows:
    tid = str(r["trade_id"])
    has_exec = tid in exec_rows
    has_exp = tid in exp_by_exec or str(r["order_id"]) in exp_by_exec or str(r["master_order_id"]) in exp_by_exec
    has_out = tid in out_by_exec or str(r["order_id"]) in out_by_exec
    if not has_exec and not has_exp and not has_out:
        stage = "LEDGER_ONLY_ZERO"
    elif has_out:
        o = out_by_exec.get(tid) or {}
        stage = "OUTCOME_ZERO" if abs(float(o.get("realized_pnl_usd") or 0)) < 0.005 else "OUTCOME_OK_BUT_LEDGER_ZERO"
    elif has_exp:
        stage = "EXPERIENCE_NO_OUTCOME"
    else:
        stage = "EXECUTION_NO_EXPERIENCE"
    stage_counter[stage] += 1
    if len(samples) < 6:
        samples.append({
            "ticket": tid, "broker_pnl": r["broker_pnl"], "ledger_pnl": r["ledger_pnl"],
            "exit_mechanism": r["exit_mechanism"], "stage": stage,
            "has_execution": has_exec, "has_experience": has_exp, "has_outcome": has_out,
        })

print("stage classification:", dict(stage_counter))
print("samples:")
for s in samples:
    print(" ", s)

# 4) exit_reason_source coverage on affected rows
src_counter = Counter(r.get("exit_reason_source") or "" for r in rows)
print()
print("exit_reason_source on affected:", dict(src_counter))

# 5) do any affected rows have an ORDER in audit_orders or broker_orders?
order_match = 0
for r in rows:
    oid = str(r.get("order_id") or "")
    if oid:
        row = con.execute("SELECT 1 FROM audit_orders WHERE order_id=? LIMIT 1", (oid,)).fetchone()
        if row:
            order_match += 1
print("affected with audit_orders row:", order_match)

con.close()