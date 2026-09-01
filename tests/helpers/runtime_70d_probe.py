"""AGENT-2 RUNTIME 70D CONTRACT PROBE — test-only observability helpers.

NON-INTERFERENCE CONTRACT (Agent 2 mission):
  * This module is TEST-ONLY. Nothing here may be imported by src/.
  * It observes REAL runtime objects (loaded bundle, schema registry,
    live-engine methods bound onto a minimal stub) — it never re-implements
    production feature-building logic and never fabricates dimensions.
  * When a quantity is not measurable it reports NOT_PRESENT / None rather
    than inventing a value.

Purpose: give Agent 1 (production repair owner) precise, staged evidence of
where the 70D contract diverges: feature producer width vs retrain record
width vs buffer width vs scaler width vs trainer width.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Canonical production artifact currently configured (configs/base.yaml +
#: ModelConfig default). Read from the real AppConfig — never hardcoded here.
DEFAULT_ARTIFACT_REL = "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"


# ---------------------------------------------------------------------------
# Contract snapshot
# ---------------------------------------------------------------------------


@dataclass
class ContractSnapshot:
    """Normalized snapshot of the runtime contract, gathered from REAL objects.

    Any field that cannot be measured stays None — tests render that as
    NOT_PRESENT rather than guessing.
    """

    schema_id: str | None = None
    schema_dimension: int | None = None
    feature_columns: tuple[str, ...] | None = None
    model_dim: int | None = None
    scaler_dim: int | None = None
    meta_schema_id: str | None = None
    artifact_path: str | None = None
    liquidity_enabled: bool | None = None
    trainer_dim: int | None = None
    retrain_record_dim: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "schema_dimension": self.schema_dimension,
            "feature_columns_count": len(self.feature_columns)
            if self.feature_columns is not None
            else None,
            "model_dim": self.model_dim,
            "scaler_dim": self.scaler_dim,
            "meta_schema_id": self.meta_schema_id,
            "artifact_path": self.artifact_path,
            "liquidity_enabled": self.liquidity_enabled,
            "trainer_dim": self.trainer_dim,
            "retrain_record_dim": self.retrain_record_dim,
            **{f"extra_{k}": v for k, v in self.extra.items()},
        }

    def render(self, title: str = "CONTRACT") -> str:
        """Grep-friendly [CONTRACT] trace block."""
        d = self.as_dict()
        lines = [f"[{title}]"]
        for k, v in d.items():
            if v is None:
                rendered = "NOT_PRESENT"
            elif isinstance(v, bool):
                rendered = "true" if v else "false"
            else:
                rendered = str(v)
            lines.append(f"{k}={rendered}")
        return "\n".join(lines)


def load_effective_config() -> Any:
    """Load the SAME config object the application uses (AppConfig + YAML).

    Read-only. Prefers configs/base.yaml (the launcher's config path);
    falls back to the pure AppConfig defaults when the file is absent.
    """
    from nexus_scalp.configuration.config import AppConfig

    base = REPO_ROOT / "configs" / "base.yaml"
    if base.exists():
        try:
            return AppConfig.load_from_yaml(base)
        except Exception:
            pass
    return AppConfig()


def artifact_path_from_config(cfg: Any) -> Path:
    return REPO_ROOT / str(cfg.model.model_artifact_path)


def probe_artifact(model_path: Path) -> dict[str, Any]:
    """Read the REAL artifact's declared contract (meta + scaler + checkpoint).

    Mirrors the resolution order the engine itself uses (BUG-141
    _declared_contract_dim_for_path) but read-only and side-effect free.
    Returns meta/scaler/checkpoint widths separately so a split between
    them is visible instead of collapsed into one number.
    """
    out: dict[str, Any] = {
        "meta_dim": None,
        "meta_schema_id": None,
        "meta_columns_count": None,
        "scaler_dim": None,
        "checkpoint_dim": None,
        "exists": model_path.exists(),
    }
    import numpy as np

    meta_path = model_path.with_suffix(".meta.json")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            dim = meta.get("feature_schema_dimension") or meta.get("num_features")
            if isinstance(dim, int) and dim > 0:
                out["meta_dim"] = dim
            out["meta_schema_id"] = meta.get("feature_schema_id")
            cols = meta.get("feature_columns")
            if isinstance(cols, list):
                out["meta_columns_count"] = len(cols)
        except Exception:
            pass
    scaler_path = model_path.with_suffix(".scaler.npz")
    if scaler_path.exists():
        try:
            data = np.load(scaler_path)
            out["scaler_dim"] = int(np.asarray(data["mean"]).shape[0])
        except Exception:
            pass
    if model_path.exists():
        try:
            import torch

            sd = torch.load(model_path, map_location="cpu")
            w = sd.get("input_projection.weight") if isinstance(sd, dict) else None
            if w is not None and hasattr(w, "shape") and len(w.shape) == 2:
                out["checkpoint_dim"] = int(w.shape[1])
        except Exception:
            pass
    return out


def build_engine_stub_with_real_methods(bundle_dim: int | None = 70):
    """Minimal stub carrying the REAL LiveEngine contract methods.

    Binds LiveEngine.effective_feature_dim / effective_feature_schema_id /
    effective_feature_cols / _retrain_record_dim onto a lightweight object
    (no engine construction, no adapter, no threads). This is exactly the
    pattern already used by tests/unit/test_bug185_record_contract_alignment.py.
    """
    from nexus_scalp.application.live_engine import LiveEngine

    class _Bundle:
        def __init__(self, dim: int | None):
            self.model = SimpleNamespace(num_features=dim or 0)
            self.scaler = SimpleNamespace(dimension=lambda: dim) if dim is not None else None

    class _EngineLike:
        _bundle_lock = threading.RLock()
        FEATURE_DIM = LiveEngine.FEATURE_DIM
        FEATURE_SCHEMA_ID = LiveEngine.FEATURE_SCHEMA_ID

        def __init__(self):
            self._bundle = _Bundle(bundle_dim) if bundle_dim is not None else None

    e = _EngineLike()
    e.effective_feature_dim = property(LiveEngine.effective_feature_dim.fget).__get__(e)
    e.effective_feature_schema_id = property(LiveEngine.effective_feature_schema_id.fget).__get__(e)
    e.effective_feature_cols = property(LiveEngine.effective_feature_cols.fget).__get__(e)
    e._retrain_record_dim = LiveEngine._retrain_record_dim.__get__(e)
    return e


def canonical_70d_names() -> tuple[str, ...]:
    """Real canonical names from the SSOT schema_contract module."""
    from nexus_scalp.features.schema_contract import canonical_feature_names

    return canonical_feature_names()


def collect_contract_snapshot() -> ContractSnapshot:
    """Gather the full contract snapshot from real runtime sources."""
    from nexus_scalp.features.schema import FEATURE_SCHEMAS
    from nexus_scalp.features.schema_contract import DIMENSION as SSOT_DIM
    from nexus_scalp.features.schema_contract import SCHEMA_ID as SSOT_SCHEMA_ID

    snap = ContractSnapshot()
    snap.schema_id = SSOT_SCHEMA_ID
    snap.schema_dimension = SSOT_DIM

    cfg = load_effective_config()
    snap.liquidity_enabled = bool(cfg.model.liquidity_features_enabled)

    model_path = artifact_path_from_config(cfg)
    snap.artifact_path = str(cfg.model.model_artifact_path)
    art = probe_artifact(model_path)
    snap.model_dim = art["checkpoint_dim"]
    snap.scaler_dim = art["scaler_dim"]
    snap.meta_schema_id = art["meta_schema_id"]
    snap.extra["meta_dim"] = art["meta_dim"]
    snap.extra["meta_columns_count"] = art["meta_columns_count"]

    try:
        names = canonical_70d_names()
        snap.feature_columns = names
    except Exception:
        snap.feature_columns = None

    try:
        stub = build_engine_stub_with_real_methods(snap.scaler_dim or snap.model_dim or None)
        snap.retrain_record_dim = int(stub._retrain_record_dim())
        snap.trainer_dim = int(stub.trainer.num_features) if hasattr(stub, "trainer") else None
        snap.extra["engine_effective_dim"] = int(stub.effective_feature_dim)
        snap.extra["engine_effective_schema_id"] = str(stub.effective_feature_schema_id)
    except Exception as exc:
        snap.extra["engine_stub_error"] = f"{type(exc).__name__}: {exc}"

    snap.extra["registry_active_schema_id"] = FEATURE_SCHEMAS.active.schema_id
    snap.extra["registry_active_dimension"] = FEATURE_SCHEMAS.active.dimension
    return snap


# ---------------------------------------------------------------------------
# Feature-pipeline trace (real producer, real merge functions)
# ---------------------------------------------------------------------------


def build_feature_pipeline_trace(
    bars_count: int = 240,
    *,
    liquidity_enabled: bool = True,
) -> dict[str, Any]:
    """Trace the REAL feature pipeline stages with deterministic synthetic bars.

    Uses the REAL producers:
      - ScalpFeatureEngine.compute_from_bars -> base block (fv.to_tensor_input())
      - LiquidityGovernor.compute_from_engine -> liquidity block (snapshot)
      - features70.assemble_70d / liquidity_runtime.build_70d_vector -> merge
      - LiveEngine._validate_50d_tensor / _validate_feature_vector gates

    Nothing here is invented: every dimension is measured from a returned
    object. NOT_PRESENT is reported for stages that cannot run.
    """
    import math
    from datetime import UTC, datetime, timedelta

    from nexus_scalp.features.scalp_features import ScalpFeatureEngine
    from nexus_scalp.market_data.bar_aggregator import BarData

    t0 = datetime.now(UTC) - timedelta(minutes=bars_count + 2)
    bars: list[BarData] = []
    price = 3300.0
    for i in range(bars_count):
        # Deterministic pseudo-random walk (no RNG state leakage between runs).
        step = math.sin(i * 0.37) * 0.8 + math.cos(i * 0.11) * 0.5
        o = price
        price = price + step
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=t0 + timedelta(minutes=i + 1),
                open=o,
                high=max(o, price) + 0.4,
                low=min(o, price) - 0.4,
                close=price,
                tick_volume=100 + (i % 7) * 5,
                is_complete=True,
            )
        )
    last = bars[-1]
    tick = SimpleNamespace(
        symbol="XAUUSD",
        timestamp=last.timestamp,
        bid=last.close,
        ask=last.close + 0.20,
        volume=last.tick_volume,
        last=0.0,
        flags=0,
    )

    trace: dict[str, Any] = {"bars_count": len(bars)}

    # 1) BASE 50D via the real producer
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    fv = engine.compute_from_bars(bars, tick)
    base = fv.to_tensor_input()
    trace["base_dim"] = len(base)
    trace["base_all_finite"] = all(math.isfinite(float(v)) for v in base)

    # 2) LIQUIDITY 10D via the real governor (same call the parity tests use)
    liq: list[float] | None = None
    gov_status = "NOT_PRESENT"
    causal = "NOT_PRESENT"
    try:
        from nexus_scalp.features.liquidity_runtime import LiquidityGovernor

        gov = LiquidityGovernor(enabled=liquidity_enabled)
        gov.compute_from_engine(
            bars=bars,
            mid_price=float(last.close),
            atr=float(fv.atr_m1),
            decision_at=last.timestamp,
        )
        snap = gov.last_snapshot
        if snap is not None:
            liq = [float(v) for v in snap.features]
        gov_status = "LIQUIDITY_CALCULATION_OK" if liq else "NO_SNAPSHOT"
        causal = str(gov.causal_state())
    except Exception as exc:
        gov_status = f"LIQUIDITY_CALCULATION_FAILED: {type(exc).__name__}: {exc}"
    trace["liquidity_dim"] = len(liq) if liq is not None else None
    trace["liquidity_status"] = gov_status
    trace["liquidity_causal_state"] = causal

    # 3) NEWS 10D (neutral context = documented no-news default, NOT invented data)
    news: list[float] | None = None
    try:
        from nexus_scalp.features.features70 import news_10d_from_context

        news = news_10d_from_context(None)
    except Exception:
        news = None
    trace["news_dim"] = len(news) if news is not None else None

    # 4) CANONICAL MERGE via the real runtime builder
    merged: list[float] | None = None
    merge_fn = "NOT_PRESENT"
    try:
        from nexus_scalp.features.liquidity_runtime import build_70d_vector

        if liq is not None:
            merged = build_70d_vector(base, family_10=news, liquidity_10=liq)
            merge_fn = "liquidity_runtime.build_70d_vector"
        else:
            merge_fn = "SKIPPED_NO_LIQUIDITY (build_70d_vector would raise)"
    except Exception as exc:
        trace["merge_error"] = f"{type(exc).__name__}: {exc}"
    trace["merged_dim"] = len(merged) if merged is not None else None
    trace["merge_fn"] = merge_fn

    # 5) Validation gates (real classmethods, bound to a stub)
    try:
        from nexus_scalp.application.live_engine import LiveEngine

        stub50 = build_engine_stub_with_real_methods(50)
        v50 = LiveEngine._validate_50d_tensor.__func__(stub50, base, context="trace_probe")
        trace["validate_50d_ok"] = len(v50) == len(base)
        if merged is not None:
            stub70 = build_engine_stub_with_real_methods(70)
            v70 = LiveEngine._validate_feature_vector(stub70, merged, context="trace_probe")
            trace["validate_70d_ok"] = len(v70) == len(merged)
        else:
            trace["validate_70d_ok"] = None
    except Exception as exc:
        trace["validation_gate_error"] = f"{type(exc).__name__}: {exc}"
        trace["validate_50d_ok"] = False
        trace["validate_70d_ok"] = None

    # 6) RETRAIN RECORD construction — via the REAL production builder.
    #    BUG-185 P3: production no longer re-implements the record dict
    #    inline ({feat_i: base[i] for i in range(rec_dim)} — the IndexError
    #    class); it routes through LiveEngine._build_retrain_record, the
    #    canonical base|news|liquidity assembly with the refusal guard.
    #    The probe binds the SAME real method on the stub (mirroring the
    #    other stages) so the trace observes the repaired path, never a
    #    re-implementation of it.
    try:
        from nexus_scalp.application.live_engine import LiveEngine

        stub = build_engine_stub_with_real_methods(70)
        stub._validate_50d_tensor = LiveEngine._validate_50d_tensor.__get__(stub)
        rec_dim = int(stub._retrain_record_dim())
        stub._news_enabled = False
        stub.news_engine = None
        # Stage 2 already produced a REAL governor snapshot (LIQUIDITY_CALCULATION_OK);
        # attach THAT governor so the canonical builder sees a VALID causal state —
        # exactly what the live engine provides on the bar-close cadence.
        stub.liquidity_governor = gov
        record = None
        record_error = None
        try:
            record = LiveEngine._build_retrain_record(
                stub,
                base50=base,
                fv=fv,
                bar=last,
                spread=0.20,
                context="probe_retrain_record",
            )
        except Exception as exc:
            record_error = f"{type(exc).__name__}: {exc}"
        trace["retrain_record_dim"] = rec_dim
        trace["retrain_record_built_dim"] = (len(record) - 6) if isinstance(record, dict) else None
        trace["retrain_record_error"] = record_error
        trace["retrain_record_producer_width"] = len(base)
        trace["retrain_record_source"] = "LiveEngine._build_retrain_record"
    except Exception as exc:
        trace["retrain_probe_error"] = f"{type(exc).__name__}: {exc}"

    return trace


def render_feature_trace(trace: dict[str, Any], expected_dim: int) -> str:
    """RUNTIME_FEATURE_TRACE block, grep-friendly."""
    lines = ["RUNTIME_FEATURE_TRACE", "-" * 21]
    lines.append("schema=scalp_v3")
    lines.append(f"expected_dim={expected_dim}")
    lines.append(f"base_dim={trace.get('base_dim', 'NOT_PRESENT')}")
    lines.append(
        f"liquidity_dim={trace.get('liquidity_dim') if trace.get('liquidity_dim') is not None else 'NOT_PRESENT'}"
    )
    lines.append(
        f"news_dim={trace.get('news_dim') if trace.get('news_dim') is not None else 'NOT_PRESENT'}"
    )
    lines.append(
        f"final_dim={trace.get('merged_dim') if trace.get('merged_dim') is not None else 'NOT_PRESENT'}"
    )
    lines.append(f"merge_fn={trace.get('merge_fn', 'NOT_PRESENT')}")
    lines.append(f"liquidity_status={trace.get('liquidity_status', 'NOT_PRESENT')}")
    lines.append(f"liquidity_causal={trace.get('liquidity_causal_state', 'NOT_PRESENT')}")
    lines.append(f"retrain_record_dim={trace.get('retrain_record_dim', 'NOT_PRESENT')}")
    lines.append(
        f"retrain_record_built_dim={trace.get('retrain_record_built_dim') if trace.get('retrain_record_built_dim') is not None else 'NOT_PRESENT'}"
    )
    if trace.get("retrain_record_error"):
        lines.append(f"retrain_record_error={trace['retrain_record_error']}")
    if trace.get("merge_error"):
        lines.append(f"merge_error={trace['merge_error']}")
    if trace.get("validation_gate_error"):
        lines.append(f"validation_gate_error={trace['validation_gate_error']}")
    lines.append(f"validate_50d_ok={trace.get('validate_50d_ok')}")
    lines.append(f"validate_70d_ok={trace.get('validate_70d_ok')}")
    return "\n".join(lines)
