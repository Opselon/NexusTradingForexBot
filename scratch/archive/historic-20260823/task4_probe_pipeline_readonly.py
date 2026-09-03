"""TASK-4 probe: run the real research pipeline READ-ONLY against artifacts/audit.db.

Measures the full lineage: ledger -> outcomes -> dataset -> discovery, and
computes the rejection taxonomy / family distribution / zero-substitution
census. Does NOT write to the DB, does NOT modify thresholds.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.discovery import MIN_FAMILY_SAMPLES, discover_candidates
from nexus_scalp.research.registry import StrategyRegistry

DB = Path(__file__).resolve().parents[1] / "artifacts" / "audit.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
c = conn.cursor()


def q(sql: str, *a):
    return c.execute(sql, a).fetchall()


print("=== 0. RAW COUNTS ===")
print("experiences:", q("SELECT COUNT(*) FROM audit_experiences")[0][0])
print("outcomes:", q("SELECT COUNT(*) FROM audit_experience_outcomes")[0][0])
print("ledger CLOSED:", q("SELECT COUNT(*) FROM audit_ledger WHERE status='CLOSED'")[0][0])
print("registry:", q("SELECT COUNT(*) FROM strategy_registry")[0][0])
print("research_runs:", q("SELECT COUNT(*) FROM research_runs")[0][0])

print()
print("=== 1. ZERO-SUBSTITUTION CENSUS (all outcomes) ===")
tot, r0, p0, exec_, closed = q("""
    SELECT COUNT(*),
           SUM(CASE WHEN realized_r_multiple = 0.0 THEN 1 ELSE 0 END),
           SUM(CASE WHEN realized_pnl_usd = 0.0 THEN 1 ELSE 0 END),
           SUM(is_executed), SUM(is_closed)
    FROM audit_experience_outcomes
""")[0]
print(f"total={tot} r==0: {r0} pnl==0: {p0} executed={exec_} closed={closed}")

# Where do the zero-R outcomes come from? reconstruction_source in payload
src_cnt: Counter[str] = Counter()
zero_src: Counter[str] = Counter()
payload_rows = q("SELECT payload FROM audit_experience_outcomes")
for (p,) in payload_rows:
    try:
        d = json.loads(p)
    except Exception:
        continue
    bo = d.get("broker_outcome") or {}
    src = bo.get("reconstruction_source") or "NO_BROKER_OUTCOME"
    src_cnt[src] += 1
    if float(d.get("realized_r_multiple", 0.0)) == 0.0:
        zero_src[src] += 1
print("reconstruction_source distribution:", dict(src_cnt))
print("zero-R by reconstruction_source:", dict(zero_src))

print()
print("=== 2. OUTCOME R DISTRIBUTION (non-zero) ===")
rows = q("""
    SELECT idempotency_key, realized_r_multiple, realized_pnl_usd, exit_reason,
           outcome_timestamp FROM audit_experience_outcomes
    WHERE realized_r_multiple <> 0.0 ORDER BY outcome_timestamp
""")
print(f"non-zero-R outcomes: {len(rows)}")
for r in rows[:12]:
    print("  ", r[0][:30], "R=", round(r[1], 4), "pnl=", round(r[2], 2), r[3], r[4][:19])

print()
print("=== 3. RESEARCH DATASET PATH (read-only) ===")
repo = AuditRepository(db_url=f"sqlite:///{DB.as_posix()}")
ledger = ExperienceLedger(repo)
builder = ResearchDatasetBuilder(ledger)
ds = builder.build()
print(f"dataset_id={ds.dataset_id} samples={len(ds.samples)}")
print("schema_ids:", ds.schema_ids)

zero_r = sum(1 for s in ds.samples if s.realized_r == 0.0)
print(f"samples with realized_r==0.0: {zero_r} / {len(ds.samples)}")
print(
    f"samples with realized_pnl_usd==0.0: {sum(1 for s in ds.samples if s.realized_pnl_usd == 0.0)}"
)

# family distribution exactly as discovery computes it
from nexus_scalp.research.discovery import _context_fingerprint  # noqa: E402

fam: dict[str, list] = defaultdict(list)
for s in ds.samples:
    fam[_context_fingerprint(s)].append(s)
print(f"\ndistinct families: {len(fam)}")
sizes = sorted((len(v) for v in fam.values()), reverse=True)
print("family-size distribution:", sizes)
print(
    f"largest={max(sizes) if sizes else 0} median={sorted(sizes)[len(sizes) // 2] if sizes else 0} smallest={min(sizes) if sizes else 0}"
)
print(
    f"families >= MIN_FAMILY_SAMPLES({MIN_FAMILY_SAMPLES}): {sum(1 for v in fam.values() if len(v) >= MIN_FAMILY_SAMPLES)}"
)
for fp, v in sorted(fam.items(), key=lambda kv: -len(kv[1]))[:8]:
    exp = sum(x.realized_r for x in v) / len(v)
    print(f"  n={len(v):3d} exp_r={exp:+.4f} {fp}")

cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
print(f"\ncandidates discovered: {len(cands)}")

# top rejection reasons
reasons: Counter[str] = Counter()
for _fp, v in fam.items():
    if len(v) < MIN_FAMILY_SAMPLES:
        reasons["INSUFFICIENT_FAMILY_SAMPLES"] += 1
        continue
    exp = sum(x.realized_r for x in v) / len(v)
    if exp < 0.10:
        reasons["LOW_EXPECTANCY"] += 1
print("rejection reason counts (family level):", dict(reasons))
print()
print("=== 4. REGISTRY (read-only) ===")
reg = StrategyRegistry(repo)
print("registry count:", reg.count())
for e in reg.list()[:5]:
    print("  ", e.strategy_id, e.strategy_version, e.lifecycle, e.sample_count)

print()
print("=== 5. DEDUP CHECK: duplicate idempotency keys in experiences ===")
print(
    "distinct idempotency_keys:",
    q("SELECT COUNT(DISTINCT idempotency_key) FROM audit_experiences")[0][0],
)
print("rows:", q("SELECT COUNT(*) FROM audit_experiences")[0][0])
print()
print("=== 6. SAMPLE-COUNT SEMANTICS CHECK ===")
# Does a research sample represent one economic trade? Count outcome rows per execution_id
dup_exec = q("""
    SELECT execution_id, COUNT(*) FROM audit_experience_outcomes
    WHERE execution_id <> '' GROUP BY execution_id HAVING COUNT(*) > 1
""")
print("outcome rows sharing an execution_id (split fills?):", len(dup_exec))
for r in dup_exec[:5]:
    print("  ", r[0], r[1])
