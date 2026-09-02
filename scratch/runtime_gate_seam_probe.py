"""Runtime-gate seam probe (Hermes-Main): empirically verify the exact
constructor/method seams the canonical gate intends to drive.

Read-only over the repo: no order, no network, no mutation outside the
system temp dir. Evidence printed as one JSON blob.
"""
from __future__ import annotations

import inspect
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
sys.path.insert(0, str(REPO / "src"))

out: dict[str, object] = {}

# 1) PaperMT5Adapter constructor seam
from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

sig = inspect.signature(PaperMT5Adapter.__init__)
out["paper_adapter_params"] = list(sig.parameters)[:6]

# 2) LiveEngine tick-pipeline attribute seams (from source, cheap)
import ast

le_src = (REPO / "src" / "nexus_scalp" / "application" / "live_engine.py").read_text(
    encoding="utf-8", errors="replace"
)
tree = ast.parse(le_src)
out["live_engine_lines"] = len(le_src.splitlines())
# find _process_tick_pipeline and list self.<attr> reads inside it
attrs: set[str] = set()
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_process_tick_pipeline":
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "self"
            ):
                attrs.add(sub.attr)
out["pipeline_attrs_sample"] = sorted(
    a
    for a in attrs
    if a
    in {
        "signal_policy",
        "risk_engine",
        "order_manager",
        "experience_engine",
        "intelligence_gate",
        "news_gate",
        "news_engine",
        "live_freshness_gate",
        "audit",
        "regime_classifier",
        "liquidity_governor",
        "feature_engine",
        "aggregator",
        "candle_intel",
        "shadow",
        "shadow70",
    }
)

# 3) create_app signature
from nexus_scalp.web.server import create_app

out["create_app_params"] = list(inspect.signature(create_app).parameters)[:8]

# 4) Migration engine status API
from nexus_scalp.database.engine import DatabaseMigrationEngine

out["migration_methods"] = [m for m in ("plan", "status", "migrate", "expected_version") if hasattr(DatabaseMigrationEngine, m)]
mig_sig = inspect.signature(DatabaseMigrationEngine.__init__)
out["migration_init_params"] = list(mig_sig.parameters)[:8]

# 5) SignalPolicy / RiskEngine constructor seams
from nexus_scalp.risk.risk_engine import RiskEngine
from nexus_scalp.signals.policy import SignalPolicy

out["risk_params"] = list(inspect.signature(RiskEngine.__init__).parameters)[:6]
out["policy_params"] = list(inspect.signature(SignalPolicy.__init__).parameters)[:6]

# 6) OrderLifecycleManager constructor seam
from nexus_scalp.execution.order_manager import OrderLifecycleManager

out["om_params"] = list(inspect.signature(OrderLifecycleManager.__init__).parameters)[:10]

# 7) notifier constructibility (telegram disabled)
from nexus_scalp.observability.telegram_notifier import TelegramNotifier

try:
    n = TelegramNotifier(enabled=False, bot_token="", admin_id="")
    out["notifier_disabled_ok"] = True
except Exception as exc:  # pragma: no cover
    out["notifier_disabled_ok"] = f"{type(exc).__name__}: {exc}"

# 8) AppConfig from base.yaml + paper adapter round-trip + migration status on a temp DB
from nexus_scalp.configuration.config import AppConfig

cfg = AppConfig.load_from_yaml(REPO / "configs" / "base.yaml")
out["base_yaml_mode"] = cfg.execution.mode.value
out["base_yaml_artifact"] = cfg.model.model_artifact_path
out["base_yaml_liquidity"] = getattr(cfg.model, "liquidity_features_enabled", None)

with tempfile.TemporaryDirectory() as td:
    tdp = Path(td)
    adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
    out["paper_connect"] = adapter.connect()
    tick = adapter.get_last_tick("XAUUSD")
    out["paper_tick_ok"] = tick is not None and tick.bid > 0
    bars = adapter.get_historical_bars("XAUUSD", "M1", 240)
    out["paper_bars_ok"] = bool(bars) and len(bars) == 240
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    repo = AuditRepository(db_url=f"sqlite:///{tdp / 'probe_audit.db'}", flush_interval_sec=0.05)
    out["audit_repo_ok"] = repo is not None
    # migration engine on the temp DB
    try:
        mig = DatabaseMigrationEngine(db_path=tdp / "probe_audit.db")
        st = mig.status()
        out["migration_status_keys"] = sorted(list(st.keys()))[:10]
        out["migration_current_expected"] = (st.get("current"), st.get("expected"))
    except Exception as exc:
        out["migration_status_error"] = f"{type(exc).__name__}: {exc}"
    repo.close()

# 9) schema contract import-time guards
from nexus_scalp.features.schema_contract import DIMENSION, SCHEMA_ID, feature_schema_hash

out["schema"] = {
    "schema_id": SCHEMA_ID,
    "dimension": DIMENSION,
    "hash": feature_schema_hash(),
}

# 10) settings service (boot-wired inside LiveEngine)
from nexus_scalp.settings.service import load_settings_service

svc = load_settings_service()
out["settings_service_ok"] = svc is not None

print(json.dumps(out, indent=1, default=str))
