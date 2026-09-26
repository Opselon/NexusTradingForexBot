"""Step 1: BEFORE snapshot of the LIVE nexusdb + provider resolution check."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_pg_lib import LIVE_DOMAINS, cfg_summary, connect, table_counts

OUT = Path("C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/lane_g_live_before.json")

t0 = time.perf_counter()
res: dict = {"probe": "lane-g-live-before", "started_at": time.time()}

res["domains"] = {d: cfg_summary(d) for d in LIVE_DOMAINS}
res["canonical_check"] = {
    d: (v["host"], v["port"], v["database"], v["username"]) == ("localhost", 5432, "nexusdb", "postgres")
    and v["is_postgresql"]
    for d, v in res["domains"].items()
}

counts, errors = table_counts()
res["total_tables"] = len(counts)
res["counts"] = counts
res["empty_tables"] = sorted(t for t, c in counts.items() if c == 0)
res["empty_before"] = len(res["empty_tables"])
res["nonzero"] = {t: c for t, c in counts.items() if c > 0}
res["sweep_errors"] = errors
res["sweep_ms"] = round((time.perf_counter() - t0) * 1000, 1)

# dead-letter evidence of prior failed writes
with connect() as conn, conn.cursor() as cur:
    for q, k in (
        ("SELECT count(*) FROM audit_dead_letter", "audit_dead_letter"),
        ("SELECT count(*) FROM audit_signals", "audit_signals"),
        ("SELECT count(*) FROM audit_guard_telemetry", "audit_guard_telemetry"),
        ("SELECT count(*) FROM incidents", "incidents"),
        ("SELECT count(*) FROM model_governance_events", "model_governance_events"),
        ("SELECT count(*) FROM shadow_runs", "shadow_runs"),
        ("SELECT version()", "pg_version"),
    ):
        try:
            cur.execute(q)
            res[k] = cur.fetchone()[0]
        except Exception as exc:
            conn.rollback()
            res[k] = f"ERR {type(exc).__name__}: {exc}"

OUT.write_text(json.dumps(res, indent=1, default=str))
print(json.dumps({k: v for k, v in res.items() if k not in ("counts", "empty_tables")}, indent=1, default=str))
print("empty_before =", res["empty_before"], "/", res["total_tables"])
