"""
FINAL ROOT-CAUSE PROOF: Where the phantom tickets come from.

The engine tracked tickets 152487837184..152488450000 (decisions 05:10-07:21)
with protection activity (BREAKEVEN_FAILED etc), yet:
  - audit_orders has ZERO non-protection (entry/dispatch) rows for those tickets
  - the broker deal history has NO deals for those tickets
  - the broker's real deals (01:12-02:35 UTC, positions 1524868..-1524870..)
    with real PnL were never recorded in audit_experiences

So the phantom tickets were INVENTED by the engine's position-tracking/
protection layer (paper/simulation fallback?), not by real broker fills.

Find: audit_signals for 152487837184 & the experience's request_id, and
check where the experience decision chain converted a SIGNAL into a tracked
ticket without a real MT5 fill. Also dump the full experience row payload
for the first experience to see what action the model took.
"""

import json
import sqlite3

con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

# 1) The first experience's payload
row = cur.execute(
    "SELECT experience_id, payload FROM audit_experiences WHERE idempotency_key LIKE 'exp_3e8bcc1b%'"
).fetchone()
if row:
    p = json.loads(row["payload"])
    print("=== first experience payload (selected) ===")
    for k in [
        "experience_id",
        "request_id",
        "execution_id",
        "decision_id",
        "idempotency_key",
        "symbol",
        "timeframe",
        "decision_timestamp",
        "strategy_id",
        "strategy_version",
        "action",
        "entry_reason",
        "proposed_entry",
        "stop_loss",
        "take_profit",
        "model_probability",
        "signal_confidence",
        "min_rr_policy",
        "record_version",
    ]:
        print(f"  {k}={p.get(k)!r}")
    ctx = p.get("context", {})
    print(
        "  context:",
        {
            k: ctx.get(k)
            for k in ["session", "regime", "volatility_regime", "trend_state", "setup_type"]
        },
    )
    fs = p.get("feature_snapshot", {})
    print(
        "  feature_snapshot: schema=",
        fs.get("feature_schema_id"),
        "dim=",
        fs.get("feature_dimension"),
        "n_values=",
        len(fs.get("values", [])),
    )
    prov = p.get("provenance", {})
    print(
        "  provenance: model_id=",
        prov.get("model_id"),
        "version=",
        prov.get("model_version"),
        "role=",
        prov.get("model_role"),
    )
else:
    print("no first experience row")

# 2) was there an audit_signals row with a ticket binding? find signal for request
rid = "3e8bcc1b-77d6-4e77-962c-0b28ea357832"
rows = cur.execute(
    "SELECT id, generated_at, action, confidence, proposed_entry, stop_loss, take_profit, regime, "
    "execution_mode, reason_code, blocked_by, confidence_before_filters, confidence_after_filters "
    "FROM audit_signals WHERE request_id = ?",
    (rid,),
).fetchall()
print(f"\n=== audit_signals rows for request {rid} ===")
for r in rows:
    print(dict(r))

con.close()
