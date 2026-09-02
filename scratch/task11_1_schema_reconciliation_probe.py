"""TASK-11 STEP-01/02: schema reconciliation forensic probe — scalp_v3 vs scalp_v4.

Read-only. Documents the ACTUAL state of every 70D schema reference.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from nexus_scalp.features.schema import FEATURE_SCHEMAS  # noqa: E402
from nexus_scalp.features.schema_contract import (  # noqa: E402
    DIMENSION,
    SCHEMA_ID,
    canonical_feature_names,
    feature_schema_hash,
)

print("=== CANONICAL CONTRACT (schema_contract.py) ===")
print("SCHEMA_ID:", SCHEMA_ID, "| DIMENSION:", DIMENSION)
names = canonical_feature_names()
print("names len:", len(names))
print("50..59:", names[50:60])
print("60..69:", names[60:70])
print("hash:", feature_schema_hash())

print("\n=== REGISTRY (features/schema.py) ===")
for s in FEATURE_SCHEMAS.list_schemas():
    print(f"  {s.schema_id}: dim={s.dimension} active={s.is_active} supersedes={s.supersedes}")

print("\n=== scalp_v3 vs scalp_v4 DESCRIPTION DIFF (registry) ===")
v3 = FEATURE_SCHEMAS.resolve("scalp_v3")
v4 = FEATURE_SCHEMAS.resolve("scalp_v4")
print("same dimension:", v3.dimension == v4.dimension == 70)
print("v3 desc:", (v3.description or "")[:180])
print("v4 desc:", (v4.description or "")[:180])

print("\n=== ACTIVE RUNTIME USAGE ===")
try:
    from nexus_scalp.features.liquidity_runtime import SCHEMA_70D

    print("liquidity_runtime.SCHEMA_70D:", SCHEMA_70D, "(ACTIVE_RUNTIME)")
except Exception as e:
    print("liquidity_runtime import err:", e)
try:
    from nexus_scalp.shadow.shadow70.models import SHADOW70_SCHEMA_ID

    print("shadow70.SHADOW70_SCHEMA_ID:", SHADOW70_SCHEMA_ID, "(SHADOW)")
except Exception as e:
    print("shadow70 import err:", e)
try:
    from nexus_scalp.model_generation.schema_v2 import SEVENTY_D_SCHEMA_ID

    print("schema_v2.SEVENTY_D_SCHEMA_ID:", SEVENTY_D_SCHEMA_ID, "(DATASET)")
except Exception as e:
    print("schema_v2 import err:", e)

print("\n=== DATASET MANIFEST PROVENANCE ===")

for ds in ("ds_d3f35b12d63148da", "ds_d3886c503d6c0901"):
    p = REPO / f"artifacts/model_generation/datasets/{ds}/dataset_manifest.json"
    if p.exists():
        m = json.loads(p.read_text(encoding="utf-8"))
        print(
            f"  {ds}: schema={m.get('feature_schema_id')} "
            f"schema_hash={m.get('feature_schema_hash')} "
            f"rows={m.get('row_counts', {}).get('total')} "
            f"tf={m.get('timeframe')}"
        )
    else:
        print(f"  {ds}: manifest missing")

return_code = 0
if SCHEMA_ID != "scalp_v3":
    print("\nFINDING: schema_contract canonical id is NOT scalp_v3")
    return_code = 1
if v3.dimension != 70:
    print("FINDING: registry scalp_v3 dim != 70")
    return_code = 1
print("\nEXIT:", return_code)
raise SystemExit(return_code)
