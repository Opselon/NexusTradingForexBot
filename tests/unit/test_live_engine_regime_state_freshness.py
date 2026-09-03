"""BUG-TDF-Q2 (TASK-NX-TDFQ2-TICKAGE, 2026-09-03): tick-freshness guard
for the regime reclassification seam.

Researcher TDF-R2 Q2/Q2b finding: the BUG-169 duplicate-tick path in
LiveEngine._process_tick_pipeline REUSES ``_regime_last_state`` without any
freshness check, so a frozen or duplicate-quote stream can hold the last
regime state (e.g. FREEZE_ALL / HIGH_SPREAD_CHOP) indefinitely. The
classifier's hysteresis guarantee ("never get stuck frozen") silently
assumes fresh ticks keep arriving — exactly what a frozen feed violates.

Fix shape (alarm-only, BUG-169 dedup contract preserved): when a duplicate
tick reuses the cached regime state and the last SUCCESSFUL classify_tick()
is older than ``algo.regime_state_max_age_sec`` (default 300s), the engine
emits a rate-limited structured WARNING naming the staleness. Forcing a
reclassification on duplicate data would push the duplicate into the
classifier's rolling rings (skewing tick_velocity / rv_5m / norm_ofi) and
break the BUG-169 contract, so the guard alarms and never reclassifies.

Test discipline (BUG-112/118): the module logger is monkeypatched — never
capsys/caplog. The clock is monkeypatched (no sleeps; xdist-safe). The
REAL guard method is bound onto a minimal stand-in (BUG-185 pattern) so no
full engine boot is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import yaml

from nexus_scalp.application import live_engine as le
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.regime_classifier import (
    MarketRegimeState,
    RecommendedExecutionType,
    RegimeReason,
    RegimeType,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASE_EPOCH = 1_000_000.0


def _make_state() -> MarketRegimeState:
    return MarketRegimeState(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        regime_type=RegimeType.HIGH_SPREAD_CHOP,
        regime_probability=0.9,
        order_flow_imbalance=0.0,
        realized_volatility_5m=0.001,
        tick_velocity_per_sec=1.0,
        current_spread_usd=0.30,
        is_macro_news_active=False,
        recommended_execution_type=RecommendedExecutionType.FREEZE_ALL,
        reason=RegimeReason.SPREAD_SCHMITT,
    )


def _make_tick() -> TickData:
    return TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.2)


class _EngineLike:
    """Minimal stand-in binding the REAL guard method (BUG-185 pattern)."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(algo=SimpleNamespace(regime_state_max_age_sec=300.0))
        self._regime_last_state = None
        self._regime_last_ts = None
        self._regime_stale_warn_at = 0.0

    _assert_regime_state_freshness = LiveEngine._assert_regime_state_freshness


def _render(warn_args: tuple) -> str:
    """Render a structlog-style %s call into its final message string."""
    if len(warn_args) > 1:
        return warn_args[0] % warn_args[1:]
    return warn_args[0]


def test_stale_reuse_beyond_max_age_emits_structured_warning(monkeypatch):
    """Frozen stream beyond max-age -> structured WARNING fires (alarm)."""
    e = _EngineLike()
    monkeypatch.setattr(le.time, "time", lambda: _BASE_EPOCH)
    e._regime_last_state = _make_state()
    # Last successful classify_tick() was 301s ago (> 300s max age).
    e._regime_state_classified_at = _BASE_EPOCH - 301.0

    warns: list[tuple] = []
    monkeypatch.setattr(le.logger, "warning", lambda *a, **k: warns.append(a))

    e._assert_regime_state_freshness(tick=_make_tick())

    assert len(warns) == 1, "stale reused state must produce exactly one WARNING"
    rendered = _render(warns[0])
    assert "STALE_STATE_REUSED" in rendered
    assert "ALARM_ONLY" in rendered
    assert "max_age_sec=300.0" in rendered
    assert "state_age=301.0s" in rendered
    # Regime identity remains audit-visible in the alarm.
    assert "HIGH_SPREAD_CHOP" in rendered


def test_frozen_stream_warning_is_rate_limited(monkeypatch):
    """A permanently frozen stream must not flood the warning log."""
    e = _EngineLike()
    clock = {"now": _BASE_EPOCH}
    monkeypatch.setattr(le.time, "time", lambda: clock["now"])
    e._regime_last_state = _make_state()
    e._regime_state_classified_at = clock["now"] - 301.0

    warns: list[tuple] = []
    monkeypatch.setattr(le.logger, "warning", lambda *a, **k: warns.append(a))
    tick = _make_tick()

    e._assert_regime_state_freshness(tick=tick)
    clock["now"] += 60.0
    e._assert_regime_state_freshness(tick=tick)  # inside the same window
    assert len(warns) == 1, "duplicate alarms inside one window are suppressed"

    clock["now"] += 300.0  # new rate-limit window, still stale
    e._assert_regime_state_freshness(tick=tick)
    assert len(warns) == 2, "a fresh window must re-alarm (state still stale)"


def test_young_reused_state_is_silent(monkeypatch):
    """Fresh stream / young duplicates: ZERO behavioral change, no alarm."""
    e = _EngineLike()
    monkeypatch.setattr(le.time, "time", lambda: _BASE_EPOCH)
    e._regime_last_state = _make_state()
    e._regime_state_classified_at = _BASE_EPOCH - 10.0

    warns: list[tuple] = []
    errors: list[tuple] = []
    monkeypatch.setattr(le.logger, "warning", lambda *a, **k: warns.append(a))
    monkeypatch.setattr(le.logger, "error", lambda *a, **k: errors.append(a))

    e._assert_regime_state_freshness(tick=_make_tick())

    assert warns == [] and errors == []


def test_guard_never_reclassifies_duplicate_data(monkeypatch):
    """BUG-169 dedup contract: the guard alarms but NEVER feeds the
    duplicate tick into classify_tick(), and never silently swaps the
    cached state identity."""
    e = _EngineLike()
    monkeypatch.setattr(le.time, "time", lambda: _BASE_EPOCH)
    state = _make_state()
    e._regime_last_state = state
    e._regime_state_classified_at = _BASE_EPOCH - 9999.0

    classify_calls: list = []

    class _SpyClassifier:
        def classify_tick(self, **kwargs):
            classify_calls.append(kwargs)
            return state

    e.regime_classifier = _SpyClassifier()
    warns: list[tuple] = []
    monkeypatch.setattr(le.logger, "warning", lambda *a, **k: warns.append(a))

    e._assert_regime_state_freshness(tick=_make_tick())

    assert classify_calls == [], "guard must not reclassify on duplicate data"
    assert warns, "guard must alarm instead"
    assert e._regime_last_state is state, "state identity unchanged (no silent refresh)"


def test_missing_classification_stamp_is_treated_as_stale(monkeypatch):
    """If freshness cannot be PROVEN (no classification stamp), the guard
    alarms honestly instead of assuming the state is fresh."""
    e = _EngineLike()
    monkeypatch.setattr(le.time, "time", lambda: _BASE_EPOCH)
    e._regime_last_state = _make_state()
    # No _regime_state_classified_at attribute at all.

    warns: list[tuple] = []
    monkeypatch.setattr(le.logger, "warning", lambda *a, **k: warns.append(a))

    e._assert_regime_state_freshness(tick=_make_tick())

    assert len(warns) == 1
    rendered = _render(warns[0])
    assert "STALE_STATE_REUSED" in rendered
    assert "state_age=unknown" in rendered


def test_regime_state_max_age_sec_default_and_yaml_keys():
    """Config contract: AlgoConfig default 300s; base.yaml + live.yaml
    carry the same key (config pattern followed)."""
    from nexus_scalp.configuration.config import AlgoConfig

    assert AlgoConfig().regime_state_max_age_sec == 300.0

    for name in ("base.yaml", "live.yaml"):
        data = yaml.safe_load((_REPO_ROOT / "configs" / name).read_text(encoding="utf-8"))
        assert float(data["algo"]["regime_state_max_age_sec"]) == 300.0, name
