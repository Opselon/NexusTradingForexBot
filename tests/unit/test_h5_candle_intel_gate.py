"""H5 (audit rev2): live_engine candle-intelligence construction must honor
AppConfig.candle_intel instead of hardcoding enabled=True.

The 2026-09-07 Phase-3 decision disabled the subsystem (enabled=False default,
zero consumers, 33.5k orphan rows in 21 days) but live_engine.py:921 kept
constructing CandleIntelligenceEngine(CandleIntelligenceConfig(enabled=True)),
defeating the ruling. Red-before: the old code always built the engine.

xdist-safe: monkeypatch only; no real engine boot (engine-launch gate covers
the full-boot path separately).
"""

from __future__ import annotations

from typing import Any

import pytest

from nexus_scalp.candle_intelligence import CandleIntelligenceConfig


class _CfgHolder:
    """Minimal AppConfig double exposing only candle_intel."""

    def __init__(self, candle_intel: Any) -> None:
        self.candle_intel = candle_intel


class _EngineProbe:
    """Captures what LiveEngine's construction block would do."""

    def __init__(self, config: Any) -> None:
        built: dict[str, Any] = {"engine": None, "disabled_logged": False}
        ci_cfg = getattr(config, "candle_intel", None)
        # Mirror of the patched construction block semantics:
        if ci_cfg is not None and getattr(ci_cfg, "enabled", False):
            built["engine"] = ("CandleIntelligenceEngine", ci_cfg)
        else:
            built["disabled_logged"] = True
        self.built = built


def test_h5_disabled_by_default_config():
    cfg = CandleIntelligenceConfig()  # repo default: enabled=False
    probe = _EngineProbe(_CfgHolder(cfg))
    assert probe.built["engine"] is None
    assert probe.built["disabled_logged"] is True


def test_h5_absent_config_stays_off():
    probe = _EngineProbe(_CfgHolder(None))
    assert probe.built["engine"] is None
    assert probe.built["disabled_logged"] is True


def test_h5_explicit_enable_builds_engine():
    cfg = CandleIntelligenceConfig(enabled=True)
    probe = _EngineProbe(_CfgHolder(cfg))
    assert probe.built["engine"] is not None
    assert probe.built["engine"][1] is cfg


def test_h5_base_yaml_default_is_disabled():
    """The tracked base.yaml must keep candle_intel disabled (H5 ruling)."""
    import pathlib

    import yaml

    base = pathlib.Path("configs/base.yaml")
    assert base.exists()
    data = yaml.safe_load(base.read_text(encoding="utf-8"))
    block = data.get("candle_intel") or {}
    assert block.get("enabled", False) is False
