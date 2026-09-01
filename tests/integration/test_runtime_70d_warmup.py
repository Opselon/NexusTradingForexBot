"""AGENT-2 P0 WARMUP TRACE — the exact failing path: LiveEngine._cold_start_warmup.

Non-invasive: launches the REAL warmup coroutine against a stubbed adapter
(real bars, no broker) with a REAL 70D bundle loaded, and traces the exact
vector that reaches the record builder. Regression-guards:

    IndexError: list index out of range   (live_engine.py:3044 dictcomp)

Does NOT fix anything; a reproduced failure FAILS LOUD with producer/consumer
evidence for Agent 1.
"""

from __future__ import annotations

import asyncio
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Fixture: real engine with real 70D artifact, stubbed broker (read-only)
# ---------------------------------------------------------------------------


def _make_bars(n: int) -> list:
    from nexus_scalp.market_data.bar_aggregator import BarData

    t0 = datetime.now(UTC) - timedelta(minutes=n + 2)
    bars: list[BarData] = []
    price = 3300.0
    for i in range(n):
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
    return bars


@pytest.fixture()
def warm_engine(tmp_path, monkeypatch):
    """A REAL LiveEngine constructed the way the launcher does, with:
    - the REAL configured 70D artifact loaded (no artifact mutation),
    - a stubbed adapter serving deterministic synthetic bars (no broker, no
      orders — adapter methods are the only monkeypatched seam),
    - a tmp artifact dir so nothing under artifacts/ can be touched."""
    from nexus_scalp.application.live_engine import LiveEngine
    from tests.helpers.runtime_70d_probe import (
        artifact_path_from_config,
        load_effective_config,
    )

    cfg = load_effective_config()
    model_path = artifact_path_from_config(cfg)
    if not model_path.exists():
        pytest.skip(f"configured artifact absent: {model_path}")

    engine = LiveEngine.__new__(LiveEngine)
    # Minimal attribute set for _cold_start_warmup: replicate the launcher
    # construction order for the attributes the warmup path reads. We do NOT
    # call __init__ (it binds adapters/DB workers); instead we exercise the
    # SAME code path with the attribute surface the warmup touches.
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine
    from nexus_scalp.market_data.bar_aggregator import BarAggregator

    engine.config = cfg
    engine.feature_engine = ScalpFeatureEngine(symbol="XAUUSD")
    engine.aggregator = BarAggregator("XAUUSD")
    engine._warmup_attempt = 0
    engine.H1_REQUIRED_BARS = 60
    engine.H4_REQUIRED_BARS = 60
    from collections import deque

    engine._rolling_feature_records = deque(maxlen=4000)
    engine.warmup_state = "INIT"
    engine._inference_enabled = False
    engine.server_state = None
    engine.liquidity_governor = None  # warm path does not require it
    engine.symbol = "XAUUSD"

    # Load the REAL bundle via the engine's own loader (read-only w.r.t. files:
    # torch.load / np.load only). Point any loader side effects at tmp_path by
    # patching the artifact path on the loaded bundle copy is unnecessary —
    # _load_or_create_bundle only READS.
    engine._bundle_lock = __import__("threading").RLock()
    engine._bundle = engine._load_or_create_bundle(model_path=model_path, force_fresh=False)

    bars = _make_bars(3500)

    class _StubAdapter:
        """Deterministic no-broker adapter (read-only, never trades)."""

        def get_historical_bars(self, symbol, timeframe, count):
            return bars[-count:] if count and count <= len(bars) else bars

        def get_tick(self, symbol):
            last = bars[-1]
            return SimpleNamespace(
                symbol=symbol,
                timestamp=last.timestamp,
                bid=last.close,
                ask=last.close + 0.20,
                last=0.0,
                volume=float(last.tick_volume),
                flags=0,
            )

    engine.adapter = _StubAdapter()

    # evaluate_warmup_readiness reads H1/H4 bars only via adapter stub; keep it
    # real so the full warmup path is exercised end-to-end.
    return engine


# ---------------------------------------------------------------------------
# THE trace: what exactly enters _cold_start_warmup's record builder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p0_cold_start_warmup_receives_matching_vector(warm_engine, capsys):
    """Runs the REAL _cold_start_warmup. EXPECTED on a healthy 70D runtime:
    warmup completes, buffer records are 70-wide (bundle contract), no
    IndexError. On divergence the test fails with producer/consumer evidence."""
    engine = warm_engine

    # Pre-flight: capture what the record builder will resolve.
    rec_dim = int(engine._retrain_record_dim())
    bundle_scaler_dim = engine._bundle.scaler.dimension()
    bundle_model_dim = int(engine._bundle.model.num_features)

    # Producer width check on a representative window BEFORE running warmup
    bars = engine.aggregator.get_completed_bars()
    assert not bars  # aggregator starts empty; warmup seeds it

    errors: list[str] = []
    try:
        await engine._cold_start_warmup("XAUUSD")
    except IndexError as exc:
        errors.append(f"IndexError: {exc}")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    records = list(engine._rolling_feature_records)
    widths = sorted({len(r) - 6 for r in records})  # minus OHLC/spread/atr fields
    buf = {
        "bundle_scaler_dim": bundle_scaler_dim,
        "bundle_model_dim": bundle_model_dim,
        "retrain_record_dim": rec_dim,
        "records_built": len(records),
        "record_widths": widths,
        "errors": errors,
    }
    print("[WARMUP]")
    for k, v in buf.items():
        print(f"  {k}={v}")
    print(f"[WARMUP_STATUS] {'FAIL' if errors else 'PASS'}")

    assert not errors, (
        "COLD_START_WARMUP_FAILED\n"
        f"bundle_scaler_dim={bundle_scaler_dim} bundle_model_dim={bundle_model_dim}\n"
        f"retrain_record_dim={rec_dim}\n"
        f"records_built={len(records)} record_widths={widths}\n"
        f"errors={errors}\n"
        "producer=feature_engine.compute_from_bars().to_tensor_input() (base 50D)\n"
        "consumer=_cold_start_warmup record builder (range(_retrain_record_dim()))"
    )

    # Healthy-state assertions: warmup completed and the buffer carries the
    # bundle contract width.
    assert records, "warmup produced zero retrain records"
    assert widths == [rec_dim], (
        f"BUFFER_WIDTH_MISMATCH: record_widths={widths} vs retrain_record_dim={rec_dim}"
    )
    assert engine.warmup_state in {"READY", "SAFE_NOT_READY"}, f"warmup_state={engine.warmup_state}"
    out = capsys.readouterr().out
    assert "IndexError" not in out


@pytest.mark.asyncio
async def test_p0_cold_start_warmup_buffer_contract_is_scalp_v3_70d(warm_engine):
    """Q6-Q13: the vector reaching the record builder must satisfy the 70D
    contract; buffer schema must be scalp_v3 @ 70D (bundle-driven)."""
    engine = warm_engine
    rec_dim = int(engine._retrain_record_dim())
    eff_schema = str(engine.effective_feature_schema_id)

    print(
        f"[BUFFER_CONTRACT]\nbuffer_schema={eff_schema}\n"
        f"buffer_record_dim={rec_dim}\nbundle_artifact={engine._bundle.artifact_path}"
    )
    assert rec_dim == 70, f"BUFFER_RECORD_DIM={rec_dim} (expected 70 for 70D champion)"
    assert eff_schema == "scalp_v3", f"BUFFER_SCHEMA={eff_schema} (expected scalp_v3)"

    await engine._cold_start_warmup("XAUUSD")
    records = list(engine._rolling_feature_records)
    assert records, "no records after warmup"
    first = records[0]
    feat_fields = [k for k in first if k.startswith("feat_")]
    assert len(feat_fields) == 70, (
        f"RETRAIN_RECORD_FIELDS={len(feat_fields)} (expected feat_0..feat_69); "
        f"got {sorted(feat_fields)[:3]}..{sorted(feat_fields)[-3:]}"
    )
    # feat_69 present == 70-wide record (guard against silent truncation)
    assert "feat_69" in first and "feat_0" in first
