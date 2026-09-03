"""Dump all audit_orders rows that reference phantom tickets with an ENTRY (non-protection)."""

import sqlite3

con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

# all non-protection rows for phantom tickets
rows = cur.execute(
    "SELECT id, timestamp, ticket, order_id, action, price, volume, execution_mode, reason "
    "FROM audit_orders "
    "WHERE ticket >= 152487000000 AND execution_mode != 'PROTECTION' "
    "ORDER BY id"
).fetchall()
print(f"non-protection rows for 152487000000+: {len(rows)}")
for r in rows:
    print(
        f"id={r['id']} ts={r['timestamp']} ticket={r['ticket']} action={r['action']!r} "
        f"price={r['price']} vol={r['volume']} mode={r['execution_mode']} "
        f"reason={(r['reason'] or '')[:70]}"
    )

# also check the 'Generated candidate'/'Executed order' for ALL tickets
print("\n=== ALL 'Generated candidate' rows ===")
rows = cur.execute(
    "SELECT id, timestamp, ticket, order_id, action, price, volume, execution_mode "
    "FROM audit_orders WHERE action = 'Generated candidate' ORDER BY id"
).fetchall()
for r in rows:
    print(
        f"id={r['id']} ts={r['timestamp']} ticket={r['ticket']} order_id={r['order_id']} "
        f"price={r['price']} vol={r['volume']} mode={r['execution_mode']}"
    )
con.close()
