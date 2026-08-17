"""Forensic pass 2: entry/exit quality decomposition via trade_autopsies."""

import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, "src")

con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row


def q(sql, *args):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


print("=== AUTOPSY QUALITY VERDICT DISTRIBUTION ===")
for r in q("""SELECT quality_verdict, COUNT(*) n, SUM(realized_pnl_usd) pnl, AVG(realized_pnl_usd) avg_pnl
              FROM trade_autopsies GROUP BY quality_verdict ORDER BY pnl ASC"""):
    print(f"  {r['quality_verdict']:20s} n={r['n']:3d} pnl={r['pnl']:9.2f} avg={r['avg_pnl']:8.2f}")

print("\n=== ENTRY QUALITY ===")
for (
    r
) in q("""SELECT entry_quality, COUNT(*) n, SUM(realized_pnl_usd) pnl, AVG(realized_pnl_usd) avg_pnl
              FROM trade_autopsies GROUP BY entry_quality ORDER BY pnl ASC"""):
    print(
        f"  {str(r['entry_quality'])[:20]:20s} n={r['n']:3d} pnl={r['pnl']:9.2f} avg={r['avg_pnl']:8.2f}"
    )

print("\n=== BEHAVIORAL FLAGS ===")
flags = defaultdict(lambda: [0, 0.0])
for r in q("""SELECT behavioral_flags, realized_pnl_usd FROM trade_autopsies"""):
    for f_raw in (r["behavioral_flags"] or "").split(","):
        f = f_raw.strip()
        if f:
            flags[f][0] += 1
            flags[f][1] += r["realized_pnl_usd"]
for f, (n, pnl) in sorted(flags.items(), key=lambda kv: kv[1][1]):
    print(f"  {f:35s} n={n:3d} pnl={pnl:9.2f}")

print("\n=== EXIT MECHANISM (autopsy) ===")
for r in q("""SELECT exit_mechanism, COUNT(*) n, SUM(realized_pnl_usd) pnl, AVG(realized_pnl_usd) avg_pnl,
              AVG(mfe_r) avg_mfe_r, AVG(mae_r) avg_mae_r, AVG(giveback_pct) giveback
              FROM trade_autopsies GROUP BY exit_mechanism ORDER BY pnl ASC"""):
    print(
        f"  {r['exit_mechanism']:28s} n={r['n']:3d} pnl={r['pnl']:9.2f} avg={r['avg_pnl']:8.2f} mfeR={r['avg_mfe_r']:.2f} maeR={r['avg_mae_r']:.2f} giveback={r['giveback']:.0%}"
    )
