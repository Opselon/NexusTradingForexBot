"""Agent-12 forensic scan (read-only) — corrected clock skew + learning rates.

Fixes over the generic `nexus incidents scan`:
- clock skew measured only over RECENT rows (last 24h) — historical sync
  batches skew the median with stale reconstructions.
- learning pipeline rates use the CANONICAL experience->outcome linkage
  (idempotency_key), not naive table counts.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from statistics import median

DB = r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

now = datetime.now(UTC)
cutoff = (now - timedelta(hours=24)).isoformat()

# ---- clock skew (recent only) ----
rows = [dict(r) for r in con.execute(
    "SELECT entry_time, exit_time FROM audit_broker_trades "
    "WHERE exit_time != '' AND exit_time >= ? ORDER BY exit_time DESC LIMIT 2000",
    (cutoff,),
)]
offs = []
for r in rows:
    for col in ("entry_time", "exit_time"):
        try:
            dt = datetime.fromisoformat(str(r.get(col) or "").replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                offs.append((dt - now).total_seconds())
        except ValueError:
            continue
if offs:
    print("RECENT broker rows:", len(rows), "| offset samples:", len(offs))
    print("median offset vs host UTC (s):", round(median(offs), 1))
    from collections import Counter
    print("hour buckets:", dict(sorted(Counter(round(o / 3600.0, 1) for o in offs).items(), key=lambda x: -x[1])[:5]))

# ---- learning pipeline: canonical linkage ----
n_exp = con.execute("SELECT COUNT(*) FROM audit_experiences").fetchone()[0]
n_out = con.execute("SELECT COUNT(*) FROM audit_experience_outcomes").fetchone()[0]
linked = con.execute("""
    SELECT COUNT(*) FROM audit_experience_outcomes o
    JOIN audit_experiences e ON e.idempotency_key = o.idempotency_key
""").fetchone()[0]
n_res = con.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
n_cand = con.execute("SELECT COUNT(*) FROM strategy_registry").fetchone()[0]
print()
print("experiences:", n_exp, "outcomes:", n_out, "outcomes linked to experiences:", linked)
print("research_runs:", n_res, "registry candidates:", n_cand)
exp_to_out = linked / n_out if n_out else None
out_to_res = n_res / n_out if n_out else None
res_to_cand = n_cand / n_res if n_res else None
print("linked_to_outcome_rate:", round(exp_to_out, 4) if exp_to_out is not None else None)
print("outcome_to_research_rate:", round(out_to_res, 4) if out_to_res is not None else None)
print("research_to_candidate_rate:", round(res_to_cand, 4) if res_to_cand is not None else None)

# ---- zero outcomes ----
n_zero = con.execute("SELECT COUNT(*) FROM audit_experience_outcomes WHERE realized_pnl_usd = 0").fetchone()[0]
brk_zero_rows = con.execute("""
    SELECT COUNT(*) FROM audit_experience_outcomes o
    JOIN audit_broker_trades b ON b.trade_id = o.execution_id
    WHERE o.realized_pnl_usd = 0 AND b.net_pnl != 0
""").fetchone()[0]
print()
print("zero outcomes:", n_zero, "| with broker PnL != 0 (SUSPECT):", brk_zero_rows)

# ---- news all-neutral ----
try:
    n_articles = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    print()
    print("news articles:", n_articles)
except Exception as e:
    print("news db not in audit:", e)
con.close()