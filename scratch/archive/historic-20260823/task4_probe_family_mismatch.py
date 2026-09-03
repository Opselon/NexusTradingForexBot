"""TASK-4 probe: does candidate validation backtest the candidate's OWN family
or the WHOLE dataset (all families mixed)?

Hypothesis D1: pipeline.validate_candidate runs every gate over dataset.samples
unfiltered by the candidate's context fingerprint -> a candidate discovered
from family F is validated on trades from 22 families (mixed regimes/sessions/
volatility), so OOS/expectancy/robustness are NOT family-specific evidence.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.research.backtest import BacktestEngine
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.discovery import _context_fingerprint, discover_candidates
from nexus_scalp.research.metrics import compute_backtest
from nexus_scalp.research.models import ExecutionAssumptions, ResearchDataset
from nexus_scalp.research.registry import StrategyRegistry

DB = Path(__file__).resolve().parents[1] / "artifacts" / "audit.db"
repo = AuditRepository(db_url=f"sqlite:///{DB.as_posix()}")
ledger = ExperienceLedger(repo)
builder = ResearchDatasetBuilder(ledger)
ds = builder.build()

cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
print(f"discovered candidates: {len(cands)} (top families only, floor 20)")
families = defaultdict(list)
for s in ds.samples:
    families[_context_fingerprint(s)].append(s)

# For the largest families (>= floor 12 boundary to see the effect), compare:
# (a) whole-dataset backtest (what validate_candidate does)
# (b) family-only backtest (what it SHOULD do)
eng = BacktestEngine()
for fp, fam in sorted(families.items(), key=lambda kv: -len(kv[1]))[:6]:
    if len(fam) < 5:
        continue
    whole = compute_backtest(ds.samples, "STRAT-X", "v1", ds.dataset_id, ExecutionAssumptions())
    fam_bt = compute_backtest(fam, "STRAT-X", "v1", ds.dataset_id, ExecutionAssumptions())
    print(
        f"\nfam n={len(fam):3d} exp_family={fam_bt.expectancy_r:+.4f} "
        f"exp_whole_dataset={whole.expectancy_r:+.4f}  fp={fp}"
    )
    print(
        f"    family winrate={fam_bt.win_rate:.3f} (wins {fam_bt.wins}/losses {fam_bt.losses}) "
        f"vs whole winrate={whole.win_rate:.3f}"
    )

# Walk-forward / OOS also consume the whole dataset for every candidate:
from nexus_scalp.research.oos import OOSGate  # noqa: E402
from nexus_scalp.research.walkforward import WalkForwardEngine  # noqa: E402

print("\n=== OOS + WALK-FORWARD on whole dataset for a single-family candidate ===")
oggate = OOSGate()
wf_eng = WalkForwardEngine()
for fp, fam in sorted(families.items(), key=lambda kv: -len(kv[1]))[:2]:
    if len(fam) < 8:
        continue
    oos_whole = oggate.evaluate(ds, "STRAT-X", "v1")
    oos_fam = oggate.evaluate(
        ResearchDataset(dataset_id="fam", samples=fam, source_range={}), "STRAT-X", "v1"
    )
    print(f"fp={fp[:40]} n={len(fam)}")
    print(
        f"  OOS whole: in={oos_whole.in_sample_expectancy_r:+.4f} oos={oos_whole.oos_expectancy_r:+.4f} status={oos_whole.status}"
    )
    print(
        f"  OOS family: in={oos_fam.in_sample_expectancy_r:+.4f} oos={oos_fam.oos_expectancy_r:+.4f} status={oos_fam.status}"
    )

# Registry upsert behavior: does re-upsert of the same (id, version) overwrite results?
from nexus_scalp.research.models import CandidateLifecycle, StrategyRegistryEntry  # noqa: E402

print("\n=== REGISTRY UPSERT OVERWRITE CHECK (in-memory test DB) ===")
import os  # noqa: E402
import tempfile  # noqa: E402

# CodeQL py/insecure-temporary-file (#65): tempfile.mktemp is deprecated and
# racy (CWE-377). NamedTemporaryFile creates the file atomically with
# exclusive access; delete=False keeps it alive for the DB handle.
_tmp_f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_f.close()
tmp = _tmp_f.name
trepo = AuditRepository(db_url=f"sqlite:///{tmp}")
treg = StrategyRegistry(trepo)
entry1 = StrategyRegistryEntry(
    strategy_id="S1",
    strategy_version="v1",
    lifecycle=CandidateLifecycle.DISCOVERED,
    sample_count=10,
)
treg.upsert(entry1)
trepo._queue.join()
got1 = treg.get("S1", "v1")
print(
    "after DISCOVERED upsert: lifecycle=",
    got1.lifecycle.value if got1 else None,
    "sample_count=",
    got1.sample_count if got1 else None,
)
entry2 = entry1.model_copy(update={"lifecycle": CandidateLifecycle.VALIDATED, "sample_count": 99})
treg.upsert(entry2)
trepo._queue.join()
got2 = treg.get("S1", "v1")
print(
    "after VALIDATED upsert (same id+version): lifecycle=",
    got2.lifecycle.value if got2 else None,
    "sample_count=",
    got2.sample_count if got2 else None,
)
print("=> same (id,version) silently OVERWRITES prior results (definition mutation not guarded)")
trepo.close()
os.remove(tmp)
repo.close()
