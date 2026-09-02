"""Trace the actual ENTRY events for the phantom tickets."""

import sqlite3

con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

print("=== audit_orders actions census (top 20) ===")
rows = cur.execute(
    "SELECT action, execution_mode, COUNT(*) n FROM audit_orders GROUP BY action, execution_mode ORDER BY n DESC LIMIT 20"
).fetchall()
for r in rows:
    print(f"  {r['n']:6d}  action={r['action']!r} mode={r['execution_mode']!r}")

print("\n=== First rows mentioning 'Executed' in ANY action ===")
rows = cur.execute(
    "SELECT id, timestamp, ticket, order_id, action, price, volume, execution_mode FROM audit_orders "
    "WHERE action LIKE '%Executed%' OR reason LIKE '%dispatch%' ORDER BY id LIMIT 12"
).fetchall()
for r in rows:
    print(
        f"id={r['id']} ts={r['timestamp']} ticket={r['ticket']} action={r['action']!r} "
        f"price={r['price']} vol={r['volume']} mode={r['execution_mode']}"
    )

print("\n=== audit_orders with order_id matching the FIRST experience's request id ===")
rid = "3e8bcc1b"
rows = cur.execute(
    "SELECT id, timestamp, ticket, order_id, action, price, volume, execution_mode FROM audit_orders "
    "WHERE order_id LIKE ? ORDER BY id LIMIT 20",
    (f"%{rid}%",),
).fetchall()
for r in rows:
    print(
        f"id={r['id']} ts={r['timestamp']} ticket={r['ticket']} order_id={r['order_id']} "
        f"action={r['action']!r} price={r['price']} vol={r['volume']} mode={r['execution_mode']}"
    )

print("\n=== audit_signals for the first experience request id (decision + entry signal) ===")
rows = cur.execute(
    "SELECT id, request_id, action, confidence, proposed_entry, stop_loss, take_profit, regime, "
    "generated_at, execution_mode, reason_code FROM audit_signals WHERE request_id LIKE ? ORDER BY id LIMIT 10",
    (f"%{rid}%",),
).fetchall()
for r in rows:
    print(
        f"id={r['id']} gen={r['generated_at']} request={r['request_id']} action={r['action']} "
        f"conf={r['confidence']} entry={r['proposed_entry']} sl={r['stop_loss']} tp={r['take_profit']} "
        f"mode={r['execution_mode']} reason={r['reason_code']}"
    )
con.close()
