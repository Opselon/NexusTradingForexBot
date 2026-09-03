"""BUG-075 in-process smoke: fixed store.list_registry against the REAL audit.db."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.store import list_registry

repo = AuditRepository(db_url="sqlite:///artifacts/audit.db")
rows = list_registry(repo)
for r in rows[:5]:
    print(
        r.get("strategy_id"), "| score:", repr(r.get("score")), "| lifecycle:", r.get("lifecycle")
    )
print("total rows:", len(rows))
bad = [r for r in rows if str(r.get("score", "")).strip().lower() == "null"]
print("rows still carrying literal 'null':", len(bad))
assert len(bad) == 0, "BUG-075: registry rows must not expose literal 'null' score"
print("SMOKE-OK")
repo.close()
