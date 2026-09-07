# Phase 12 — verify champion metrics path end-to-end + comparison BLOCKED/EVALUATED contract
import hashlib
import json
from pathlib import Path

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.model_lifecycle.champion import ChampionManager
from nexus_scalp.model_lifecycle.comparison import ChampionChallengerComparator
from nexus_scalp.model_lifecycle.champion_metrics import ChampionMetricsProvider, blocked_comparison

audit = AuditRepository()
ledger = ExperienceLedger(audit)
cm = ChampionManager(
    artifact_path="artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
    model_id="primary_scalp",
    model_version="v3.0",
    feature_schema_id="scalp_v3",
    feature_dimension=70,
    num_classes=3,
)
champ = cm.load_champion()
metrics = ChampionMetricsProvider().load(champ)
assert metrics is not None, "champion metrics must load from the baseline artifact"
print("champion metrics provenance:", metrics["provenance"])
print("  expectancy_r:", metrics["expectancy_r"])
print("  oos_expectancy_r:", metrics["oos_expectancy_r"])
print("  evaluation_id:", metrics["evaluation_id"])

# The comparison contract: challenger with NEGATIVE expectancy must be ineligible
# against the REAL champion metrics (both negative -> challenger must still be
# positive to be eligible; comparator rule: exp_t <= 0 -> ineligible).
challenger_negative = {
    "expectancy_r": -0.2,
    "max_drawdown_r": 1.0,
    "oos_expectancy_r": -0.3,
    "tail_loss_count": 2,
    "robustness_status": "PASS",
    "stability": 0.8,
}
cmp_result = ChampionChallengerComparator().compare(
    champion=metrics, challenger=challenger_negative, run_id="tr_phase12_probe7"
)
print("negative challenger eligible:", cmp_result.eligible)
print("reasons:", cmp_result.reasons[:2])
assert cmp_result.eligible is False

# BLOCKED path: baseline dir that has no artifact for this champion's bytes
from nexus_scalp.model_lifecycle import champion_metrics as cm_mod
import tempfile as _tempfile
_empty_dir = Path(_tempfile.mkdtemp())
missing = ChampionMetricsProvider(baseline_dir=_empty_dir).load(champ)
print("missing-artifact verdict:", blocked_comparison(champ)["reason"])
assert missing is None
print("ALL COMPARISON CONTRACT CHECKS PASSED")
