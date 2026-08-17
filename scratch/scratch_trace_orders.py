"""Trace the very first audit_orders rows for the phantom tickets."""

import sqlite3

con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

print("=== FIRST audit_orders rows (chronological) ===")
rows = cur.execute(
    "SELECT id, ticket, order_id, action, price, volume, reason, execution_mode, timestamp "
    "FROM audit_orders ORDER BY id LIMIT 30"
).fetchall()
for r in rows:
    print(
        f"id={r['id']} ts={r['timestamp']} ticket={r['ticket']} action={r['action']} "
        f"price={r['price']} vol={r['volume']} mode={r['execution_mode']} "
        f"reason={(r['reason'] or '')[:60]}"
    )

print("\n=== MIN/MAX audit_orders timestamp ===")
r = cur.execute("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM audit_orders").fetchone()
print(r[0], "..", r[1], "count=", r[2])

print("\n=== First 'Executed order' entries for phantom tickets (real dispatch rows) ===")
rows = cur.execute(
    "SELECT ticket, order_id, action, price, volume, reason, execution_mode, timestamp "
    "FROM audit_orders WHERE action LIKE '%Executed%' AND ticket >= 152487800000 "
    "ORDER BY id LIMIT 15"
).fetchall()
for r in rows:
    print(
        f"ts={r['timestamp']} ticket={r['ticket']} order_id={r['order_id'][:28]} "
        f"action={r['action']} price={r['price']} vol={r['volume']} mode={r['execution_mode']}"
    )

print("\n=== First 'Generated candidate' pending rows for phantom tickets ===")
rows = cur.execute(
    "SELECT ticket, order_id, action, price, volume, reason, execution_mode, timestamp "
    "FROM audit_orders WHERE action LIKE '%Generated candidate%' AND ticket >= 152487800000 "
    "ORDER BY id LIMIT 10"
).fetchall()
for r in rows:
    print(
        f"ts={r['timestamp']} ticket={r['ticket']} order_id={r['order_id'][:28]} "
        f"action={r['action']} price={r['price']} vol={r['volume']} mode={r['execution_mode']}"
    )
con.close()
