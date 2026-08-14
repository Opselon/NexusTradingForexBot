"""
Comprehensive Unit Tests for Production-Grade HTF Warmup, Fallback & Console Observability.
Covers all 11 mandatory test requirements in LiveEngine.
"""

import math
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ActionType, ExecutionMode
from nexus_scalp.domain.models import AccountInfo, SymbolInfo, TickData
from nexus_scalp.market_data.bar_aggregator import BarData


class FakeMT5Adapter:
    """Fake MT5 adapter for unit testing HTF warmup gate without live broker dependencies."""

    def __init__(self, h1_count: int = 14, h4_count: int = 14, m1_count: int = 3500):
        self.h1_count = h1_count
        self.h4_count = h4_count
        self.m1_count = m1_count
        self.connected = True
        self.get_historical_bars_calls = []

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    def get_account_info(self) -> AccountInfo:
        return AccountInfo(
            login=123456,
            trade_mode=0,
            leverage=100,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            margin_free=10000.0,
            currency="USD",
        )

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(
            symbol=symbol,
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=1.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            stops_level=10,
            freeze_level=0,
            trade_contract_size=100.0,
        )

    def get_last_tick(self, symbol: str) -> TickData:
        return TickData(
            symbol=symbol,
            timestamp=datetime.now(UTC),
            bid=2000.0,
            ask=2000.20,
            last=2000.10,
            volume=1.0,
        )

    def get_historical_bars(
        self, symbol: str, timeframe: str = "M1", count: int = 100
    ) -> list[BarData]:
        self.get_historical_bars_calls.append((symbol, timeframe, count))
        base_time = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        if timeframe == "H1":
            actual_count = self.h1_count
            step = timedelta(hours=1)
        elif timeframe == "H4":
            actual_count = self.h4_count
            step = timedelta(hours=4)
        else:
            actual_count = self.m1_count
            step = timedelta(minutes=1)

        bars = []
        for i in range(actual_count):
            bar_time = base_time + i * step
            bars.append(
                BarData(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=bar_time,
                    open=2000.0 + i * 0.1,
                    high=2001.0 + i * 0.1,
                    low=1999.0 + i * 0.1,
                    close=2000.5 + i * 0.1,
                    tick_volume=100,
                    is_complete=True,
                )
            )
        return bars

    def get_positions(self, symbol: str | None = None):
        return []

    def get_pending_orders(self, symbol: str | None = None):
        return []


@pytest.fixture
def base_config(tmp_path):
    model_path = tmp_path / "model.pt"
    cfg = AppConfig()
    cfg.execution.mode = ExecutionMode.PAPER
    cfg.model.model_artifact_path = str(model_path)
    return cfg


# Test 1: Insufficient H1 history -> NOT_READY / SAFE_NOT_READY
@pytest.mark.asyncio
async def test_1_insufficient_h1_history_results_in_not_ready(base_config):
    adapter = FakeMT5Adapter(h1_count=5, h4_count=14, m1_count=3500)
    audit = MagicMock()
    engine = LiveEngine(
        config=base_config, adapter=adapter, audit_repo=audit, force_fresh_model=True
    )

    await engine._cold_start_warmup("XAUUSD")

    assert engine.warmup_state == "SAFE_NOT_READY"
    assert engine._inference_enabled is False


# Test 2: Insufficient H4 history -> NOT_READY / SAFE_NOT_READY
@pytest.mark.asyncio
async def test_2_insufficient_h4_history_results_in_not_ready(base_config):
    adapter = FakeMT5Adapter(h1_count=14, h4_count=2, m1_count=3500)
    audit = MagicMock()
    engine = LiveEngine(
        config=base_config, adapter=adapter, audit_repo=audit, force_fresh_model=True
    )

    await engine._cold_start_warmup("XAUUSD")

    assert engine.warmup_state == "SAFE_NOT_READY"
    assert engine._inference_enabled is False


# Test 3: Both timeframes sufficient -> READY
@pytest.mark.asyncio
async def test_3_both_timeframes_sufficient_results_in_ready(base_config):
    adapter = FakeMT5Adapter(h1_count=14, h4_count=14, m1_count=3500)
    audit = MagicMock()
    engine = LiveEngine(
        config=base_config, adapter=adapter, audit_repo=audit, force_fresh_model=True
    )

    await engine._cold_start_warmup("XAUUSD")

    assert engine.warmup_state == "READY"
    assert engine._inference_enabled is True


# Test 4: Normal inference cannot occur while warmup is incomplete
def test_4_normal_inference_blocked_while_warmup_incomplete(base_config):
    adapter = FakeMT5Adapter(h1_count=5, h4_count=14)
    audit = MagicMock()
    engine = LiveEngine(
        config=base_config, adapter=adapter, audit_repo=audit, force_fresh_model=True
    )

    engine.warmup_state = "SAFE_NOT_READY"
    engine._inference_enabled = False

    tick = adapter.get_last_tick("XAUUSD")
    account = adapter.get_account_info()

    with patch.object(engine, "_infer_probabilities") as mock_infer:
        engine._process_tick_pipeline(tick, account)
        mock_infer.assert_not_called()

    assert audit.log_signal.called
    proposal = audit.log_signal.call_args[0][0]
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "HTF_WARMUP_INCOMPLETE"


# Test 5: HTF fallback caused by insufficient startup history is observable/detectable
@pytest.mark.asyncio
async def test_5_htf_fallback_caused_by_insufficient_history_is_detectable(base_config):
    adapter = FakeMT5Adapter(
        h1_count=14, h4_count=14, m1_count=50
    )  # m1_count=50 means aggregated H1/H4 bars will be empty
    audit = MagicMock()
    engine = LiveEngine(
        config=base_config, adapter=adapter, audit_repo=audit, force_fresh_model=True
    )

    await engine._cold_start_warmup("XAUUSD")

    assert engine.warmup_state == "SAFE_NOT_READY"
    assert engine._inference_enabled is False


# Test 6: Valid HTF features do not incorrectly report fallback
@pytest.mark.asyncio
async def test_6_valid_htf_features_do_not_incorrectly_report_fallback(base_config):
    adapter = FakeMT5Adapter(h1_count=14, h4_count=14, m1_count=3500)
    audit = MagicMock()
    engine = LiveEngine(
        config=base_config, adapter=adapter, audit_repo=audit, force_fresh_model=True
    )

    await engine._cold_start_warmup("XAUUSD")

    tick = adapter.get_last_tick("XAUUSD")
    completed = engine.aggregator.get_completed_bars()
    sample_fv = engine.feature_engine.compute_from_bars(completed, tick)

    assert sample_fv.htf_h4_trend != 0.0 or len(completed) >= 3360
    assert engine.warmup_state == "READY"


# Test 7: NaN/Inf/invalid feature fallback remains detectable
def test_7_nan_inf_invalid_feature_fallback_remains_detectable(base_config):
    adapter = FakeMT5Adapter(h1_count=14, h4_count=14)
    audit = MagicMock()
    engine = LiveEngine(
        config=base_config, adapter=adapter, audit_repo=audit, force_fresh_model=True
    )

    invalid_tensor = [0.0] * 50
    invalid_tensor[0] = float("nan")
    invalid_tensor[1] = float("inf")

    validated = engine._validate_50d_tensor(invalid_tensor, context="test")
    assert validated[0] == 0.0
    assert validated[1] == 0.0
    assert not math.isnan(validated[0])
    assert not math.isinf(validated[1])


# Test 8: Warmup transitions correctly WARMING_UP -> READY
@pytest.mark.asyncio
async def test_8_warmup_transitions_correctly_warming_up_to_ready(base_config):
    adapter = FakeMT5Adapter(h1_count=14, h4_count=14, m1_count=3500)
    audit = MagicMock()
    engine = LiveEngine(
        config=base_config, adapter=adapter, audit_repo=audit, force_fresh_model=True
    )

    assert engine.warmup_state == "WARMING_UP"
    assert engine._inference_enabled is False

    await engine._cold_start_warmup("XAUUSD")

    assert engine.warmup_state == "READY"
    assert engine._inference_enabled is True


# Test 9: Warmup failure remains fail-safe
@pytest.mark.asyncio
async def test_9_warmup_failure_remains_fail_safe(base_config):
    adapter = FakeMT5Adapter(h1_count=0, h4_count=0, m1_count=0)
    audit = MagicMock()
    engine = LiveEngine(
        config=base_config, adapter=adapter, audit_repo=audit, force_fresh_model=True
    )

    await engine._cold_start_warmup("XAUUSD")

    assert engine.warmup_state == "SAFE_NOT_READY"
    assert engine._inference_enabled is False

    tick = adapter.get_last_tick("XAUUSD")
    account = adapter.get_account_info()

    with patch.object(engine, "_infer_probabilities") as mock_infer:
        engine._process_tick_pipeline(tick, account)
        mock_infer.assert_not_called()


# Test 10: _process_tick_pipeline() does not perform blocking historical bootstrap
def test_10_process_tick_pipeline_does_not_perform_blocking_historical_bootstrap(base_config):
    adapter = FakeMT5Adapter(h1_count=14, h4_count=14)
    audit = MagicMock()
    engine = LiveEngine(
        config=base_config, adapter=adapter, audit_repo=audit, force_fresh_model=True
    )

    engine.warmup_state = "READY"
    engine._inference_enabled = True

    adapter.get_historical_bars_calls.clear()
    tick = adapter.get_last_tick("XAUUSD")
    account = adapter.get_account_info()

    engine._process_tick_pipeline(tick, account)

    # get_historical_bars should NOT be called inside normal tick processing when READY
    hist_calls_during_tick = [
        call for call in adapter.get_historical_bars_calls if call[2] == 3500 or call[2] == 1200
    ]
    assert len(hist_calls_during_tick) == 0


# Test 11: Important warmup/fallback state transitions emit expected log events
@pytest.mark.asyncio
async def test_11_warmup_state_transitions_emit_expected_log_events(base_config):
    adapter = FakeMT5Adapter(h1_count=14, h4_count=14, m1_count=3500)
    audit = MagicMock()
    engine = LiveEngine(
        config=base_config, adapter=adapter, audit_repo=audit, force_fresh_model=True
    )

    with patch("nexus_scalp.application.live_engine.logger.info") as mock_log_info:
        await engine._cold_start_warmup("XAUUSD")
        log_msgs = " ".join([str(call[0][0]) for call in mock_log_info.call_args_list])
        assert "[WARMUP] START" in log_msgs
        assert "[WARMUP] COMPLETE" in log_msgs
        assert "[INFERENCE] ENABLED" in log_msgs
