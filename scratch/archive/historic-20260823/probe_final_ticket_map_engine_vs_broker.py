"""
Final mapping: are the engine's tracked tickets (1524878..) the SAME numbers
as broker orders that were pending at the time (state=2 expired)? Compare
ticket ranges and times directly.
"""

import sqlite3

con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

# engine ledger OPENED + experience tickets
rows = cur.execute(
    "SELECT DISTINCT ticket FROM audit_ledger WHERE ticket > 1000000 ORDER BY ticket"
).fetchall()
engine_tickets = [r[0] for r in rows]
print("engine ticket count:", len(engine_tickets))
print("engine ticket range:", engine_tickets[0], "..", engine_tickets[-1])
# gaps?
print("engine tickets sample (every 10th):", engine_tickets[::10])

# Broker pending orders at 04:24 (152487589985+) vs engine first ticket 152487837184
# Difference between broker order tickets and engine position tickets:
print("\nbroker order tickets at 04:24: 152487589985 .. 152487596461")
print("engine first ticket: 152487837184")
print("delta: broker 152487596461 -> engine 152487837184 =", 152487837184 - 152487596461)
print("engine last ticket: 152488471867")
print("broker current position tickets (07:06): 152488384880 .. 152488384992")
print(
    "engine ledger last ticket 152488471867 vs broker current 152488384992: delta ",
    152488471867 - 152488384992,
)
con.close()
