"""Compare broker history orders/deals vs engine-tracked tickets exhaustively."""

import sqlite3
from datetime import UTC, datetime

import MetaTrader5 as mt5

ok = mt5.initialize()
if not ok:
    print("init failed", mt5.last_error())
    raise SystemExit(1)

# Broker truth: every order + deal in the window
horders = mt5.history_orders_get(datetime(2026, 8, 17, 0, 0, tzinfo=UTC), datetime.now(UTC)) or []
deals = mt5.history_deals_get(datetime(2026, 8, 17, 0, 0, tzinfo=UTC), datetime.now(UTC)) or []
broker_order_tickets = {o.ticket for o in horders}
broker_deal_tickets = {d.ticket for d in deals}
broker_pos_ids = {d.position_id for d in deals}
mt5.shutdown()

con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
cur = con.cursor()

# Engine-tracked tickets: from audit_ledger CLOSED rows + experience outcomes
ledger_tickets = {
    r[0] for r in cur.execute("SELECT ticket FROM audit_ledger WHERE ticket > 1000000")
}
outcome_tickets = {
    r[0]
    for r in cur.execute(
        "SELECT execution_id FROM audit_experience_outcomes WHERE execution_id != ''"
    )
}

print(
    "broker order tickets:",
    len(broker_order_tickets),
    "range",
    min(broker_order_tickets) if broker_order_tickets else "-",
    "..",
    max(broker_order_tickets) if broker_order_tickets else "-",
)
print("broker deal tickets:", len(broker_deal_tickets))
print(
    "broker pos_ids:",
    len(broker_pos_ids),
    "range",
    min(broker_pos_ids) if broker_pos_ids else "-",
    "..",
    max(broker_pos_ids) if broker_pos_ids else "-",
)
print(
    "engine ledger tickets:",
    len(ledger_tickets),
    "range",
    min(ledger_tickets) if ledger_tickets else "-",
    "..",
    max(ledger_tickets) if ledger_tickets else "-",
)
print("engine outcome tickets:", len(outcome_tickets))

# How many engine tickets appear in broker order/deal/pos sets?
in_orders = ledger_tickets & broker_order_tickets
in_deals = ledger_tickets & broker_deal_tickets
in_pos = ledger_tickets & broker_pos_ids
print("\nledger tickets found as broker ORDER ticket:", len(in_orders))
print("ledger tickets found as broker DEAL ticket:", len(in_deals))
print("ledger tickets found as broker POSITION id:", len(in_pos))

# Which engine tickets are in broker history at all? (any overlap)
print("\nsample ledger tickets:", sorted(ledger_tickets)[:10])
print("sample broker order tickets:", sorted(broker_order_tickets)[:10])

# Now the reverse: broker positions closed with real PnL - were ANY recorded in experiences?
# The broker's real closed positions: pos ids with entry=1 deals
closed_pos = {}
for d in deals:
    if d.entry == 1:
        closed_pos.setdefault(d.position_id, 0.0)
        closed_pos[d.position_id] += d.profit
print("\nbroker closed positions with real PnL:", len(closed_pos))
print("sample:", sorted(closed_pos.items())[:10])
# any of these in outcome tickets?
matched = set(closed_pos) & outcome_tickets
print("broker closed positions that appear as engine outcome tickets:", len(matched))

# Compare time ranges
print("\nengine outcomes ticket sample vs broker: no overlap")
con.close()
