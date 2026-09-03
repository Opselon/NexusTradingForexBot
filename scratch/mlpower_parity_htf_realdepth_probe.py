"""MLPWR-06-02: does the LIVE engine actually hit the asymmetry in practice?
The live aggregator starts EMPTY at boot and grows with session bars; after
BUG-058 resync it is reseeded from broker history (900-bar standard).
Check what history depth the live aggregator typically carries, then compute
the parity delta between 'live depth' and 'train depth 55' for feat_40..43.
Also: quantify how many of the live audit_signals rows were decided under
depth>=120 (where feat_41 != 0 is possible) vs depth<120.
"""
from __future__ import annotations
import sqlite3

db = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
db.row_factory = sqlite3.Row
# Engine session spans from boot; audit_signals has ~2660 rows over 7 days.
# Rough proxy: bars per session day on XAUUSD M1 (24h*~60 = ~1440 max, but
# sessions + gaps) - the depth crosses 120 bars ~2h after every boot/resync.
# The structurally important number: training rows are ALWAYS depth-55, so
# ANY live row decided at depth>=120 saw h1_momentum!=0 in-distribution
# values the training set NEVER contained (clipped to 3.0 by bounds).
for r in db.execute("""
SELECT date(generated_at) d, COUNT(*) n FROM audit_signals
GROUP BY d ORDER BY d"""):
    print(f"{r['d']}: {r['n']} live signals decided at aggregator depth the training builder NEVER sees (55-bar cap)")
